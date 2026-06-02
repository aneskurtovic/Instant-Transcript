"""FastAPI app: submit UI, JSON API, server-rendered transcript pages.

Single-process v1. Transcription runs in a background thread (FastAPI
``BackgroundTasks``); the UI polls ``/api/status/{id}``. A periodic asyncio loop
purges expired rows, and every read path returns 410 once a row is past its TTL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import store
from .config import get_settings
from .transcribe import TranscribeError, transcribe_job

logger = logging.getLogger("instant_transcript")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

settings = get_settings()

# --- Optional rate limiting (slowapi) ---------------------------------------
limiter = None
if settings.rate_limit_enabled:
    try:
        from slowapi import Limiter
        from slowapi.util import get_remote_address

        limiter = Limiter(key_func=get_remote_address)
    except Exception:  # pragma: no cover - slowapi optional
        logger.warning("slowapi not available; rate limiting disabled")
        limiter = None


_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return parsed.netloc.lower() in _YOUTUBE_HOSTS


def extract_video_id(url: str) -> str | None:
    """Best-effort video id extraction for display only (Whisper uses the URL)."""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None
    host = parsed.netloc.lower()
    if host in {"youtu.be", "www.youtu.be"}:
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid if _VIDEO_ID_RE.match(vid) else None
    # youtube.com/watch?v=...
    from urllib.parse import parse_qs

    qs = parse_qs(parsed.query)
    if "v" in qs and qs["v"]:
        vid = qs["v"][0]
        return vid if _VIDEO_ID_RE.match(vid) else None
    # /shorts/<id>, /embed/<id>, /live/<id>
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
        return parts[1] if _VIDEO_ID_RE.match(parts[1]) else None
    return None


def _fmt_dt(epoch: int | None) -> str:
    if not epoch:
        return "—"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


# --- Background worker -------------------------------------------------------

def _run_transcription(transcript_id: str, url: str, language: str | None) -> None:
    """Executed in a worker thread by BackgroundTasks. Updates the DB row."""
    store.update(transcript_id, status="processing")
    try:
        result = transcribe_job(
            url=url,
            tmp_dir=settings.tmp_dir,
            transcript_id=transcript_id,
            language=language,
        )
        store.update(
            transcript_id,
            status="done",
            transcript=result.text,
            segments_json=json.dumps(result.segments, ensure_ascii=False),
            language=result.language,
            model=result.model,
        )
        logger.info("Transcription done: %s", transcript_id)
    except TranscribeError as exc:
        store.update(transcript_id, status="error", error=str(exc))
        logger.warning("Transcription failed (%s): %s", transcript_id, exc)
    except Exception as exc:  # pragma: no cover - defensive
        store.update(
            transcript_id,
            status="error",
            error="Unexpected error during transcription.",
        )
        logger.exception("Unexpected transcription error (%s): %s", transcript_id, exc)


# --- Cleanup loop ------------------------------------------------------------

async def _cleanup_loop() -> None:
    interval = max(1, settings.cleanup_interval_min) * 60
    while True:
        try:
            removed = store.delete_expired()
            if removed:
                logger.info("Cleanup removed %d expired transcript(s)", removed)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Cleanup loop error: %s", exc)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Instant Transcript", lifespan=lifespan)

if limiter is not None:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# --- Routes ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "default_ttl_hours": settings.default_ttl_hours,
            "max_duration_min": settings.max_duration_min,
            "whisper_model": settings.whisper_model,
        },
    )


async def _transcribe_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str,
    ttl_hours: int | None,
    language: str | None,
):
    url = (url or "").strip()
    if not url or not is_youtube_url(url):
        return JSONResponse(
            {"error": "Please provide a valid YouTube URL."}, status_code=400
        )

    lang = (language or "").strip() or None
    ttl = settings.clamp_ttl_hours(ttl_hours)
    expires_at = store.now() + ttl * 3600

    transcript_id = secrets.token_urlsafe(9)
    store.create(
        transcript_id=transcript_id,
        youtube_url=url,
        video_id=extract_video_id(url),
        title=None,
        expires_at=expires_at,
        status="queued",
        model=settings.whisper_model,
    )

    background_tasks.add_task(_run_transcription, transcript_id, url, lang)

    return JSONResponse(
        {
            "id": transcript_id,
            "share_url": settings.share_url(transcript_id),
            "raw_url": settings.raw_url(transcript_id),
            "status": "queued",
        }
    )


# Accept both JSON and form bodies. The UI posts JSON.
async def api_transcribe(request: Request, background_tasks: BackgroundTasks):
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
        url = body.get("url", "")
        ttl_hours = body.get("ttl_hours")
        language = body.get("language")
    else:
        form = await request.form()
        url = form.get("url", "")
        ttl_hours = form.get("ttl_hours")
        language = form.get("language")
        if ttl_hours is not None:
            try:
                ttl_hours = int(ttl_hours)
            except (TypeError, ValueError):
                ttl_hours = None

    return await _transcribe_handler(
        request, background_tasks, url, ttl_hours, language
    )


# Optionally wrap with the rate limiter, then register the route. slowapi's
# decorator reads ``request`` from the endpoint signature, which we provide.
if limiter is not None:
    api_transcribe = limiter.limit(settings.rate_limit)(api_transcribe)

app.add_api_route("/api/transcribe", api_transcribe, methods=["POST"])


@app.get("/api/status/{transcript_id}")
async def api_status(transcript_id: str):
    row = store.get(transcript_id)
    if row is None:
        return JSONResponse({"error": "Not found."}, status_code=404)
    if store.is_expired(row):
        return JSONResponse({"error": "This link has expired."}, status_code=410)

    return JSONResponse(
        {
            "status": row["status"],
            "title": row["title"],
            "language": row["language"],
            "error": row["error"],
            "share_url": settings.share_url(transcript_id),
            "raw_url": settings.raw_url(transcript_id),
        }
    )


@app.get("/t/{transcript_id}", response_class=HTMLResponse)
async def transcript_page(request: Request, transcript_id: str):
    row = store.get(transcript_id)
    if row is None:
        return templates.TemplateResponse(
            request,
            "transcript.html",
            {"state": "missing", "id": transcript_id},
            status_code=404,
        )
    if store.is_expired(row):
        return templates.TemplateResponse(
            request,
            "transcript.html",
            {"state": "expired", "id": transcript_id},
            status_code=410,
        )

    segments = []
    if row["segments_json"]:
        try:
            segments = json.loads(row["segments_json"])
        except (ValueError, TypeError):
            segments = []

    return templates.TemplateResponse(
        request,
        "transcript.html",
        {
            "state": row["status"],  # queued | processing | done | error
            "id": transcript_id,
            "row": row,
            "segments": segments,
            "raw_url": settings.raw_url(transcript_id),
            "share_url": settings.share_url(transcript_id),
            "expires_human": _fmt_dt(row["expires_at"]),
            "created_human": _fmt_dt(row["created_at"]),
        },
    )


@app.get("/t/{transcript_id}/raw")
async def transcript_raw(transcript_id: str):
    row = store.get(transcript_id)
    if row is None:
        return PlainTextResponse("Not found.", status_code=404)
    if store.is_expired(row):
        return PlainTextResponse("This link has expired (410 Gone).", status_code=410)

    status = row["status"]
    if status in {"queued", "processing"}:
        return PlainTextResponse(
            "Transcript is still being generated. Try again shortly.",
            status_code=202,
        )
    if status == "error":
        return PlainTextResponse(
            f"Transcription failed: {row['error'] or 'unknown error'}",
            status_code=422,
        )

    # status == done — build the AI-context header block + transcript body.
    header_lines = [
        f"Title: {row['title'] or 'Unknown'}",
        f"Source URL: {row['youtube_url']}",
        f"Language: {row['language'] or 'unknown'}",
        f"Transcribed with: Whisper {row['model'] or settings.whisper_model} (local)",
        f"Generated: {_fmt_dt(row['created_at'])}",
        f"Expires: {_fmt_dt(row['expires_at'])}",
    ]
    body = "\n".join(header_lines) + "\n" + ("-" * 60) + "\n\n"
    body += (row["transcript"] or "").strip() + "\n"

    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")
