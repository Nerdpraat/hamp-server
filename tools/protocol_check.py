#!/usr/bin/env python3
"""Conformance check for an Energy Hub server (docs/protocol.md, version 2).

Runs scripted scenarios against a server and validates every message it sends against the JSON
Schemas in docs/protocol/schemas.

    python tools/protocol_check.py ws://localhost:8000/ws/ingest <site key> --yes

WARNING: the check replaces the site's configuration (and with it the stored history of that
site's instances). Use a test site or a development server. Pass --yes to confirm.

Needs: websockets, jsonschema.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

import protocol_schemas as schemas


class Failure(Exception):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


class Session:
    """One websocket connection; every server message is validated against the schemas."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.hello: dict = {}

    @classmethod
    async def open(cls, url: str, key: str | None, hello: bool = True) -> "Session":
        headers = {"Authorization": f"Bearer {key}"} if key is not None else {}
        s = cls(await websockets.connect(url, additional_headers=headers, max_size=None))
        if hello:
            s.hello = await s.recv()
            expect(s.hello.get("type") == "hello", f"first message must be hello, got {s.hello.get('type')}")
        return s

    async def recv(self, timeout: float = 5) -> dict:
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        msg = json.loads(raw)
        problems = schemas.errors(msg, "server_message")
        expect(not problems, f"server message violates the schema: {problems} in {str(msg)[:300]}")
        return msg

    async def send(self, msg: object) -> None:
        await self.ws.send(msg if isinstance(msg, str) else json.dumps(msg))

    async def call(self, msg: object) -> dict:
        await self.send(msg)
        return await self.recv()

    async def close(self) -> None:
        await self.ws.close()

    async def closed_with(self, timeout: float = 5) -> int | None:
        """Wait for the server to close the connection and return the close code."""
        try:
            while True:
                await asyncio.wait_for(self.ws.recv(), timeout)
        except ConnectionClosed as err:
            return err.rcvd.code if err.rcvd else None


def is_static(sensor: dict) -> bool:
    """A static slot has a number, select or boolean selector: the client sends the value itself."""
    return "entity" not in sensor["selector"]


def sample_instances(hello: dict, count: int = 3) -> tuple[list[dict], dict[str, str]]:
    """Instances built from the server's own catalogue: the first two entity sensors of the first devices."""
    instances, kinds = [], {}
    for dev in hello["devices"]:
        entity_sensors = [s for s in dev["sensors"] if not is_static(s)][:2]
        if not entity_sensors:
            continue
        if len(instances) >= count:
            break
        sensors = {s["id"]: {"source": f"check.{dev['id']}.{s['id']}"} for s in entity_sensors}
        iid = f"check-{dev['id']}"[:40].replace("_", "-")
        instances.append({"id": iid, "device": dev["id"], "name": f"Check {dev['name']}", "sensors": sensors})
        for s in entity_sensors:
            kinds[f"{iid}.{s['id']}"] = s["kind"]
    return instances, kinds


def sample_static(selector: dict) -> tuple[list[object], list[object]]:
    """(valid values, invalid values) for a static selector, derived from the selector alone."""
    (kind, cfg), = selector.items()
    if kind == "number":
        lo, hi = cfg.get("min"), cfg.get("max")
        good = [v for v in (lo, hi, ((lo or 0) + (hi if hi is not None else (lo or 0) + 10)) / 2) if v is not None]
        bad: list[object] = ["12", True, None]
        if hi is not None:
            bad.append(hi + 1)
        if lo is not None:
            bad.append(lo - 1)
        return good, bad
    if kind == "select":
        values = [o if isinstance(o, str) else o["value"] for o in cfg["options"]]
        return values, [1, True] + ([] if cfg.get("custom_value") else ["not-an-option-" + "x" * 5])
    return [True, False], [0, 1, "true", None]


@dataclass
class Ctx:
    url: str
    key: str
    skip_rate_limit: bool


# ---- scenarios ---------------------------------------------------------------------------------
async def authentication_is_required(ctx: Ctx) -> None:
    for label, key in (("missing key", None), ("wrong key", "definitely-not-the-key")):
        try:
            s = await Session.open(ctx.url, key, hello=False)
        except InvalidStatus as err:
            expect(err.response.status_code == 403, f"{label}: expected HTTP 403, got {err.response.status_code}")
        else:
            await s.close()
            raise Failure(f"{label}: the server upgraded the connection")


async def hello_describes_the_server(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        h = s.hello
        expect(h["version"] == 2, f"version is {h['version']}")
        expect(abs(h["server_time"] - time.time()) < 300, "server_time is more than 5 minutes off")
        expect(h["devices"], "the catalogue is empty")
        ids = [d["id"] for d in h["devices"]]
        expect(len(ids) == len(set(ids)), "duplicate device ids in the catalogue")
        for d in h["devices"]:
            sids = [x["id"] for x in d["sensors"]]
            expect(len(sids) == len(set(sids)), f"duplicate sensor ids in device {d['id']}")
    finally:
        await s.close()


async def config_and_states_roundtrip(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        instances, kinds = sample_instances(s.hello)
        ack = await s.call({"type": "config", "currency": "EUR", "instances": instances})
        expect(ack.get("for") == "config", f"expected a config ack, got {ack}")
        expect(ack["instances"] == len(instances), "ack.instances does not match")
        expect(ack["sensors"] == len(kinds), "ack.sensors does not match")
        now = time.time()
        items = [{"key": k, "ts": now, "value": 1.5} for k in kinds]
        ack = await s.call({"type": "states", "items": items})
        expect(ack == {"type": "ack", "for": "states", "accepted": len(items), "rejected": 0}, f"states ack: {ack}")
        # without a ts the server clock is used
        ack = await s.call({"type": "states", "items": [{"key": next(iter(kinds)), "value": 2}]})
        expect(ack["accepted"] == 1, "a reading without ts must be accepted")
    finally:
        await s.close()


async def static_values_are_validated_and_acknowledged(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        statics = [(d, x) for d in s.hello["devices"] for x in d["sensors"] if is_static(x)]
        if not statics:
            print("      (the catalogue has no static slots: nothing to check)")
            return
        instances, sensors, keys = [], {}, []
        for dev, sensor in statics:
            iid = f"check-{dev['id']}"[:40].replace("_", "-") + "-static"
            instances.append({"id": iid, "device": dev["id"], "name": f"Check {dev['name']}",
                              "sensors": {sensor["id"]: {"source": "static"}}})
            keys.append((f"{iid}.{sensor['id']}", sensor))
        ack = await s.call({"type": "config", "instances": instances})
        expect(ack.get("for") == "config" and ack["sensors"] == len(keys), f"static config not accepted: {ack}")
        for key, sensor in keys:
            good, bad = sample_static(sensor["selector"])
            for value in good:
                ack = await s.call({"type": "states", "items": [{"key": key, "value": value}]})
                expect(ack == {"type": "ack", "for": "states", "accepted": 1, "rejected": 0},
                       f"{key}: the valid value {value!r} must be accepted, got {ack}")
            ack = await s.call({"type": "states", "items": [{"key": key, "value": good[0]}] * 2})
            expect(ack["accepted"] == 2, f"{key}: repeating a value must be accepted, got {ack}")
            for value in bad:
                ack = await s.call({"type": "states", "items": [{"key": key, "value": value}]})
                expect(ack == {"type": "ack", "for": "states", "accepted": 0, "rejected": 1},
                       f"{key}: the invalid value {value!r} must be rejected, got {ack}")
        expect((await s.call({"type": "ping"})) == {"type": "pong"}, "the connection must stay open")
        entity = next((d for d in s.hello["devices"] for x in d["sensors"] if not is_static(x)), None)
        if entity is not None:
            sid = next(x["id"] for x in entity["sensors"] if not is_static(x))
            err = await s.call({"type": "config", "instances": [{"id": "check-wrong-marker", "device": entity["id"],
                                                                  "name": "x", "sensors": {sid: {"source": "static"}}}]})
            expect(err.get("code") == "invalid_config", f"'static' on an entity slot must be refused, got {err}")
    finally:
        await s.close()


async def bad_readings_are_rejected_not_fatal(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        instances, kinds = sample_instances(s.hello)
        await s.call({"type": "config", "instances": instances})
        key = next(iter(kinds))
        now = time.time()
        ack = await s.call({"type": "states", "items": [
            {"key": key, "ts": now, "value": "12"},               # a string is not a number
            {"key": key, "ts": now, "value": True},               # neither is a boolean
            {"key": key, "ts": now - 7200, "value": 1},           # more than an hour old
            {"key": key, "ts": now + 3600, "value": 1},           # more than a minute ahead
            {"key": "check-nothing.nothing", "ts": now, "value": 1},
            {"key": key, "ts": now, "value": 3},                  # the only good one
        ]})
        expect(ack["accepted"] == 1 and ack["rejected"] == 5, f"expected 1 accepted and 5 rejected, got {ack}")
        expect(ack.get("unknown_keys") == ["check-nothing.nothing"], f"unknown_keys: {ack.get('unknown_keys')}")
        expect((await s.call({"type": "ping"})) == {"type": "pong"}, "the connection must stay open")
    finally:
        await s.close()


async def invalid_configs_are_refused_with_a_path(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        instances, _ = sample_instances(s.hello)
        good = instances[0]
        cases = {
            "unknown device": ({**good, "device": "no_such_device"}, "instances[0].device"),
            "unknown sensor": ({**good, "sensors": {"no_such_sensor": {"source": "x"}}},
                               "instances[0].sensors.no_such_sensor"),
            "bad instance id": ({**good, "id": "Not Valid"}, "instances[0].id"),
            "no sensors": ({**good, "sensors": {}}, "instances[0].sensors"),
            "empty name": ({**good, "name": ""}, "instances[0].name"),
        }
        for label, (instance, path) in cases.items():
            err = await s.call({"type": "config", "instances": [instance]})
            expect(err.get("code") == "invalid_config", f"{label}: expected invalid_config, got {err}")
            expect(err.get("path") == path, f"{label}: expected path {path}, got {err.get('path')}")
        dup = await s.call({"type": "config", "instances": [good, good]})
        expect(dup.get("code") == "invalid_config" and dup.get("path") == "instances[1].id",
               f"duplicate instance ids: {dup}")
        expect((await s.call({"type": "ping"})) == {"type": "pong"}, "the connection must stay open")
        # a rejected config leaves the previous one in force
        key = next(f"{good['id']}.{sid}" for sid in good["sensors"])
        await s.call({"type": "config", "instances": instances})
        refused = await s.call({"type": "config", "instances": [{**good, "device": "no_such_device"}]})
        expect(refused.get("code") == "invalid_config", f"expected invalid_config, got {refused}")
        ack = await s.call({"type": "states", "items": [{"key": key, "value": 1}]})
        expect(ack["accepted"] == 1, "a rejected config must leave the previous one in force")
    finally:
        await s.close()


async def malformed_messages_get_error_codes(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        for payload, code in (("this is not json", "bad_json"), ("[1, 2]", "bad_message"),
                              ('{"type": "teapot"}', "unknown_type"),
                              ('{"type": "states", "items": "nope"}', "bad_states")):
            err = await s.call(payload)
            expect(err.get("code") == code, f"{payload!r}: expected {code}, got {err}")
        expect((await s.call({"type": "ping"})) == {"type": "pong"}, "the connection must stay open")
    finally:
        await s.close()


async def unknown_fields_are_ignored(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        instances, kinds = sample_instances(s.hello)
        instances[0] = {**instances[0], "colour": "red"}
        ack = await s.call({"type": "config", "instances": instances, "future": {"x": 1}})
        expect(ack.get("for") == "config", f"unknown fields must be ignored, got {ack}")
        key = next(iter(kinds))
        ack = await s.call({"type": "states", "extra": 1, "items": [{"key": key, "value": 1, "unit": "W"}]})
        expect(ack["accepted"] == 1, "unknown fields in readings must be ignored")
    finally:
        await s.close()


async def a_second_connection_replaces_the_first(ctx: Ctx) -> None:
    first = await Session.open(ctx.url, ctx.key)
    second = await Session.open(ctx.url, ctx.key)
    try:
        code = await first.closed_with()
        expect(code == 4000, f"the replaced connection must close with 4000, got {code}")
        expect((await second.call({"type": "ping"})) == {"type": "pong"}, "the new connection must work")
    finally:
        await second.close()


async def oversized_frames_are_refused(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        limit = s.hello["limits"]["max_message_bytes"]
        await s.send("x" * (limit + 1024))
        code = await s.closed_with()
        expect(code == 1009, f"an oversized frame must close the connection with 1009, got {code}")
    finally:
        try:
            await s.close()
        except Exception:  # noqa: BLE001
            pass


async def flooding_is_rate_limited(ctx: Ctx) -> None:
    s = await Session.open(ctx.url, ctx.key)
    try:
        limit = s.hello["limits"]["max_messages_per_second"]
        for _ in range(limit + 20):
            await s.send({"type": "ping"})
        seen_error = False
        try:
            while True:
                msg = await s.recv()
                if msg.get("code") == "rate_limited":
                    seen_error = True
        except ConnectionClosed as err:
            code = err.rcvd.code if err.rcvd else None
            expect(code == 4429, f"flooding must close the connection with 4429, got {code}")
        expect(seen_error, "flooding must produce an error with code rate_limited first")
    finally:
        try:
            await s.close()
        except Exception:  # noqa: BLE001
            pass


SCENARIOS: list[Callable[[Ctx], Awaitable[None]]] = [
    authentication_is_required,
    hello_describes_the_server,
    config_and_states_roundtrip,
    static_values_are_validated_and_acknowledged,
    bad_readings_are_rejected_not_fatal,
    invalid_configs_are_refused_with_a_path,
    malformed_messages_get_error_codes,
    unknown_fields_are_ignored,
    a_second_connection_replaces_the_first,
    oversized_frames_are_refused,
    flooding_is_rate_limited,
]


async def run(url: str, key: str, skip_rate_limit: bool = False) -> int:
    ctx = Ctx(url, key, skip_rate_limit)
    failed = passed = 0
    for scenario in SCENARIOS:
        name = scenario.__name__.replace("_", " ")
        if skip_rate_limit and scenario is flooding_is_rate_limited:
            print(f"SKIP  {name}")
            continue
        try:
            await scenario(ctx)
            passed += 1
            print(f"PASS  {name}")
        except Failure as err:
            failed += 1
            print(f"FAIL  {name}: {err}")
        except Exception as err:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(err).__name__}: {err}")
        await asyncio.sleep(1.1)        # let the rate limit window of the last scenario pass
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="for example ws://localhost:8000/ws/ingest")
    p.add_argument("key", help="a site key")
    p.add_argument("--yes", action="store_true", help="confirm that this site's config may be replaced")
    p.add_argument("--skip-rate-limit", action="store_true", help="do not flood the server")
    a = p.parse_args()
    if not a.yes:
        raise SystemExit("This check replaces the site's configuration and deletes its history.\n"
                         "Use a test site and pass --yes to confirm.")
    sys.exit(asyncio.run(run(a.url, a.key, a.skip_rate_limit)))


if __name__ == "__main__":
    main()
