#!/usr/bin/env python3
"""Fill a server database with simulated history so the dashboard has something to show.

    python tools/seed_demo.py --config-dir server/config.example --site dru05 --days 3

Reads the same config directory as the server (database path from config.yaml, or ENERGY_HUB_DB_PATH).
Run it while the server is stopped (or accept brief SQLite write contention).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(Path(__file__).parent))

from app.config import load_config  # noqa: E402
from app.db import Database  # noqa: E402
from simulation import SPECS, Sim, config_message, readings  # noqa: E402



async def seed(config_dir: str, site: str, days: float, state: str | None = None) -> int:
    cfg = load_config(config_dir)
    site_def = cfg.site(site)
    if site_def is None:
        raise SystemExit(f"unknown site {site!r}; sites: {', '.join(s.id for s in cfg.sites)}")
    db = Database(os.environ.get("ENERGY_HUB_DB_PATH") or cfg.db_path)
    await db.connect()
    await db.upsert_site(site, site_def.name)
    message = config_message()
    await db.replace_config(site, message["instances"])
    await db.set_site_meta(site, {"currency": message["currency"]})

    counter_keys = {f"{iid}.{sid}" for iid, device, _n, readers, _f in SPECS for sid in readers
                    if cfg.catalogue.sensor(device, sid).kind == "counter"}
    sim = Sim()
    end = int(time.time() // 60 * 60)
    start = end - int(days * 86400)
    prev = readings(sim.step(start - 60, 60))
    rows = []
    for t in range(start, end, 60):
        values = readings(sim.step(t, 60))
        for key, val in values.items():
            if key in counter_keys:
                rows.append((site, key, t, max(val - prev[key], 0.0), val, None, 0.0, None, 1))
            else:
                rows.append((site, key, t, 0.0, val, val, val, val, 1))
        prev = values
        if len(rows) > 20000:
            await db.upsert_buckets(rows)
            rows = []
    await db.upsert_buckets(rows)
    await db.close()
    if state:
        sim.save(state)
    return (end - start) // 60


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config-dir", required=True)
    p.add_argument("--site", required=True, help="site id, for example dru05")
    p.add_argument("--days", type=float, default=3)
    p.add_argument("--state", help="write final simulator counters here (for fake_site.py --state)")
    a = p.parse_args()
    minutes = asyncio.run(seed(a.config_dir, a.site, a.days, a.state))
    print(f"seeded {minutes} minutes for site {a.site}")


if __name__ == "__main__":
    main()
