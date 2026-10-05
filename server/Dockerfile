# PDF Axe account server
#
# Build from the REPOSITORY ROOT (the image needs the `server` package path):
#   docker build -f server/Dockerfile -t pdfaxe-server .
# Run:
#   docker run -d --name pdfaxe -p 8000:8000 -v pdfaxe-data:/data \
#     --env-file server/.env pdfaxe-server
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SFM_DB_PATH=/data/pdfaxe.sqlite3 \
    SFM_STORAGE_DIR=/data/storage

WORKDIR /app

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install -r /app/server/requirements.txt

# Only the server package's code: no tests, no local .env, no local data.
COPY server/__init__.py server/app.py server/billing.py server/config.py server/db.py /app/server/

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin pdfaxe \
 && mkdir -p /data/storage \
 && chown -R pdfaxe:pdfaxe /data

# Database + uploaded files. Mount a persistent volume here.
VOLUME ["/data"]

USER pdfaxe
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# One worker on purpose: live /events streams are held in this process's memory.
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
