"""Last-value cache and the /ws/live endpoint for browsers."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket

from .api import build_overview
from .auth import COOKIE_NAME, session_valid
from .ingest import SOCKET_GONE

log = logging.getLogger("energy_hub.live")
router = APIRouter()

PUSH_INTERVAL = 1.0


class LiveHub:
    """Keeps the most recent value per entity with a change counter."""

    def __init__(self) -> None:
        self._version = 0
        self._vals: dict[str, dict[str, tuple[float, float, int]]] = {}

    def update(self, site: str, key: str, ts: float, value: float) -> None:
        self._version += 1
        self._vals.setdefault(site, {})[key] = (ts, value, self._version)

    def forget(self, site: str, keys: list[str]) -> None:
        for k in keys:
            self._vals.get(site, {}).pop(k, None)

    @property
    def version(self) -> int:
        return self._version

    def changes(self, site: str, since: int = 0) -> dict[str, list[float]]:
        return {k: [ts, v] for k, (ts, v, ver) in self._vals.get(site, {}).items() if ver > since}


def _same_origin(ws: WebSocket) -> bool:
    """Browsers always send Origin on WebSocket handshakes; reject other sites' pages."""
    origin = ws.headers.get("origin")
    if origin is None:
        return True  # non-browser client, still needs the session cookie
    host = ws.headers.get("x-forwarded-host") or ws.headers.get("host", "")
    return urlsplit(origin).netloc == host


async def _drain(ws: WebSocket) -> None:
    """Consume client messages so that a disconnect is noticed."""
    with contextlib.suppress(*SOCKET_GONE, RuntimeError):
        while True:
            await ws.receive_text()


@router.websocket("/ws/live")
async def live(ws: WebSocket, site: str):
    st = ws.app.state
    if not session_valid(st.config, ws.cookies.get(COOKIE_NAME)) or st.config.site(site) is None:
        await ws.close(code=4401)
        return
    if not _same_origin(ws):
        await ws.close(code=4403)
        return
    await ws.accept()
    hub: LiveHub = st.hub
    since = 0
    first = True
    reader = asyncio.create_task(_drain(ws))
    try:
        while not reader.done():
            version = hub.version
            changes = hub.changes(site, since)
            since = version
            if changes or first:
                first = False
                await ws.send_json({"type": "values", "values": changes})
            await asyncio.wait({reader}, timeout=PUSH_INTERVAL)
    except (*SOCKET_GONE, RuntimeError):
        pass
    finally:
        reader.cancel()


OVERVIEW_REFRESH = 5.0   # recompute at least this often, so stale values and offline sites show up


@router.websocket("/ws/overview")
async def overview_ws(ws: WebSocket):
    """Pushes the all-sites overview whenever it changes (checked once per PUSH_INTERVAL)."""
    st = ws.app.state
    if not session_valid(st.config, ws.cookies.get(COOKIE_NAME)):
        await ws.close(code=4401)
        return
    if not _same_origin(ws):
        await ws.close(code=4403)
        return
    await ws.accept()
    reader = asyncio.create_task(_drain(ws))
    last_version = -1
    last_payload = None
    last_check = 0.0
    try:
        while not reader.done():
            now = time.time()
            version = st.hub.version
            if version != last_version or now - last_check >= OVERVIEW_REFRESH:
                last_version, last_check = version, now
                payload = await build_overview(st, now)
                if payload != last_payload:
                    last_payload = payload
                    await ws.send_json({"type": "overview", "sites": payload})
            await asyncio.wait({reader}, timeout=PUSH_INTERVAL)
    except (*SOCKET_GONE, RuntimeError):
        pass
    finally:
        reader.cancel()
