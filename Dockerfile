FROM python:3.12-slim

WORKDIR /app

# Praat / parselmouth needs these on Debian slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY stories ./stories
COPY sounds ./sounds
COPY data ./data
COPY run.py .

# Runtime dirs (samples are gitignored; create empty for the lab tools).
RUN mkdir -p data/recordings data/tts_cache samples

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
