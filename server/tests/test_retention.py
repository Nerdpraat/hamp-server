import pytest

from app.db import Database
from app.retention import purge

NOW = 1_800_000_000


@pytest.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "r.db"))
    await d.connect()
    yield d
    await d.close()


def row(key, ts):
    return ("s", key, ts, 1.0, 1.0, None, 0.0, None, 1)


async def count(db):
    cur = await db.conn.execute("SELECT COUNT(*) FROM buckets")
    return (await cur.fetchone())[0]


async def test_purge_removes_only_old(db):
    day = 86400
    await db.upsert_buckets([
        row("k", NOW - 8 * day), row("k", NOW - 7 * day - 120), row("k", NOW - 6 * day),
        row("k", NOW - 60),
    ])
    deleted = await purge(db, 7, now=NOW)
    assert deleted == 2
    assert await count(db) == 2


async def test_purge_configurable(db):
    day = 86400
    await db.upsert_buckets([row("k", NOW - 2 * day), row("k", NOW - 60)])
    assert await purge(db, 1, now=NOW) == 1
    assert await count(db) == 1


async def test_purge_batches(db):
    await db.upsert_buckets([row("k", NOW - 10 * 86400 + i * 60) for i in range(25)])
    assert await purge(db, 7, now=NOW, batch=10) == 25
    assert await count(db) == 0
