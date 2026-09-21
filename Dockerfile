FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOWNLOAD_DIR=/tmp/vdlbot

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

RUN useradd -m -u 10001 botuser && mkdir -p /tmp/vdlbot && chown -R botuser /tmp/vdlbot
USER botuser

CMD ["python", "-m", "bot.main"]
