# Instant Transcript — single-image deploy (CPU by default).
# For GPU, base off an nvidia/cuda image and set DEVICE=cuda, COMPUTE_TYPE=float16.
FROM python:3.12-slim

# ffmpeg is required by yt-dlp and by Whisper's audio decoding.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY app ./app

# Persist sqlite db, temp audio, and (optionally) the model cache via a volume.
ENV DB_PATH=/data/transcripts.db \
    TMP_DIR=/data/tmp \
    HF_HOME=/data/hf-cache
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
