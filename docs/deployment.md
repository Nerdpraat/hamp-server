# Deployment

## The config directory

The server reads four files from one directory (`ENERGY_HUB_CONFIG_DIR`, `/config` in the container):
`config.yaml`, `auth.yaml`, `topology.yaml` and `devices.yaml`. Start from `server/config.example/`.

* **`auth.yaml` is written by the server.** Every empty or missing secret (the dashboard key, the
  session secret and the key of each site in `topology.yaml`) is generated when the server starts and
  stored in this file. Comments and the order of entries are kept, the write is atomic, and the values
  are never logged (only the names of the entries that were generated). Therefore **the config
  directory must be writable** by the user the server runs as. If it is not, the server stops with a
  message saying so. If you fill in every secret yourself, nothing is written.
* Supplied values must be long enough: site keys and the session secret at least 16 characters, the
  dashboard key at least 8. Two sites cannot share a key.
* Keep `auth.yaml` private (`chmod 600`). The server warns at startup when it is readable by others.
* **Rotate a key** by emptying its value in `auth.yaml` and restarting: a new one is generated. Give it
  to the Home Assistant integration of that site (Settings, Devices & services, Energy Hub, it will ask
  for the new key when the old one is refused).
* **Back up** `auth.yaml` together with the database: without it every site has to be given a new key.

## Docker

```
mkdir -p config data
cp server/config.example/* config/          # then edit topology.yaml and devices.yaml
HUB_USER=$(id -u):$(id -g) docker compose up -d --build
cat config/auth.yaml                         # the generated dashboard key and the site keys
```

* `HUB_USER` runs the container as your own user, so that it can write `config/auth.yaml` and
  `data/`. Without it the container runs as uid 10001: then `chown -R 10001 config data` first.
  Create `config` and `data` yourself before the first start; Docker would create them as root.
* The compose file mounts `./config` and `./data` as directories with the `:z` label. The label is
  needed on SELinux hosts (Fedora, RHEL) and harmless elsewhere. Mount **directories**: Docker cannot
  replace a single bind-mounted file, which the server needs for its atomic write of `auth.yaml`
  (it falls back to writing in place, but a directory is the safe choice).
* The container logs JSON lines (`LOG_FORMAT=json`; use `text` for plain logs). `docker stop`
  sends SIGTERM and the server flushes the buffered minute buckets before it exits, so a normal
  restart loses nothing. `HUB_PORT` changes the published port (default 8000).

## Without Docker

```
cd server
python -m venv .venv && .venv/bin/pip install -r requirements.txt
ENERGY_HUB_CONFIG_DIR=../config .venv/bin/uvicorn app.main:create_app_from_env --factory \
    --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
```

Environment variables: `ENERGY_HUB_CONFIG_DIR` (default `./config`), `ENERGY_HUB_DB_PATH` (overrides
`db_path`), `LOG_LEVEL`, `LOG_FORMAT`.

## Reverse proxy with TLS

The server speaks plain HTTP and WebSocket and expects a proxy to terminate TLS. The proxy must
forward WebSocket upgrades and keep the `Host` header (the browser WebSocket checks that its
`Origin` matches it).

nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name energy.example.com;
    # ssl_certificate ...; ssl_certificate_key ...;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;   # idle websockets must not be cut after 60 s
        proxy_send_timeout 3600s;
    }
}
```

Caddy: `energy.example.com { reverse_proxy 127.0.0.1:8000 }` (WebSockets work out of the box).

`--proxy-headers` makes the server use `X-Forwarded-For` for the client address, which the login
rate limit relies on. Only trust it from your own proxy: publish the container port on
`127.0.0.1` (as the compose file does) or set `FORWARDED_ALLOW_IPS` to the proxy's address.

The Home Assistant integration takes the public URL (`https://energy.example.com`) and derives
`wss://energy.example.com/ws/ingest` itself.

## Sizing

Measured with `tests/e2e` (3 sites, 15 sensors each, a real server process):

* 10 batches per second per site (10 times real life): about 2% of one CPU core, so about
  0.2% at the real rate of 1 per second.
* Storage is one row per sensor per minute: 3 sites x 15 sensors x 1440 minutes x 7 days is about
  450,000 rows, roughly 35 MB with the default 7-day retention.

Run `python tools/load_test.py --help` to test your own deployment.

## Backups

The database is a single SQLite file in WAL mode. Back it up with
`sqlite3 energy.db ".backup backup.db"` rather than by copying the file while the server runs. Include
`config/auth.yaml` and `config/devices.yaml` in the backup.

## Upgrading during development

The database has a single schema version. A database from another layout is reset when the server
starts (a warning is logged and its data is lost). The project is in development and has no
migration path.
