"""SQLite storage (WAL) with simple versioned migrations."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Sequence

import aiosqlite

log = logging.getLogger("energy_hub.db")

# One schema, no migration chain: the project is in development and there is no installed base.
# Bump SCHEMA_VERSION when the layout changes. A database of another version (an older development
# layout) is dropped and recreated, which also discards its data.
SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE sites(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    last_seen REAL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE devices(
    site_id TEXT NOT NULL,
    instance TEXT NOT NULL,
    device TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY(site_id, instance)
);
CREATE TABLE entities(
    site_id TEXT NOT NULL,
    key TEXT NOT NULL,
    instance TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY(site_id, key)
);
CREATE TABLE fixed_values(
    site_id TEXT NOT NULL,
    instance TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY(site_id, instance, sensor_id)
);
CREATE TABLE buckets(
    site_id TEXT NOT NULL,
    key TEXT NOT NULL,
    ts_min INTEGER NOT NULL,
    delta REAL NOT NULL DEFAULT 0,
    last REAL,
    min REAL,
    avg_sum REAL NOT NULL DEFAULT 0,
    max REAL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(site_id, key, ts_min)
) WITHOUT ROWID;
CREATE INDEX idx_buckets_ts ON buckets(ts_min);
"""
_TABLES = ("buckets", "fixed_values", "entities", "devices", "sites")

# (site_id, key, ts_min, delta, last, min, avg_sum, max, n)
BucketRow = tuple

_UPSERT = """
INSERT INTO buckets(site_id,key,ts_min,delta,last,min,avg_sum,max,n)
VALUES(?,?,?,?,?,?,?,?,?)
ON CONFLICT(site_id,key,ts_min) DO UPDATE SET
  delta = delta + excluded.delta,
  last = excluded.last,
  min = CASE WHEN min IS NULL THEN excluded.min
             WHEN excluded.min IS NULL THEN min
             ELSE MIN(min, excluded.min) END,
  avg_sum = avg_sum + excluded.avg_sum,
  max = CASE WHEN max IS NULL THEN excluded.max
             WHEN excluded.max IS NULL THEN max
             ELSE MAX(max, excluded.max) END,
  n = n + excluded.n
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "database not connected"
        return self._db

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._migrate()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _migrate(self) -> None:
        cur = await self.conn.execute("PRAGMA user_version")
        version = (await cur.fetchone())[0]
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            log.warning("database %s has schema version %s, expected %s: resetting it (all data is lost)",
                        self.path, version, SCHEMA_VERSION)
            for table in _TABLES:
                await self.conn.execute(f"DROP TABLE IF EXISTS {table}")
            await self.conn.execute("DROP INDEX IF EXISTS idx_buckets_ts")
        await self.conn.executescript(SCHEMA)
        await self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        await self.conn.commit()

    # ---- sites ---------------------------------------------------------
    async def upsert_site(self, site_id: str, name: str) -> None:
        await self.conn.execute(
            "INSERT INTO sites(id,name) VALUES(?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
            (site_id, name),
        )
        await self.conn.commit()

    async def touch_site(self, site_id: str, ts: float) -> None:
        await self.conn.execute("UPDATE sites SET last_seen=? WHERE id=?", (ts, site_id))
        await self.conn.commit()

    async def set_site_meta(self, site_id: str, meta: dict) -> None:
        await self.conn.execute(
            "UPDATE sites SET meta=? WHERE id=?", (json.dumps(meta), site_id)
        )
        await self.conn.commit()

    async def list_sites(self) -> list[dict]:
        cur = await self.conn.execute("SELECT id,name,last_seen,meta FROM sites ORDER BY name")
        out = []
        for r in await cur.fetchall():
            out.append(
                {"id": r["id"], "name": r["name"], "last_seen": r["last_seen"],
                 "meta": json.loads(r["meta"] or "{}")}
            )
        return out

    # ---- devices, entities, fixed values -----------------------------------
    async def replace_config(self, site_id: str, instances: Sequence[dict]) -> dict[str, list[str]]:
        """Make the stored instances of a site match `instances` (the protocol `config` message).

        Each instance is {id, device, name, sensors: {sensor_id: {"source": str} | {"fixed": float}}}.
        Instances and sensors that are gone are deleted together with their history. Returns
        {"instances": [...removed instance ids], "sensors": [...removed "instance.sensor" of
        instances that still exist]}.
        """
        cur = await self.conn.execute("SELECT instance FROM devices WHERE site_id=?", (site_id,))
        old_instances = {r["instance"] for r in await cur.fetchall()}
        cur = await self.conn.execute("SELECT key, instance, sensor_id FROM entities WHERE site_id=?", (site_id,))
        old_entities = {r["key"]: r["instance"] for r in await cur.fetchall()}
        cur = await self.conn.execute(
            "SELECT instance, sensor_id FROM fixed_values WHERE site_id=?", (site_id,))
        old_fixed = {f"{r['instance']}.{r['sensor_id']}": r["instance"] for r in await cur.fetchall()}

        new_instances = {i["id"] for i in instances}
        new_keys: set[str] = set()          # every sensor entry (source or fixed)
        new_source_keys: set[str] = set()   # entries that have a source, i.e. that carry data
        for inst in instances:
            for sid, entry in inst["sensors"].items():
                new_keys.add(f"{inst['id']}.{sid}")
                if "source" in entry:
                    new_source_keys.add(f"{inst['id']}.{sid}")

        removed_instances = sorted(old_instances - new_instances)
        removed_sensors = sorted(
            k for k, inst in {**old_entities, **old_fixed}.items()
            if k not in new_keys and inst in new_instances)

        for key in [k for k in old_entities if k not in new_source_keys]:
            await self.conn.execute("DELETE FROM entities WHERE site_id=? AND key=?", (site_id, key))
            await self.conn.execute("DELETE FROM buckets WHERE site_id=? AND key=?", (site_id, key))
        await self.conn.execute("DELETE FROM fixed_values WHERE site_id=?", (site_id,))
        for inst_id in removed_instances:
            await self.conn.execute("DELETE FROM devices WHERE site_id=? AND instance=?", (site_id, inst_id))

        for inst in instances:
            await self.conn.execute(
                "INSERT INTO devices(site_id,instance,device,name) VALUES(?,?,?,?) "
                "ON CONFLICT(site_id,instance) DO UPDATE SET device=excluded.device,name=excluded.name",
                (site_id, inst["id"], inst["device"], inst["name"]))
            for sid, entry in inst["sensors"].items():
                key = f"{inst['id']}.{sid}"
                if "source" in entry:
                    await self.conn.execute(
                        "INSERT INTO entities(site_id,key,instance,sensor_id,source) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(site_id,key) DO UPDATE SET source=excluded.source",
                        (site_id, key, inst["id"], sid, entry["source"]))
                else:
                    await self.conn.execute(
                        "INSERT INTO fixed_values(site_id,instance,sensor_id,value) VALUES(?,?,?,?)",
                        (site_id, inst["id"], sid, float(entry["fixed"])))
        await self.conn.commit()
        return {"instances": removed_instances, "sensors": removed_sensors}

    async def list_instances(self, site_id: str) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT instance, device, name FROM devices WHERE site_id=? ORDER BY name, instance", (site_id,))
        return [dict(r) for r in await cur.fetchall()]

    async def list_entities(self, site_id: str) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT key, instance, sensor_id, source FROM entities WHERE site_id=? ORDER BY key", (site_id,))
        return [dict(r) for r in await cur.fetchall()]

    async def list_fixed(self, site_id: str) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT instance, sensor_id, value FROM fixed_values WHERE site_id=? ORDER BY instance, sensor_id",
            (site_id,))
        return [dict(r) for r in await cur.fetchall()]

    # ---- buckets -------------------------------------------------------
    async def upsert_buckets(self, rows: Iterable[BucketRow]) -> None:
        await self.conn.executemany(_UPSERT, list(rows))
        await self.conn.commit()

    async def latest_lasts(self) -> list[tuple[str, str, int, float]]:
        cur = await self.conn.execute(
            "SELECT site_id,key,MAX(ts_min) AS t,last FROM buckets "
            "WHERE last IS NOT NULL GROUP BY site_id,key")
        return [(r["site_id"], r["key"], r["t"], r["last"]) for r in await cur.fetchall()]

    async def fetch_series(self, site_id: str, start: int, end: int, step: int,
                           keys: Sequence[str] | None = None) -> list[dict]:
        sql = ("SELECT key,(ts_min/?)*? AS t,SUM(delta) AS delta,SUM(avg_sum) AS avg_sum,"
               "SUM(n) AS n,MIN(min) AS min,MAX(max) AS max FROM buckets "
               "WHERE site_id=? AND ts_min>=? AND ts_min<?")
        args: list = [step, step, site_id, start, end]
        if keys is not None:
            if not keys:
                return []
            sql += " AND key IN (%s)" % ",".join("?" * len(keys))
            args += list(keys)
        sql += " GROUP BY key,t ORDER BY key,t"
        cur = await self.conn.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]

    async def totals(self, site_id: str, start: int, end: int) -> dict[str, float]:
        cur = await self.conn.execute(
            "SELECT key,SUM(delta) AS d FROM buckets "
            "WHERE site_id=? AND ts_min>=? AND ts_min<? GROUP BY key",
            (site_id, start, end))
        return {r["key"]: r["d"] or 0.0 for r in await cur.fetchall()}

    async def last_in_range(self, site_id: str, key: str, start: int, end: int) -> float | None:
        cur = await self.conn.execute(
            "SELECT last FROM buckets WHERE site_id=? AND key=? AND ts_min>=? AND ts_min<? "
            "ORDER BY ts_min DESC LIMIT 1", (site_id, key, start, end))
        r = await cur.fetchone()
        return r["last"] if r else None

    async def delete_older(self, cutoff: int, batch: int = 5000) -> int:
        cur = await self.conn.execute(
            "DELETE FROM buckets WHERE (site_id,key,ts_min) IN "
            "(SELECT site_id,key,ts_min FROM buckets WHERE ts_min<? LIMIT ?)",
            (cutoff, batch))
        await self.conn.commit()
        return cur.rowcount
