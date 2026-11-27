import pytest

from app.aggregator import Aggregator
from app.db import Database

T0 = 1_800_000_000  # divisible by 60


class Clock:
    def __init__(self, t=T0 + 1000):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "a.db"))
    await d.connect()
    yield d
    await d.close()


async def rows(db):
    cur = await db.conn.execute("SELECT * FROM buckets ORDER BY key, ts_min")
    return [dict(r) for r in await cur.fetchall()]


async def test_counter_deltas_and_buckets(db):
    agg = Aggregator(db, Clock())
    for ts, v in [(T0, 10.0), (T0 + 30, 10.5), (T0 + 61, 11.0)]:
        assert agg.add("s", "k", "counter", ts, v)
    await agg.flush()
    r = await rows(db)
    assert [x["ts_min"] for x in r] == [T0, T0 + 60]
    assert r[0]["delta"] == pytest.approx(0.5)   # first sample has no delta
    assert r[1]["delta"] == pytest.approx(0.5)
    assert r[1]["last"] == 11.0


async def test_counter_reset(db):
    agg = Aggregator(db, Clock())
    agg.add("s", "k", "counter", T0, 100.0)
    agg.add("s", "k", "counter", T0 + 10, 2.0)  # drop => reset, delta = new value
    await agg.flush()
    assert (await rows(db))[0]["delta"] == pytest.approx(2.0)


async def test_counter_small_dip_is_not_a_reset(db):
    agg = Aggregator(db, Clock())
    agg.add("s", "k", "counter", T0, 1092.8454241)
    agg.add("s", "k", "counter", T0 + 10, 1092.8454)   # rounding noise
    agg.add("s", "k", "counter", T0 + 20, 1092.9454)
    await agg.flush()
    assert (await rows(db))[0]["delta"] == pytest.approx(0.1, abs=1e-6)


async def test_counter_out_of_order_dropped(db):
    agg = Aggregator(db, Clock())
    agg.add("s", "k", "counter", T0 + 10, 5.0)
    assert not agg.add("s", "k", "counter", T0 + 5, 6.0)


async def test_gauge_stats(db):
    agg = Aggregator(db, Clock())
    for ts, v in [(T0, 2.0), (T0 + 10, 6.0), (T0 + 20, 4.0)]:
        agg.add("s", "g", "gauge", ts, v)
    await agg.flush()
    r = (await rows(db))[0]
    assert (r["min"], r["max"], r["n"], r["last"]) == (2.0, 6.0, 3, 4.0)
    assert r["avg_sum"] / r["n"] == pytest.approx(4.0)


async def test_drop_future_old_and_nonfinite(db):
    clock = Clock()
    agg = Aggregator(db, clock)
    assert not agg.add("s", "g", "gauge", clock.t + 3600, 1.0)
    assert not agg.add("s", "g", "gauge", clock.t - 7200, 1.0)
    assert not agg.add("s", "g", "gauge", clock.t, float("nan"))
    assert not agg.add("s", "g", "gauge", clock.t, float("inf"))
    assert await agg.flush() == 0


async def test_double_flush_merges(db):
    agg = Aggregator(db, Clock())
    agg.add("s", "k", "counter", T0, 1.0)
    agg.add("s", "k", "counter", T0 + 10, 2.0)
    await agg.flush()
    agg.add("s", "k", "counter", T0 + 20, 4.0)
    await agg.flush()
    r = await rows(db)
    assert len(r) == 1
    assert r[0]["delta"] == pytest.approx(3.0)
    assert r[0]["last"] == 4.0 and r[0]["n"] == 3


async def test_gauge_merge_min_max(db):
    agg = Aggregator(db, Clock())
    agg.add("s", "g", "gauge", T0, 5.0)
    await agg.flush()
    agg.add("s", "g", "gauge", T0 + 10, 1.0)
    agg.add("s", "g", "gauge", T0 + 20, 9.0)
    await agg.flush()
    r = (await rows(db))[0]
    assert (r["min"], r["max"], r["n"]) == (1.0, 9.0, 3)


async def test_large_gap_gives_no_delta(db):
    clock = Clock()
    agg = Aggregator(db, clock, max_age=10_000_000)
    agg.add("s", "k", "counter", T0, 1.0)
    agg.add("s", "k", "counter", T0 + 7200, 50.0)
    await agg.flush()
    assert sum(r["delta"] for r in await rows(db)) == 0


async def test_seed_after_restart(db):
    agg = Aggregator(db, Clock())
    agg.add("s", "k", "counter", T0, 10.0)
    await agg.flush()
    agg2 = Aggregator(db, Clock())
    await agg2.seed()
    agg2.add("s", "k", "counter", T0 + 30, 10.4)
    await agg2.flush()
    assert (await rows(db))[0]["delta"] == pytest.approx(0.4)


async def test_flush_on_stop(db):
    agg = Aggregator(db, Clock())
    agg.add("s", "k", "gauge", T0, 1.0)
    await agg.stop()
    assert len(await rows(db)) == 1


async def test_flush_failure_keeps_data(db, monkeypatch):
    agg = Aggregator(db, Clock())
    agg.add("s", "k", "gauge", T0, 1.0)
    orig = db.upsert_buckets

    async def boom(_):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "upsert_buckets", boom)
    assert await agg.flush() == 0
    monkeypatch.setattr(db, "upsert_buckets", orig)
    assert await agg.flush() == 1
    assert (await rows(db))[0]["n"] == 1
