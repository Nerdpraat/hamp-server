"""Load the protocol JSON Schemas and validate messages against them.

Used by the tests, by tools/protocol_check.py and by anyone who wants to check a client.
Needs the `jsonschema` package.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "docs" / "protocol" / "schemas"

SERVER_TYPES = {"hello", "ack", "error", "pong"}
CLIENT_TYPES = {"config", "states", "ping"}


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resources.append((schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012)))
        # Relative references ("hello.schema.json") resolve against the $id's directory, so the
        # id URL is enough. Also register under the bare file name for convenience.
        resources.append((path.name, Resource.from_contents(schema, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    """Validator for e.g. "hello", "config", "server_message"."""
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=_registry())


def errors(message: object, name: str) -> list[str]:
    """Human readable validation errors (empty list when the message is valid)."""
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in validator(name).iter_errors(message)]


def validate(message: object, name: str) -> None:
    problems = errors(message, name)
    if problems:
        raise ValueError(f"message does not match {name}.schema.json: " + "; ".join(problems))


def validate_server_message(message: object) -> None:
    validate(message, "server_message")


def validate_client_message(message: object) -> None:
    validate(message, "client_message")
