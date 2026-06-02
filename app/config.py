"""Environment-driven configuration.

All knobs are read from the environment (optionally seeded from a ``.env`` file
via python-dotenv). Defaults are tuned for a CPU-only run so the app boots
without a GPU; override ``DEVICE``/``COMPUTE_TYPE``/``WHISPER_MODEL`` for GPU.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Resolved application settings (read once at import via get_settings)."""

    def __init__(self) -> None:
        # --- Whisper / transcription ---
        # `small` is the out-of-box default: fast on CPU, decent quality.
        # Bump to `medium` / `large-v3` (GPU recommended) for best results.
        self.whisper_model: str = os.getenv("WHISPER_MODEL", "small")
        self.device: str = os.getenv("DEVICE", "cpu")
        self.compute_type: str = os.getenv("COMPUTE_TYPE", "int8")

        # --- TTL / guardrails ---
        self.default_ttl_hours: int = int(os.getenv("DEFAULT_TTL_HOURS", "168"))
        self.max_ttl_hours: int = int(os.getenv("MAX_TTL_HOURS", "720"))  # 30 days
        self.max_duration_min: int = int(os.getenv("MAX_DURATION_MIN", "120"))

        # --- URLs / storage ---
        self.base_url: str = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
        self.db_path: Path = Path(os.getenv("DB_PATH", "data/transcripts.db"))
        self.tmp_dir: Path = Path(os.getenv("TMP_DIR", "data/tmp"))

        # --- Cleanup loop ---
        self.cleanup_interval_min: int = int(os.getenv("CLEANUP_INTERVAL_MIN", "15"))

        # --- Rate limiting (slowapi) ---
        self.rate_limit: str = os.getenv("RATE_LIMIT", "10/hour")
        self.rate_limit_enabled: bool = _get_bool("RATE_LIMIT_ENABLED", True)

        # --- yt-dlp cookie fallback (optional, for YouTube bot checks) ---
        self.yt_dlp_cookies: str | None = os.getenv("YT_DLP_COOKIES") or None
        self.yt_dlp_cookies_from_browser: str | None = (
            os.getenv("YT_DLP_COOKIES_FROM_BROWSER") or None
        )

        # Ensure data dirs exist.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def clamp_ttl_hours(self, requested: int | None) -> int:
        """Return a TTL within [1, max_ttl_hours], defaulting when unset."""
        ttl = self.default_ttl_hours if not requested else int(requested)
        return max(1, min(ttl, self.max_ttl_hours))

    def share_url(self, transcript_id: str) -> str:
        return f"{self.base_url}/t/{transcript_id}"

    def raw_url(self, transcript_id: str) -> str:
        return f"{self.base_url}/t/{transcript_id}/raw"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
