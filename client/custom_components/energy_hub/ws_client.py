"""Resilient websocket forwarder to the Energy Hub server (docs/protocol.md, version 2).

No Home Assistant imports: it only needs an aiohttp ClientSession.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import deque
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

PROTOCOL_VERSION = 2
KEEPALIVE_SECONDS = 55  # resend an unchanged value at least this often (keeps charts gap-free)
MAX_BACKOFF = 60.0
AUTH_RETRY_SECONDS = 300.0
REPLACED_WAIT_SECONDS = 60.0     # another client uses this key: do not fight over it
BATCH_LIMIT = 500
CLOCK_SKEW_WARNING = 300.0


class AuthError(Exception):
    """The server rejected the site key."""


class ProtocolError(Exception):
    """The server answered unexpectedly or rejected the configuration."""


def normalize_url(url: str) -> str:
    """Accept http(s)://host[:port][/base] or ws(s)://... and return the ingest ws URL."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    if scheme not in ("ws", "wss") or not parts.netloc:
        raise ValueError(f"unsupported server URL: {url!r}")
    path = parts.path.rstrip("/")
    if not path.endswith("/ws/ingest"):
        path += "/ws/ingest"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def check_hello(hello: Any) -> dict[str, Any]:
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        raise ProtocolError("server did not send hello")
    if hello.get("version") != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol version {hello.get('version')} (this client speaks {PROTOCOL_VERSION})")
    return hello


async def fetch_hello(session: aiohttp.ClientSession, url: str, key: str,
                      timeout: float = 10) -> dict[str, Any]:
    """Connect once, return the server `hello` (site and device catalogue), then disconnect."""
    try:
        async with session.ws_connect(
            normalize_url(url), headers={"Authorization": f"Bearer {key}"}, timeout=timeout
        ) as ws:
            hello = await ws.receive_json(timeout=timeout)
    except aiohttp.WSServerHandshakeError as err:
        if err.status in (401, 403):
            raise AuthError("invalid key") from err
        raise
    return check_hello(hello)


class Forwarder:
    """Throttle, de-duplicate, buffer and send readings to the server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        key: str,
        instances: list[dict[str, Any]] | None = None,
        *,
        throttle: float = 5.0,
        currency: str = "",
        max_buffer: int = 120,
        on_connected: Callable[[], Awaitable[None] | None] | None = None,
        on_auth_failed: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session = session
        self._url = normalize_url(url)
        self._key = key
        self._instances: list[dict[str, Any]] = list(instances or [])
        self.throttle = max(0.05, float(throttle))
        self.currency = currency
        self._on_connected = on_connected
        self._on_auth_failed = on_auth_failed
        self._clock = clock
        self._sleep = sleep

        self._pending: dict[str, tuple[float, float]] = {}
        self._last_sent: dict[str, tuple[float, float]] = {}  # key -> (value, sent_at)
        self._buffer: deque[list[dict[str, float | str]]] = deque(maxlen=max_buffer)
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._stopped = False
        self._allowed: set[str] | None = None  # keys the server knows (None until the first config)
        self._last_resend = 0.0

        self.catalogue: list[dict[str, Any]] = []   # the devices from the server's hello
        self.limits: dict[str, int] = {}
        self.site: dict[str, Any] = {}
        self.auth_failed = False
        self.last_error: str | None = None

    # ---- public API ---------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    @property
    def buffered_batches(self) -> int:
        return len(self._buffer)

    def device(self, device_id: str) -> dict[str, Any] | None:
        return next((d for d in self.catalogue if d["id"] == device_id), None)

    def sensor(self, device_id: str, sensor_id: str) -> dict[str, Any] | None:
        dev = self.device(device_id)
        return next((s for s in dev["sensors"] if s["id"] == sensor_id), None) if dev else None

    def unit_for(self, device_id: str, sensor_id: str) -> str | None:
        s = self.sensor(device_id, sensor_id)
        return s["unit"] if s else None

    def start(self) -> None:
        self._stopped = False
        self._tasks = [
            asyncio.create_task(self._run(), name="energy_hub_connection"),
            asyncio.create_task(self._flusher(), name="energy_hub_flusher"),
        ]

    async def stop(self) -> None:
        self._stopped = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._ws = None

    def push(self, key: str, value: float, ts: float | None = None) -> None:
        """Queue the latest value of a sensor (older queued values are replaced)."""
        self._pending[key] = (self._clock() if ts is None else ts, float(value))

    async def update_instances(self, instances: list[dict[str, Any]]) -> None:
        """Replace the instance list and, when connected, tell the server right away."""
        self._instances = list(instances)
        keys = {f"{i['id']}.{sid}" for i in instances for sid, e in i["sensors"].items() if "source" in e}
        for k in [k for k in self._pending if k not in keys]:
            del self._pending[k]
        for k in [k for k in self._last_sent if k not in keys]:
            del self._last_sent[k]
        if self.connected:
            try:
                await self._send_config(self._ws)
            except (ProtocolError, aiohttp.ClientError, asyncio.TimeoutError) as err:
                self.last_error = str(err)
                _LOGGER.warning("could not apply the new device list: %s", err)

    # ---- sending ------------------------------------------------------
    def _collect(self) -> list[dict[str, float | str]]:
        now = self._clock()
        items: list[dict[str, float | str]] = []
        pending, self._pending = self._pending, {}
        for key, (ts, value) in pending.items():
            if self._allowed is not None and key not in self._allowed:
                continue
            prev = self._last_sent.get(key)
            if prev is not None and prev[0] == value and now - prev[1] < KEEPALIVE_SECONDS:
                continue
            items.append({"key": key, "ts": ts, "value": value})
        return items

    async def _flusher(self) -> None:
        while True:
            await self._sleep(self.throttle)
            items = self._collect()
            for i in range(0, len(items), BATCH_LIMIT):
                await self._deliver(items[i:i + BATCH_LIMIT])

    async def _deliver(self, batch: list[dict[str, float | str]]) -> None:
        async with self._send_lock:
            ws = self._ws
            if ws is not None and not ws.closed:
                try:
                    await ws.send_json({"type": "states", "items": batch})
                except (aiohttp.ClientError, ConnectionError, RuntimeError):
                    pass
                else:
                    now = self._clock()
                    for it in batch:
                        self._last_sent[it["key"]] = (it["value"], now)
                    return
            self._buffer.append(batch)

    async def _drain_and_publish(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Send buffered batches in order, then expose the socket, atomically w.r.t. _deliver."""
        async with self._send_lock:
            while self._buffer:
                batch = self._buffer[0]
                await ws.send_json({"type": "states", "items": batch})
                self._buffer.popleft()
                now = self._clock()
                for it in batch:
                    self._last_sent[it["key"]] = (it["value"], now)
            self._ws = ws

    # ---- connection ---------------------------------------------------
    def _usable_instances(self) -> list[dict[str, Any]]:
        """The instances the server's catalogue can accept.

        The server rejects a whole config for one bad entry, so anything it would not understand
        (unknown device or sensor, a fixed value on a slot that takes none) is dropped here, with a
        warning, instead of taking the other instances down with it.
        """
        if not self.catalogue:
            return [dict(i) for i in self._instances]
        usable = []
        for inst in self._instances:
            dev = self.device(inst["device"])
            if dev is None:
                _LOGGER.warning("server has no device type %r; skipping instance %s", inst["device"], inst["id"])
                continue
            sensors: dict[str, Any] = {}
            for sid, entry in inst["sensors"].items():
                slot = self.sensor(inst["device"], sid)
                if slot is None:
                    _LOGGER.warning("device %r has no sensor %r on this server; skipping it", inst["device"], sid)
                elif "fixed" in entry and not slot["allow_fixed"]:
                    _LOGGER.warning("%s.%s does not accept a fixed value; skipping it", inst["id"], sid)
                elif slot.get("fixed_only") and "source" in entry:
                    _LOGGER.warning("%s.%s takes only a fixed value; skipping it", inst["id"], sid)
                else:
                    sensors[sid] = entry
            if sensors:
                usable.append({**inst, "sensors": sensors})
        return usable

    async def _send_config(self, ws: aiohttp.ClientWebSocketResponse | None) -> None:
        assert ws is not None
        usable = self._usable_instances()
        limit = self.limits.get("max_instances")
        if limit and len(usable) > limit:
            _LOGGER.warning("server accepts at most %d instances; sending the first %d", limit, limit)
            usable = usable[:limit]
        self._allowed = {f"{i['id']}.{sid}" for i in usable for sid, e in i["sensors"].items() if "source" in e}
        await ws.send_json({"type": "config", "currency": self.currency, "instances": usable})

    async def _expect_ack(self, ws: aiohttp.ClientWebSocketResponse, what: str) -> None:
        while True:
            msg = await ws.receive_json(timeout=10)
            if msg.get("type") == "error":
                where = f" (at {msg['path']})" if msg.get("path") else ""
                raise ProtocolError(f"{msg.get('code')}: {msg.get('message')}{where}")
            if msg.get("type") == "ack" and msg.get("for") == what:
                return

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopped:
            connected_ok = False
            wait = 0.0
            try:
                connected_ok = await self._session_once()
            except asyncio.CancelledError:
                raise
            except AuthError as err:
                if not self.auth_failed and self._on_auth_failed is not None:
                    self._on_auth_failed()
                self.auth_failed = True
                self.last_error = str(err)
                _LOGGER.error("Energy Hub server rejected the key; retrying in %ss", AUTH_RETRY_SECONDS)
                await self._sleep(AUTH_RETRY_SECONDS)
                continue
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ProtocolError) as err:
                self.last_error = str(err) or type(err).__name__
                _LOGGER.warning("Energy Hub connection problem: %s", self.last_error)
            finally:
                self._ws = None
            if self.last_error and "same key" in self.last_error:
                wait = REPLACED_WAIT_SECONDS
            backoff = 1.0 if connected_ok else min(backoff * 2, MAX_BACKOFF)
            await self._sleep(max(wait, backoff + random.uniform(0, backoff / 4)))

    async def _session_once(self) -> bool:
        try:
            ws_cm = self._session.ws_connect(
                self._url, headers={"Authorization": f"Bearer {self._key}"}, heartbeat=30, timeout=10)
            async with ws_cm as ws:
                hello = check_hello(await ws.receive_json(timeout=10))
                self.site = hello.get("site", {})
                self.catalogue = hello.get("devices", [])
                self.limits = hello.get("limits", {})
                skew = abs(hello.get("server_time", self._clock()) - self._clock())
                if skew > CLOCK_SKEW_WARNING:
                    _LOGGER.warning("the clock differs from the server's by %.0f s; readings use this clock", skew)
                self.auth_failed = False
                await self._send_config(ws)
                await self._expect_ack(ws, "config")
                await self._drain_and_publish(ws)
                self.last_error = None
                _LOGGER.info("connected to Energy Hub as site %s", self.site.get("id"))
                if self._on_connected is not None:
                    res = self._on_connected()
                    if asyncio.iscoroutine(res):
                        await res
                await self._read(ws)
                return True
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise AuthError("invalid key") from err
            raise

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = msg.json()
            kind = data.get("type")
            if kind == "error":
                _LOGGER.warning("server error: %s %s", data.get("code"), data.get("message"))
            elif kind == "ack" and data.get("for") == "states" and data.get("unknown_keys"):
                if self._clock() - self._last_resend > 30:
                    self._last_resend = self._clock()
                    _LOGGER.info("server does not know keys %s; resending config", data["unknown_keys"])
                    await self._send_config(ws)
        if ws.close_code == 4000:
            self.last_error = "another client connected with the same key"
            _LOGGER.warning(self.last_error)
