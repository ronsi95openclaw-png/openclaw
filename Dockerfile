FROM python:3.11-slim

WORKDIR /app

# System deps (for cryptography, numpy, scipy wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source (excludes .env, credentials.json via .dockerignore)
COPY . .

# Data directory (persisted via volume mount in production)
RUN mkdir -p data/logs data/optimization

# Non-root user
RUN useradd -r -u 1001 -g root openclaw && chown -R openclaw:root /app
USER openclaw

EXPOSE 8000
EXPOSE 9090

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check: dashboard API liveness
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/api/health')" || exit 1

CMD ["python", "main.py"]
