"""/ws/ingest: clients push device instances and readings. See docs/protocol.md (version 2)."""
from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette import websockets as _starlette_ws

from .auth import site_for_authorization
from .devices import Catalogue
from .model import load_site_model

log = logging.getLogger("energy_hub.ingest")
router = APIRouter()

# The socket can also be closed under our feet by another task (a newer connection replacing
# this one). Depending on the Starlette version that surfaces as a different exception class.
SOCKET_GONE: tuple[type[BaseException], ...] = tuple(
    c for c in (WebSocketDisconnect, getattr(_starlette_ws, "WebSocketDisconnected", None)) if c)

PROTOCOL_VERSION = 2
MAX_MESSAGE_BYTES = 256 * 1024
MAX_ITEMS_PER_BATCH = 1000
MAX_MESSAGES_PER_SECOND = 100
MAX_INSTANCES = 100
LAST_SEEN_INTERVAL = 30.0

INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
LIMITS = {
    "max_message_bytes": MAX_MESSAGE_BYTES,
    "max_items_per_batch": MAX_ITEMS_PER_BATCH,
    "max_messages_per_second": MAX_MESSAGES_PER_SECOND,
    "max_instances": MAX_INSTANCES,
}


class ConfigProblem(Exception):
    """A `config` message was rejected. `path` points at the first problem."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.message = message


class RateLimiter:
    def __init__(self, limit: int, window: float = 1.0, clock=time.monotonic) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: deque[float] = deque()

    def hit(self) -> bool:
        now = self.clock()
        while self._hits and self._hits[0] < now - self.window:
            self._hits.popleft()
        self._hits.append(now)
        return len(self._hits) <= self.limit


def _error(code: str, message: str, path: str | None = None) -> dict:
    msg = {"type": "error", "code": code, "message": message}
    if path is not None:
        msg["path"] = path
    return msg


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_config(msg: dict, catalogue: Catalogue) -> tuple[str, list[dict]]:
    """Validate a `config` message. Returns (currency, instances) or raises ConfigProblem."""
    currency = msg.get("currency", "")
    if not isinstance(currency, str) or len(currency) > 8:
        raise ConfigProblem("currency", "must be a text of at most 8 characters")
    instances = msg.get("instances")
    if not isinstance(instances, list):
        raise ConfigProblem("instances", "must be a list")
    if len(instances) > MAX_INSTANCES:
        raise ConfigProblem("instances", f"at most {MAX_INSTANCES} instances are allowed")

    seen: set[str] = set()
    result: list[dict] = []
    for i, inst in enumerate(instances):
        p = f"instances[{i}]"
        if not isinstance(inst, dict):
            raise ConfigProblem(p, "must be an object")
        iid = inst.get("id")
        if not isinstance(iid, str) or not INSTANCE_ID_RE.match(iid):
            raise ConfigProblem(f"{p}.id", "must match ^[a-z0-9][a-z0-9_-]{0,39}$")
        if iid in seen:
            raise ConfigProblem(f"{p}.id", f"duplicate instance id {iid!r}")
        seen.add(iid)
        device = inst.get("device")
        if not isinstance(device, str) or catalogue.device(device) is None:
            raise ConfigProblem(f"{p}.device", f"unknown device {device!r}")
        name = inst.get("name")
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ConfigProblem(f"{p}.name", "must be a text of 1 to 80 characters")
        sensors = inst.get("sensors")
        if not isinstance(sensors, dict) or not sensors:
            raise ConfigProblem(f"{p}.sensors", "must be an object with at least one sensor")
        clean: dict[str, dict] = {}
        for sid, entry in sensors.items():
            sp = f"{p}.sensors.{sid}"
            sensor = catalogue.sensor(device, sid)
            if sensor is None:
                raise ConfigProblem(sp, f"unknown sensor {sid!r} for device {device!r}")
            if not isinstance(entry, dict):
                raise ConfigProblem(sp, "must be an object with 'source' or 'fixed'")
            has_source, has_fixed = "source" in entry, "fixed" in entry
            if has_source and has_fixed:
                raise ConfigProblem(sp, "give either 'source' or 'fixed', not both")
            if not has_source and not has_fixed:
                raise ConfigProblem(sp, "needs a 'source' (or a 'fixed' number for price slots)")
            if has_source and sensor.fixed_only:
                raise ConfigProblem(f"{sp}.source", f"sensor {sid!r} takes only a fixed value")
            if has_source:
                src = entry["source"]
                if not isinstance(src, str) or not 1 <= len(src) <= 255:
                    raise ConfigProblem(f"{sp}.source", "must be a text of 1 to 255 characters")
                clean[sid] = {"source": src}
            else:
                if not sensor.allow_fixed:
                    raise ConfigProblem(f"{sp}.fixed", f"sensor {sid!r} does not accept a fixed value")
                fixed = entry["fixed"]
                if sensor.fixed_only:
                    if not _is_number(fixed) or fixed <= 0:
                        raise ConfigProblem(f"{sp}.fixed", "must be a number greater than 0")
                elif not _is_number(fixed) or fixed < 0:
                    raise ConfigProblem(f"{sp}.fixed", "must be a number of 0 or more")
                limits = sensor.selector.number
                if limits is not None:
                    if limits.min is not None and fixed < limits.min:
                        raise ConfigProblem(f"{sp}.fixed", f"must be at least {limits.min:g}")
                    if limits.max is not None and fixed > limits.max:
                        raise ConfigProblem(f"{sp}.fixed", f"must be at most {limits.max:g}")
                clean[sid] = {"fixed": float(fixed)}
        result.append({"id": iid, "device": device, "name": name, "sensors": clean})
    return currency, result


def _key_kinds(instances: list[dict], catalogue: Catalogue) -> dict[str, str]:
    """key -> kind for every sensor that has a source."""
    out: dict[str, str] = {}
    for inst in instances:
        for sid, entry in inst["sensors"].items():
            if "source" in entry:
                out[f"{inst['id']}.{sid}"] = catalogue.sensor(inst["device"], sid).kind
    return out


@router.websocket("/ws/ingest")
async def ingest(ws: WebSocket):
    st = ws.app.state
    cfg = st.config
    site = site_for_authorization(cfg, ws.headers.get("authorization"))
    if site is None:
        await ws.close(code=4401)          # before accept: the client sees HTTP 403
        return
    await ws.accept()

    # One live connection per site: a new one replaces a stale one.
    old = st.ingest_conns.get(site.id)
    st.ingest_conns[site.id] = ws
    if old is not None:
        try:
            await old.close(code=4000)
        except Exception:  # noqa: BLE001 - old socket may already be dead
            pass

    db = st.db
    await ws.send_json({
        "type": "hello",
        "version": PROTOCOL_VERSION,
        "site": {"id": site.id, "name": site.name},
        "server_time": time.time(),
        "limits": LIMITS,
        "devices": cfg.catalogue.hello(),
    })

    model = await load_site_model(db, cfg.catalogue, site.id)
    keys: dict[str, str] = {e.key: e.kind for e in model.entities}
    limiter = RateLimiter(MAX_MESSAGES_PER_SECOND)
    last_touch = 0.0
    await db.touch_site(site.id, time.time())
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await ws.close(code=1009)
                return
            if not limiter.hit():
                await ws.send_json(_error("rate_limited", "too many messages"))
                await ws.close(code=4429)
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                await ws.send_json(_error("bad_json", "message is not valid JSON"))
                continue
            if not isinstance(msg, dict):
                await ws.send_json(_error("bad_message", "message must be an object"))
                continue

            kind = msg.get("type")
            if kind == "config":
                await ws.send_json(await _handle_config(st, site.id, msg, keys))
            elif kind == "states":
                await ws.send_json(_handle_states(st, site.id, msg, keys))
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
            else:
                await ws.send_json(_error("unknown_type", f"unknown message type {kind!r}"))

            now = time.time()
            if now - last_touch > LAST_SEEN_INTERVAL:
                last_touch = now
                await db.touch_site(site.id, now)
    except SOCKET_GONE:
        pass
    finally:
        if st.ingest_conns.get(site.id) is ws:
            del st.ingest_conns[site.id]
        try:
            await db.touch_site(site.id, time.time())
        except Exception:  # noqa: BLE001 - db may be closing during shutdown
            pass


async def _handle_config(st, site_id: str, msg: dict, keys: dict[str, str]) -> dict:
    try:
        currency, instances = parse_config(msg, st.config.catalogue)
    except ConfigProblem as problem:
        return _error("invalid_config", problem.message, problem.path)

    removed = await st.db.replace_config(site_id, instances)
    await st.db.set_site_meta(site_id, {"currency": currency})
    new_keys = _key_kinds(instances, st.config.catalogue)
    st.hub.forget(site_id, [k for k in keys if k not in new_keys])
    keys.clear()
    keys.update(new_keys)
    return {
        "type": "ack", "for": "config",
        "instances": len(instances),
        "sensors": sum(len(i["sensors"]) for i in instances),
        "removed": removed,
    }


def _handle_states(st, site_id: str, msg: dict, keys: dict[str, str]) -> dict:
    items = msg.get("items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS_PER_BATCH:
        return _error("bad_states", f"'items' must be a list of at most {MAX_ITEMS_PER_BATCH}")
    accepted = rejected = 0
    unknown: list[str] = []
    now = time.time()
    for item in items:
        if not isinstance(item, dict):
            rejected += 1
            continue
        key = item.get("key")
        kind = keys.get(key) if isinstance(key, str) else None
        if kind is None:
            rejected += 1
            if isinstance(key, str) and key not in unknown and len(unknown) < 20:
                unknown.append(key[:64])
            continue
        value = item.get("value")
        ts = item.get("ts", now)
        if not _is_number(value) or not _is_number(ts):
            rejected += 1
            continue
        if st.aggregator.add(site_id, key, kind, float(ts), float(value)):
            accepted += 1
            st.hub.update(site_id, key, float(ts), float(value))
        else:
            rejected += 1
    reply = {"type": "ack", "for": "states", "accepted": accepted, "rejected": rejected}
    if unknown:
        reply["unknown_keys"] = unknown
    return reply
