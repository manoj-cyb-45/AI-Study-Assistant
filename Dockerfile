FROM python:3.12-slim AS base

# System dependencies for PyMuPDF (mupdf) build/runtime — the manylinux wheel
# covers most cases, but libstdc++ is still required at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY start.sh .

RUN mkdir -p data/repo data/logs data/cache data/backups \
    && chmod +x start.sh

# Run as a non-root user.
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health', timeout=8).status == 200 else sys.exit(1)"

CMD ["./start.sh"]
