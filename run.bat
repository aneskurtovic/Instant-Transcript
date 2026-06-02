@echo off
REM One-command local setup + run on Windows.  Usage:  run.bat
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] Python not found. Install Python 3.10+ from python.org and retry.
  exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [!] ffmpeg not found - required for audio download/decoding.
  echo     Install from https://ffmpeg.org/download.html or: winget install Gyan.FFmpeg
)

if not exist .venv (
  echo ==^> Creating virtualenv (.venv)...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo ==^> Installing dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

if not exist .env (
  echo ==^> Seeding .env from .env.example...
  copy /Y .env.example .env >nul
)

echo ==^> Starting Instant Transcript on http://127.0.0.1:8000  (Ctrl+C to stop)
uvicorn app.main:app --host 127.0.0.1 --port 8000

endlocal
