"""An in-process stand-in for the Energy Hub ingest endpoint (protocol version 2).

Every message the client sends is validated against the protocol JSON Schemas, and violations are
collected in `hub.violations` so tests can assert there were none.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import protocol_schemas as schemas  # noqa: E402

KEY = "unit-test-key-0123456789"


from catalogue import DEVICES  # noqa: E402


class FakeHub:
    def __init__(self, key: str = KEY, version: int = 2) -> None:
        self.key = key
        self.version = version
        self.limits = {"max_message_bytes": 262144, "max_items_per_batch": 1000,
                       "max_messages_per_second": 100, "max_instances": 100}
        self.server_time_offset = 0.0
        self.configs: list[dict] = []
        self.batches: list[list[dict]] = []
        self.violations: list[str] = []
        self.keys: set[str] = set()
        self.connections = 0
        self.sockets: list[web.WebSocketResponse] = []
        self.reject_config = False
        self.server: TestServer | None = None
        self.port = 0

    @property
    def items(self) -> list[dict]:
        return [i for b in self.batches for i in b]

    def _check_config(self, data: dict) -> dict | None:
        """The parts of the real server's validation the client must not trip over."""
        by_device = {d["id"]: {s["id"]: s for s in d["sensors"]} for d in DEVICES}
        for i, inst in enumerate(data["instances"]):
            sensors = by_device.get(inst["device"])
            if sensors is None:
                return {"type": "error", "code": "invalid_config", "message": f"unknown device {inst['device']!r}",
                        "path": f"instances[{i}].device"}
            for sid, entry in inst["sensors"].items():
                if sid not in sensors:
                    return {"type": "error", "code": "invalid_config", "message": f"unknown sensor {sid!r}",
                            "path": f"instances[{i}].sensors.{sid}"}
                if "fixed" in entry and not sensors[sid]["allow_fixed"]:
                    return {"type": "error", "code": "invalid_config", "message": "no fixed value",
                            "path": f"instances[{i}].sensors.{sid}.fixed"}
        return None

    async def _handler(self, request: web.Request) -> web.StreamResponse:
        if request.headers.get("Authorization") != f"Bearer {self.key}":
            return web.Response(status=403)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        self.sockets.append(ws)
        await ws.send_json({"type": "hello", "version": self.version,
                            "site": {"id": "unit", "name": "Unit"}, "server_time": time.time() + self.server_time_offset,
                            "limits": self.limits, "devices": DEVICES})
        async for msg in ws:
            data = msg.json()
            problems = schemas.errors(data, "client_message")
            if problems:
                self.violations.append(f"{problems} in {str(data)[:200]}")
            if data["type"] == "config":
                self.configs.append(data)
                error = {"type": "error", "code": "invalid_config", "message": "nope", "path": "instances"} \
                    if self.reject_config else self._check_config(data)
                if error:
                    await ws.send_json(error)
                else:
                    self.keys = {f"{i['id']}.{sid}" for i in data["instances"]
                                 for sid, e in i["sensors"].items() if "source" in e}
                    await ws.send_json({"type": "ack", "for": "config", "instances": len(data["instances"]),
                                        "sensors": sum(len(i["sensors"]) for i in data["instances"]),
                                        "removed": {"instances": [], "sensors": []}})
            elif data["type"] == "states":
                self.batches.append(data["items"])
                known = [i for i in data["items"] if i["key"] in self.keys]
                reply = {"type": "ack", "for": "states", "accepted": len(known),
                         "rejected": len(data["items"]) - len(known)}
                unknown = sorted({i["key"] for i in data["items"] if i["key"] not in self.keys})
                if unknown:
                    reply["unknown_keys"] = unknown[:20]
                await ws.send_json(reply)
        return ws

    async def start(self, port: int = 0) -> int:
        app = web.Application()
        app.router.add_get("/ws/ingest", self._handler)
        self.server = TestServer(app, port=port or None)
        await self.server.start_server()
        self.port = self.server.port
        return self.port

    async def stop(self) -> None:
        if self.server is not None:
            for ws in list(self.sockets):
                await ws.close()
            await self.server.close()
            self.server = None

    async def drop_connections(self, code: int = 1000) -> None:
        for ws in list(self.sockets):
            await ws.close(code=code)
        self.sockets.clear()


async def wait_for(cond, timeout: float = 5.0, step: float = 0.02) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(step)
