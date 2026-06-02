"""Audio download (yt-dlp) + local transcription (faster-whisper).

Hard rule: we never use YouTube's caption track or any cloud ASR. We always
download the raw audio stream and run Whisper locally.

Heavy third-party imports (``yt_dlp``, ``faster_whisper``) are done lazily
*inside* the functions that need them, so this module — and the FastAPI app —
can be imported on a machine that doesn't have them installed (e.g. for wiring
up and inspecting the routes). The model is loaded once and cached.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import get_settings


class TranscribeError(Exception):
    """Base class for user-facing transcription failures."""


class DurationError(TranscribeError):
    """Raised when a video exceeds MAX_DURATION_MIN."""


class DownloadError(TranscribeError):
    """Raised when yt-dlp fails to fetch info or audio."""


@dataclass
class VideoInfo:
    video_id: str | None
    title: str | None
    duration_sec: int | None


@dataclass
class TranscriptResult:
    text: str
    language: str | None
    model: str
    segments: list[dict[str, Any]] = field(default_factory=list)


# --- Whisper model singleton -------------------------------------------------

_model_lock = threading.Lock()
_model: Any = None
_model_key: tuple[str, str, str] | None = None


def _get_model() -> Any:
    """Load (and cache) the WhisperModel for the current settings."""
    global _model, _model_key
    settings = get_settings()
    key = (settings.whisper_model, settings.device, settings.compute_type)
    with _model_lock:
        if _model is None or _model_key != key:
            from faster_whisper import WhisperModel  # lazy heavy import

            _model = WhisperModel(
                settings.whisper_model,
                device=settings.device,
                compute_type=settings.compute_type,
            )
            _model_key = key
        return _model


# --- yt-dlp helpers ----------------------------------------------------------

def _ydl_opts(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    # Optional cookie fallback for YouTube bot checks.
    if settings.yt_dlp_cookies:
        opts["cookiefile"] = settings.yt_dlp_cookies
    if settings.yt_dlp_cookies_from_browser:
        # yt-dlp expects a tuple like ("chrome",) / ("firefox",)
        opts["cookiesfrombrowser"] = (settings.yt_dlp_cookies_from_browser,)
    if extra:
        opts.update(extra)
    return opts


def fetch_info(url: str) -> VideoInfo:
    """Pull metadata (title, duration, id) without downloading the media."""
    from yt_dlp import YoutubeDL  # lazy heavy import
    from yt_dlp.utils import DownloadError as YTDownloadError

    try:
        with YoutubeDL(_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except YTDownloadError as exc:  # pragma: no cover - network dependent
        raise DownloadError(f"Could not read video info: {exc}") from exc

    duration = info.get("duration")
    return VideoInfo(
        video_id=info.get("id"),
        title=info.get("title"),
        duration_sec=int(duration) if duration else None,
    )


def enforce_duration(info: VideoInfo) -> None:
    settings = get_settings()
    if info.duration_sec is None:
        return
    max_sec = settings.max_duration_min * 60
    if info.duration_sec > max_sec:
        mins = info.duration_sec // 60
        raise DurationError(
            f"Video is {mins} min long; the limit is "
            f"{settings.max_duration_min} min. Try a shorter clip."
        )


def download_audio(url: str, tmp_dir: Path, transcript_id: str) -> Path:
    """Download the best audio stream to ``tmp_dir`` and return the file path."""
    from yt_dlp import YoutubeDL  # lazy heavy import
    from yt_dlp.utils import DownloadError as YTDownloadError

    outtmpl = str(tmp_dir / f"{transcript_id}.%(ext)s")
    opts = _ydl_opts({"outtmpl": outtmpl})
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except YTDownloadError as exc:  # pragma: no cover - network dependent
        raise DownloadError(f"Audio download failed: {exc}") from exc

    if not path.exists():
        # yt-dlp may have remuxed to a different extension; find by stem.
        matches = list(tmp_dir.glob(f"{transcript_id}.*"))
        if not matches:
            raise DownloadError("Audio download produced no file.")
        path = matches[0]
    return path


def run_whisper(audio_path: Path, language: str | None) -> TranscriptResult:
    """Transcribe ``audio_path`` locally with faster-whisper."""
    settings = get_settings()
    model = _get_model()
    segments_iter, info = model.transcribe(str(audio_path), language=language)

    segments: list[dict[str, Any]] = []
    parts: list[str] = []
    for seg in segments_iter:
        text = seg.text.strip()
        segments.append(
            {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text}
        )
        parts.append(text)

    return TranscriptResult(
        text=" ".join(p for p in parts if p),
        language=getattr(info, "language", None) or language,
        model=settings.whisper_model,
        segments=segments,
    )


def transcribe_job(
    url: str, tmp_dir: Path, transcript_id: str, language: str | None
) -> TranscriptResult:
    """Full pipeline: validate duration → download audio → Whisper → cleanup.

    The temp audio file is always removed in the ``finally`` block.
    """
    info = fetch_info(url)
    enforce_duration(info)

    audio_path: Path | None = None
    try:
        audio_path = download_audio(url, tmp_dir, transcript_id)
        return run_whisper(audio_path, language)
    finally:
        if audio_path is not None:
            with contextlib.suppress(OSError):
                audio_path.unlink()
        # Sweep any sibling fragments yt-dlp may have left behind.
        for leftover in tmp_dir.glob(f"{transcript_id}.*"):
            with contextlib.suppress(OSError):
                leftover.unlink()
