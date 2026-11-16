# ── ViralClipper — Production Dockerfile ──────────────────────────────────────
FROM python:3.11-slim

# Install system deps: FFmpeg + OpenCV headless requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy app source
COPY . .

# Create output dir (Render uses /tmp for ephemeral storage)
RUN mkdir -p /tmp/viralclipper_output

# Expose port
EXPOSE 10000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:10000/ || exit 1

# Start with gunicorn (production WSGI server)
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:10000", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "600", \
     "--worker-class", "gthread", \
     "--max-requests", "50", \
     "--max-requests-jitter", "10", \
     "--preload"]
