# Office Axe portal. Build from the project root:
#   docker build -f webportal/deploy/Dockerfile -t officeaxe-portal .
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir waitress==3.0.0
COPY license_keys.py ed25519_pure.py revoked_keys.json /app/
COPY webportal/*.py /app/webportal/
COPY webportal/static /app/webportal/static
COPY promotion/office-axe-demo.mp4 /app/promotion/office-axe-demo.mp4
ENV OFFICEAXE_PORTAL_DATA=/data \
    OFFICEAXE_COOKIE_SECURE=1 \
    OFFICEAXE_TRUST_PROXY=1 \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 portal && mkdir -p /data && chown portal /data
USER portal
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "webportal/manage.py", "run", "--host", "0.0.0.0", "--port", "8000"]
