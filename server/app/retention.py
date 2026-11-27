"""Background retention: delete buckets older than `retention_days`."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from .db import Database

log = logging.getLogger("energy_hub.retention")


async def purge(db: Database, retention_days: float, now: float | None = None,
                batch: int = 5000) -> int:
    now = time.time() if now is None else now
    cutoff = int(now - retention_days * 86400)
    total = 0
    while True:
        n = await db.delete_older(cutoff, batch)
        total += n
        if n < batch:
            break
        await asyncio.sleep(0)  # let other tasks run between batches
    if total:
        log.info("retention: deleted %d buckets older than %s days", total, retention_days)
    return total


async def retention_loop(db: Database, retention_days: float, interval: float = 3600,
                         clock: Callable[[], float] = time.time) -> None:
    while True:
        try:
            await purge(db, retention_days, clock())
        except Exception:
            log.exception("retention run failed")
        await asyncio.sleep(interval)
