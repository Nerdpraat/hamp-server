"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, ingest, live
from .aggregator import Aggregator
from .auth import LoginLimiter
from .config import Config, ConfigError, load_config
from .db import Database
from .retention import retention_loop

log = logging.getLogger("energy_hub")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log shippers (set LOG_FORMAT=json)."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self' ws: wss:; frame-ancestors 'none'"),
}


def create_app(config: Config, retention_interval: float = 3600) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(config.db_path)
        await db.connect()
        for s in config.sites:
            await db.upsert_site(s.id, s.name)
        agg = Aggregator(db)
        await agg.seed()
        app.state.db = db
        app.state.aggregator = agg
        tasks = [
            asyncio.create_task(agg.run(config.flush_interval), name="aggregator"),
            asyncio.create_task(
                retention_loop(db, config.retention_days, retention_interval), name="retention"),
        ]
        log.info("started with %d site(s), retention %s days", len(config.sites),
                 config.retention_days)
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            await agg.stop()  # final flush
            await db.close()

    app = FastAPI(title="Energy Hub", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.config = config
    app.state.hub = live.LiveHub()
    app.state.ingest_conns = {}
    app.state.login_limiter = LoginLimiter()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            # Revalidate static files on every load (cheap 304 via ETag): a new server version
            # must not run against a cached older script or stylesheet.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.get("/healthz")
    async def healthz():
        return JSONResponse({"status": "ok"})

    app.include_router(api.router)
    app.include_router(ingest.router)
    app.include_router(live.router)
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def create_app_from_env() -> FastAPI:
    """uvicorn --factory entry point.

    Reads the four config files from ENERGY_HUB_CONFIG_DIR (default ./config). Missing secrets in
    auth.yaml are generated and written back, so that directory must be writable.
    """
    configure_logging()
    config_dir = os.environ.get("ENERGY_HUB_CONFIG_DIR", "config")
    try:
        cfg = load_config(config_dir)
    except ConfigError as err:
        print(f"configuration error: {err}", file=sys.stderr)
        raise SystemExit(2) from None
    if os.environ.get("ENERGY_HUB_DB_PATH"):  # lets containers keep the database on a volume
        cfg.db_path = os.environ["ENERGY_HUB_DB_PATH"]
    return create_app(cfg)
