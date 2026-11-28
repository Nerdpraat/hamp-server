#!/usr/bin/env python3
"""Simulate a site pushing about 40 sensors at 1 Hz to /ws/ingest (protocol version 2).

    python tools/fake_site.py --url ws://localhost:8000/ws/ingest --key <site key>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).parent))
from simulation import Sim, config_message, readings, static_values  # noqa: E402


async def run(url: str, key: str, rate: float, duration: float | None, seed: int,
              state: str | None = None) -> int:
    sim = Sim(seed)
    if state:
        sim.load(state)
    sent = 0
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {key}"}) as ws:
        hello = json.loads(await ws.recv())
        if hello.get("version") != 2:
            raise SystemExit(f"unsupported protocol version {hello.get('version')}")
        print(f"connected to site {hello['site']['name']} ({len(hello['devices'])} device types)")
        await ws.send(json.dumps(config_message()))
        reply = json.loads(await ws.recv())
        if reply["type"] == "error":
            raise SystemExit(f"config rejected: {reply['message']} (at {reply.get('path')})")
        print("config accepted:", reply["instances"], "instances,", reply["sensors"], "sensors")
        for key, value in static_values().items():          # one item per message: the ack confirms it
            await ws.send(json.dumps({"type": "states", "items": [{"key": key, "value": value}]}))
            reply = json.loads(await ws.recv())
            if reply.get("type") != "ack" or reply["accepted"] != 1:
                raise SystemExit(f"static value {key}={value} refused: {reply}")
        start = time.time()
        interval = 1.0 / rate
        while duration is None or time.time() - start < duration:
            now = time.time()
            values = readings(sim.step(now, interval))
            items = [{"key": k, "ts": now, "value": v} for k, v in values.items()]
            await ws.send(json.dumps({"type": "states", "items": items}))
            reply = json.loads(await ws.recv())
            if reply.get("type") == "error":
                print("server error:", reply)
            elif reply["rejected"]:
                print("rejected", reply["rejected"], reply.get("unknown_keys", ""))
            sent += len(items)
            await asyncio.sleep(interval)
    return sent


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="ws://localhost:8000/ws/ingest")
    p.add_argument("--key", required=True, help="the site key from auth.yaml")
    p.add_argument("--rate", type=float, default=1.0, help="batches per second")
    p.add_argument("--duration", type=float, default=None, help="seconds (default: forever)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--state", help="simulator counters written by seed_demo.py --state")
    a = p.parse_args()
    try:
        n = asyncio.run(run(a.url, a.key, a.rate, a.duration, a.seed, a.state))
        print("sent", n, "readings")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
