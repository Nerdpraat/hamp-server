"""In-memory 1-minute bucket aggregation, flushed to SQLite."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Callable

from .db import Database

log = logging.getLogger("energy_hub.aggregator")

# Like Home Assistant: a counter only counts as reset when it falls below 90% of its
# previous value. Smaller decreases are treated as noise.
RESET_FACTOR = 0.9


@dataclass
class _Bucket:
    delta: float = 0.0
    last: float | None = None
    last_ts: float = 0.0
    min: float | None = None
    max: float | None = None
    avg_sum: float = 0.0
    n: int = 0

    def merge(self, other: "_Bucket") -> None:
        self.delta += other.delta
        if other.last is not None and (self.last is None or other.last_ts >= self.last_ts):
            self.last, self.last_ts = other.last, other.last_ts
        if other.min is not None:
            self.min = other.min if self.min is None else min(self.min, other.min)
        if other.max is not None:
            self.max = other.max if self.max is None else max(self.max, other.max)
        self.avg_sum += other.avg_sum
        self.n += other.n


class Aggregator:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time,
                 max_age: float = 3600, max_gap: float = 3600,
                 future_tolerance: float = 60) -> None:
        self.db = db
        self.clock = clock
        self.max_age = max_age
        self.max_gap = max_gap
        self.future_tolerance = future_tolerance
        self._open: dict[tuple[str, str, int], _Bucket] = {}
        self._prev: dict[tuple[str, str], tuple[float, float]] = {}
        self._stop = asyncio.Event()

    async def seed(self) -> None:
        """Restore previous counter values so a restart does not lose a delta."""
        for site, key, ts, last in await self.db.latest_lasts():
            self._prev[(site, key)] = (last, float(ts))

    def add(self, site: str, key: str, kind: str, ts: float, value: float) -> bool:
        """Record one sample. Returns False when the sample was dropped."""
        if not math.isfinite(value) or not math.isfinite(ts):
            return False
        now = self.clock()
        if ts > now + self.future_tolerance or ts < now - self.max_age:
            return False
        minute = int(ts // 60 * 60)
        bucket = self._open.setdefault((site, key, minute), _Bucket())
        if kind == "counter":
            prev = self._prev.get((site, key))
            delta = 0.0
            if prev is not None:
                pval, pts = prev
                if ts < pts:
                    return False
                if ts - pts <= self.max_gap:
                    if value >= pval:
                        delta = value - pval
                    elif value < pval * RESET_FACTOR:
                        delta = value  # large drop => meter reset, counting restarts from 0
                    # else: small dip (rounding/jitter): no delta, baseline follows the meter
            self._prev[(site, key)] = (value, ts)
            bucket.delta += delta
        else:
            bucket.min = value if bucket.min is None else min(bucket.min, value)
            bucket.max = value if bucket.max is None else max(bucket.max, value)
            bucket.avg_sum += value
        bucket.n += 1
        if bucket.last is None or ts >= bucket.last_ts:
            bucket.last, bucket.last_ts = value, ts
        return True

    async def flush(self) -> int:
        if not self._open:
            return 0
        pending, self._open = self._open, {}
        rows = [(s, k, m, b.delta, b.last, b.min, b.avg_sum, b.max, b.n)
                for (s, k, m), b in pending.items()]
        try:
            await self.db.upsert_buckets(rows)
        except Exception:
            log.exception("flush failed; keeping %d buckets in memory", len(rows))
            for ident, b in pending.items():
                self._open.setdefault(ident, _Bucket()).merge(b)
            return 0
        return len(rows)

    async def run(self, interval: float) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            await self.flush()

    async def stop(self) -> None:
        self._stop.set()
        await self.flush()
