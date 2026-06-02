"""SQLite persistence for transcripts.

A single SQLite file holds every transcript row. The DB is accessed from both
the request thread and the background worker thread, so we keep one shared
connection (``check_same_thread=False``) guarded by a lock. Volumes are tiny and
writes are infrequent, so a global lock is more than fast enough.

Expiry here is purely data-level: ``delete_expired`` purges old rows. The HTTP
410-on-expiry behaviour lives in the route layer (``main.py``) so a row can be
reported as "expired" right up until the cleanup loop removes it.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from .config import get_settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id            TEXT PRIMARY KEY,
    youtube_url   TEXT NOT NULL,
    video_id      TEXT,
    title         TEXT,
    status        TEXT NOT NULL,          -- queued | processing | done | error | expired
    language      TEXT,
    model         TEXT,
    transcript    TEXT,
    segments_json TEXT,
    error         TEXT,
    created_at    INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transcripts_expires_at ON transcripts(expires_at);
"""

# Columns that callers are allowed to update via ``update``.
_UPDATABLE = {
    "video_id",
    "title",
    "status",
    "language",
    "model",
    "transcript",
    "segments_json",
    "error",
    "expires_at",
}


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        settings = get_settings()
        _conn = sqlite3.connect(
            str(settings.db_path), check_same_thread=False
        )
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
    return _conn


def init_db() -> None:
    """Create the schema if it does not exist. Safe to call repeatedly."""
    with _lock:
        conn = _connect()
        conn.executescript(SCHEMA)
        conn.commit()


def now() -> int:
    return int(time.time())


def create(
    *,
    transcript_id: str,
    youtube_url: str,
    video_id: str | None,
    title: str | None,
    expires_at: int,
    status: str = "queued",
    model: str | None = None,
) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO transcripts
                (id, youtube_url, video_id, title, status, model, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transcript_id,
                youtube_url,
                video_id,
                title,
                status,
                model,
                now(),
                expires_at,
            ),
        )
        conn.commit()


def get(transcript_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT * FROM transcripts WHERE id = ?", (transcript_id,)
        ).fetchone()
    return dict(row) if row else None


def update(transcript_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = [c for c in fields if c in _UPDATABLE]
    if not cols:
        raise ValueError(f"No updatable columns in {list(fields)}")
    assignments = ", ".join(f"{c} = ?" for c in cols)
    values = [fields[c] for c in cols]
    values.append(transcript_id)
    with _lock:
        conn = _connect()
        conn.execute(
            f"UPDATE transcripts SET {assignments} WHERE id = ?", values
        )
        conn.commit()


def delete(transcript_id: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM transcripts WHERE id = ?", (transcript_id,))
        conn.commit()


def delete_expired() -> int:
    """Delete rows whose ``expires_at`` is in the past. Returns count removed."""
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM transcripts WHERE expires_at < ?", (now(),)
        )
        conn.commit()
        return cur.rowcount


def is_expired(row: dict[str, Any]) -> bool:
    return int(row["expires_at"]) < now()
