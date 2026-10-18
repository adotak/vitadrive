FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY vitadrive ./vitadrive

RUN python -m pip wheel --wheel-dir=/wheels -r requirements.txt . \
    && python -m pip install --prefix=/install --no-cache-dir --no-index --find-links=/wheels vitadrive==1.0.0

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATABASE_URL=/data/vitadrive.db

WORKDIR /app

COPY --from=builder /install/ /usr/local/
COPY app.py /app/app.py

RUN groupadd --gid 10001 vitadrive \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin vitadrive \
    && mkdir -p /data \
    && chown 10001:10001 /data

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=3)" || exit 1

LABEL org.opencontainers.image.source="https://github.com/adotak/vitadrive" \
    org.opencontainers.image.title="VitaDrive" \
    org.opencontainers.image.licenses="MIT"

# The shell wrapper expands PORT and execs Uvicorn as PID 1.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
