import logging
import sqlite3

import pytest

from app.db import SCHEMA_VERSION, Database


async def tables(db):
    cur = await db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r["name"] for r in await cur.fetchall()]


async def test_fresh_database_gets_the_current_schema(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    await db.connect()
    try:
        assert await tables(db) == ["buckets", "devices", "entities", "fixed_values", "sites"]
        cur = await db.conn.execute("PRAGMA user_version")
        assert (await cur.fetchone())[0] == SCHEMA_VERSION
    finally:
        await db.close()


async def test_data_survives_a_reconnect(tmp_path):
    path = str(tmp_path / "a.db")
    db = Database(path)
    await db.connect()
    await db.upsert_site("home", "Home")
    await db.replace_config("home", [{"id": "b1", "device": "battery", "name": "B", "sensors": {"power": {"source": "x"}}}])
    await db.close()
    db = Database(path)
    await db.connect()
    try:
        assert [i["instance"] for i in await db.list_instances("home")] == ["b1"]
    finally:
        await db.close()


async def test_a_database_of_another_layout_is_reset_not_migrated(tmp_path, caplog):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE entities(site_id TEXT, key TEXT, type TEXT);"
                      "INSERT INTO entities VALUES('home','k','grid_import');"
                      "CREATE TABLE sites(id TEXT PRIMARY KEY, name TEXT);"
                      "PRAGMA user_version=1;")
    con.commit()
    con.close()
    db = Database(str(path))
    with caplog.at_level(logging.WARNING):
        await db.connect()
    try:
        assert "resetting it" in caplog.text
        cur = await db.conn.execute("PRAGMA table_info(entities)")
        assert [r["name"] for r in await cur.fetchall()] == ["site_id", "key", "instance", "sensor_id", "source"]
        assert await db.list_entities("home") == []
    finally:
        await db.close()
