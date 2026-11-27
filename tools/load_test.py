#!/usr/bin/env python3
"""Load generator: several sites pushing about 15 sensors each over /ws/ingest (protocol v2).

Real life is 1 batch per second per site (rate=1). Use --rate 10 to test with 10x headroom.
The sensors are taken from the server's own device catalogue, so this works with any devices.yaml.

    python tools/load_test.py --port 8000 --key K1 --key K2 --key K3 --duration 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets


def build_instances(hello: dict, count: int) -> tuple[list[dict], dict[str, str]]:
    """Instances that together carry `count` sensors, and key -> kind for each."""
    instances: list[dict] = []
    kinds: dict[str, str] = {}
    for dev in hello["devices"]:
        take = [s for s in dev["sensors"] if not s["allow_fixed"]][: count - len(kinds)]
        if not take:
            break
        iid = f"load-{dev['id']}".replace("_", "-")[:40]
        instances.append({"id": iid, "device": dev["id"], "name": f"Load {dev['name']}",
                          "sensors": {s["id"]: {"source": f"load.{iid}.{s['id']}"} for s in take}})
        kinds.update({f"{iid}.{s['id']}": s["kind"] for s in take})
        if len(kinds) >= count:
            break
    return instances, kinds


async def _site(port: int, key: str, sensors: int, rate: float, duration: float, stats: dict) -> None:
    uri = f"ws://127.0.0.1:{port}/ws/ingest"
    async with websockets.connect(uri, additional_headers={"Authorization": f"Bearer {key}"}) as ws:
        hello = json.loads(await ws.recv())
        instances, kinds = build_instances(hello, sensors)
        await ws.send(json.dumps({"type": "config", "instances": instances}))
        ack = json.loads(await ws.recv())
        assert ack["type"] == "ack", ack

        async def reader() -> None:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "ack" and msg.get("for") == "states":
                    stats["accepted"] += msg["accepted"]
                    stats["rejected"] += msg["rejected"]
                elif msg.get("type") == "error":
                    stats["errors"] += 1

        task = asyncio.create_task(reader())
        counters = {k: 1000.0 for k, kind in kinds.items() if kind == "counter"}
        end = time.monotonic() + duration
        interval = 1.0 / rate
        nxt = time.monotonic()
        while time.monotonic() < end:
            now = time.time()
            items = []
            for i, (k, kind) in enumerate(kinds.items()):
                if kind == "counter":
                    counters[k] += 0.001
                    value = counters[k]
                else:
                    value = 20 + (i * 7 + now) % 5
                items.append({"key": k, "ts": now, "value": value})
            await ws.send(json.dumps({"type": "states", "items": items}))
            stats["sent"] += len(items)
            nxt += interval
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))
        await asyncio.sleep(0.5)  # let the last acks arrive
        task.cancel()


async def run_load(port: int, keys: list[str], rate: float = 1.0, duration: float = 10.0,
                   sensors_per_site: int = 15) -> dict:
    stats = {"sent": 0, "accepted": 0, "rejected": 0, "errors": 0}
    await asyncio.gather(*(_site(port, k, sensors_per_site, rate, duration, stats) for k in keys))
    return stats


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--key", action="append", required=True, help="one site key per simulated site")
    p.add_argument("--rate", type=float, default=1.0, help="batches per second per site")
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--sensors", type=int, default=15, help="sensors per site")
    a = p.parse_args()
    print(asyncio.run(run_load(a.port, a.key, a.rate, a.duration, a.sensors)))


if __name__ == "__main__":
    main()
