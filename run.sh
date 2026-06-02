#!/usr/bin/env bash
#
# One-command local setup + run (no Docker).
#
#   ./run.sh
#
# Creates a virtualenv, installs dependencies, seeds .env on first run, checks
# for ffmpeg, then starts the app on http://localhost:8000.
#
set -euo pipefail

cd "$(dirname "$0")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { printf "${GREEN}==>${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!! ${NC} %s\n" "$1"; }
die()  { printf "${RED}xx ${NC} %s\n" "$1" >&2; exit 1; }

# --- Python ---------------------------------------------------------------
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || die "python3 not found. Install Python 3.10+ and retry."

# --- ffmpeg (required by yt-dlp + Whisper decoding) -----------------------
if ! command -v ffmpeg >/dev/null 2>&1; then
  warn "ffmpeg not found — it's required to download/decode audio."
  if   command -v apt-get >/dev/null 2>&1; then warn "Install with: sudo apt-get install -y ffmpeg"
  elif command -v brew    >/dev/null 2>&1; then warn "Install with: brew install ffmpeg"
  elif command -v dnf     >/dev/null 2>&1; then warn "Install with: sudo dnf install -y ffmpeg"
  fi
  warn "Continuing anyway (the web UI loads, but transcription will fail until ffmpeg is installed)."
fi

# --- virtualenv -----------------------------------------------------------
if [ ! -d .venv ]; then
  say "Creating virtualenv (.venv)…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# --- dependencies ---------------------------------------------------------
say "Installing dependencies (first run downloads them; later runs are fast)…"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# --- config ---------------------------------------------------------------
if [ ! -f .env ]; then
  say "Seeding .env from .env.example (edit it to tweak model/TTL/etc.)…"
  cp .env.example .env
fi

# --- run ------------------------------------------------------------------
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
say "Starting Instant Transcript on http://${HOST}:${PORT}  (Ctrl+C to stop)"
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
