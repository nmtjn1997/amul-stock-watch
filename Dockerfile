FROM python:3.12-slim

# curl is the HTTP transport (its TLS handshake passes Cloudflare where urllib often does not).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY amul_watch ./amul_watch
RUN pip install --no-cache-dir . && useradd --create-home --uid 1000 amul && mkdir /data && chown amul /data

USER amul
ENV AMUL_WATCH_HOME=/data \
    AMUL_WATCH_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8847

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD curl -fsS -o /dev/null http://127.0.0.1:8847/api/state -u "x:${AMUL_WATCH_UI_PASSWORD}" || exit 1

# First start creates /data/config.yaml etc. from the examples; later starts keep them.
CMD ["sh", "-c", "amul-watch init >/dev/null && exec amul-watch serve --no-open"]
