#!/usr/bin/env python3
"""A small, complete client for the Energy Hub ingest protocol (docs/protocol.md, version 2).

It needs only the `websockets` package and the standard library. It is meant as a readable example
for building your own client, in any language. It shows:

* the handshake with the site key and the version check,
* choosing device types from the catalogue the server sends in `hello`,
* sending `config`, then `states`, and reading the acknowledgements,
* the client rules: throttle, keep-alive resend every 60 s, reconnect with backoff, bounded buffer.

Run a demo (two batteries, a smart plug and a meter with a fixed price) against a server:

    python tools/reference_client.py --url ws://localhost:8000/ws/ingest --key <site key> --duration 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time
from collections import deque

import websockets

SUPPORTED_VERSION = 2
KEEPALIVE_SECONDS = 60


class ProtocolError(Exception):
    pass


class Client:
    """Keeps one connection to the server alive and sends readings."""

    def __init__(self, url: str, key: str, instances: list[dict], currency: str = "",
                 throttle: float = 5.0, max_buffer: int = 120) -> None:
        self.url, self.key = url, key
        self.instances, self.currency = instances, currency
        self.throttle = throttle
        self.hello: dict = {}
        self._pending: dict[str, tuple[float, float]] = {}       # key -> (ts, value): latest only
        self._last_sent: dict[str, tuple[float, float]] = {}     # key -> (value, when)
        self._buffer: deque[list[dict]] = deque(maxlen=max_buffer)  # batches made while offline
        self._ws = None

    # ---- what the application calls ------------------------------------------------------
    def keys(self) -> list[str]:
        """Keys of the sensors that have a source: <instance id>.<sensor id>."""
        return [f"{i['id']}.{sid}" for i in self.instances
                for sid, entry in i["sensors"].items() if "source" in entry]

    def push(self, key: str, value: float) -> None:
        if math.isfinite(value):
            self._pending[key] = (time.time(), float(value))

    # ---- connection ---------------------------------------------------------------------------
    async def run(self, duration: float | None = None) -> None:
        end = None if duration is None else time.monotonic() + duration
        backoff = 1.0
        while end is None or time.monotonic() < end:
            try:
                async with websockets.connect(
                        self.url, additional_headers={"Authorization": f"Bearer {self.key}"}) as ws:
                    await self._session(ws, end)
                    return
            except websockets.exceptions.InvalidStatus as err:
                if err.response.status_code in (401, 403):
                    raise ProtocolError("the server refused the key (HTTP %d)" % err.response.status_code)
                raise
            except (OSError, websockets.exceptions.ConnectionClosed) as err:
                code = getattr(getattr(err, "rcvd", None), "code", None)
                wait = 60 if code == 4000 else backoff        # 4000: another client uses this key
                print(f"connection lost ({err}); retrying in {wait:.0f}s")
                await asyncio.sleep(wait + random.uniform(0, wait / 4))
                backoff = min(backoff * 2, 60)

    async def _session(self, ws, end: float | None) -> None:
        self.hello = json.loads(await ws.recv())
        if self.hello.get("type") != "hello" or self.hello.get("version") != SUPPORTED_VERSION:
            raise ProtocolError(f"unsupported server hello: {str(self.hello)[:200]}")
        catalogue = {d["id"]: {s["id"] for s in d["sensors"]} for d in self.hello["devices"]}
        for inst in self.instances:                               # fail early, with our own message
            if inst["device"] not in catalogue or not set(inst["sensors"]) <= catalogue[inst["device"]]:
                raise ProtocolError(f"instance {inst['id']} does not fit the server's catalogue")

        await ws.send(json.dumps({"type": "config", "currency": self.currency, "instances": self.instances}))
        reply = json.loads(await ws.recv())
        if reply.get("type") == "error":
            raise ProtocolError(f"config rejected: {reply['message']} (at {reply.get('path')})")
        print(f"connected to site {self.hello['site']['name']}: config accepted "
              f"({reply['instances']} instances, {reply['sensors']} sensors)")
        reader = asyncio.create_task(self._read(ws))
        try:
            while end is None or time.monotonic() < end:
                while self._buffer:                               # oldest first
                    await ws.send(json.dumps({"type": "states", "items": self._buffer[0]}))
                    self._buffer.popleft()
                batch = self._collect()
                if batch:
                    try:
                        await ws.send(json.dumps({"type": "states", "items": batch}))
                    except websockets.exceptions.ConnectionClosed:
                        self._buffer.append(batch)                # keep it for the next connection
                        raise
                await asyncio.sleep(self.throttle)
        finally:
            reader.cancel()

    def _collect(self) -> list[dict]:
        """Latest value per key. Skip unchanged ones, but resend every key once per minute."""
        now = time.time()
        batch, pending, self._pending = [], self._pending, {}
        for key, (ts, value) in pending.items():
            last = self._last_sent.get(key)
            if last and last[0] == value and now - last[1] < KEEPALIVE_SECONDS:
                continue
            self._last_sent[key] = (value, now)
            batch.append({"key": key, "ts": ts, "value": value})
        return batch

    async def _read(self, ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "error":
                print("server error:", msg["code"], msg["message"])
            elif msg.get("type") == "ack" and msg.get("for") == "states" and msg.get("rejected"):
                print("server rejected", msg["rejected"], "readings", msg.get("unknown_keys", ""))


def demo_instances() -> list[dict]:
    """Two batteries, one smart plug and a meter with a fixed import price."""
    def battery(n: int) -> dict:
        return {"id": f"battery-{n}", "device": "battery", "name": f"Battery {n}", "sensors": {
            "state_of_charge": {"source": f"demo.battery{n}.soc"},
            "power": {"source": f"demo.battery{n}.power"},
            "energy_charged": {"source": f"demo.battery{n}.charged"},
            "energy_discharged": {"source": f"demo.battery{n}.discharged"}}}
    return [
        battery(1), battery(2),
        {"id": "plug-car", "device": "consumer_device", "name": "Car charger", "sensors": {
            "power": {"source": "demo.car.power"}, "energy": {"source": "demo.car.energy"}}},
        {"id": "meter", "device": "electricitymeter", "name": "Electricity Meter", "sensors": {
            "energy_import": {"source": "demo.meter.import"},
            "power_import": {"source": "demo.meter.power"},
            "price_import": {"fixed": 0.25}}},
    ]


async def demo(url: str, key: str, duration: float) -> None:
    client = Client(url, key, demo_instances(), currency="EUR", throttle=1.0)

    async def feed() -> None:
        energy = {k: 1000.0 for k in client.keys() if k.endswith(("charged", "discharged", "energy", "import"))}
        while True:
            t = time.time()
            for key in client.keys():
                if key in energy:
                    energy[key] += 0.001
                    client.push(key, energy[key])
                elif key.endswith("soc"):
                    client.push(key, 50 + 10 * math.sin(t / 30))
                else:
                    client.push(key, 500 + 400 * math.sin(t / 10))
            await asyncio.sleep(1)

    feeder = asyncio.create_task(feed())
    try:
        await client.run(duration)
    finally:
        feeder.cancel()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="ws://localhost:8000/ws/ingest")
    p.add_argument("--key", required=True, help="the site key from auth.yaml")
    p.add_argument("--duration", type=float, default=30, help="seconds to run the demo")
    a = p.parse_args()
    try:
        asyncio.run(demo(a.url, a.key, a.duration))
    except ProtocolError as err:
        raise SystemExit(f"error: {err}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
