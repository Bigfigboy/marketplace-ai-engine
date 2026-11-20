FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY demo ./demo
COPY alembic.ini ./

# Unprivileged user; data directory (DB + uploads) is a volume, never served over HTTP.
RUN useradd --create-home --uid 10001 engine \
    && mkdir -p /app/data/uploads \
    && chown -R engine:engine /app/data \
    && chmod 700 /app/data/uploads
USER engine

ENV DATABASE_URL=sqlite:////app/data/ai_engine.db \
    STORAGE_DIR=/app/data/uploads

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# Behind a trusted reverse proxy add: "--proxy-headers", "--forwarded-allow-ips", "<proxy ip>"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
