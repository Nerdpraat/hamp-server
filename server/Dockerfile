FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

RUN useradd --system --uid 10001 hub && mkdir /data /config && chown hub /data /config
USER hub

# /config holds config.yaml, auth.yaml, topology.yaml and devices.yaml. The server writes generated
# secrets into auth.yaml, so /config must be writable for the user the container runs as.
ENV ENERGY_HUB_CONFIG_DIR=/config \
    ENERGY_HUB_DB_PATH=/data/energy.db \
    LOG_FORMAT=json
VOLUME ["/config", "/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

# Trust X-Forwarded-* only from the proxy: set FORWARDED_ALLOW_IPS to its address.
CMD ["sh", "-c", "exec uvicorn app.main:create_app_from_env --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1}"]
