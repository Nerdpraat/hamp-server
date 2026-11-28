"""The protocol document, schemas and examples must agree with each other."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import protocol_schemas as ps  # noqa: E402

DOC = ROOT / "docs" / "protocol.md"
EXAMPLES = ROOT / "docs" / "protocol" / "examples"


def doc_messages():
    """Every complete message in a ```json block of the document."""
    for block in re.findall(r"```json\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S):
        try:
            yield json.loads(block)
        except ValueError:                       # several one-line messages in one block
            for line in block.splitlines():
                if line.strip():
                    yield json.loads(line)


def test_schemas_are_valid_json_schemas():
    from jsonschema import Draft202012Validator
    for path in ps.SCHEMA_DIR.glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_every_message_in_the_document_validates():
    seen = set()
    for msg in doc_messages():
        kind = msg["type"]
        if kind in ps.SERVER_TYPES:
            ps.validate_server_message(msg)
        else:
            assert kind in ps.CLIENT_TYPES, msg
            ps.validate_client_message(msg)
        seen.add(kind)
    assert seen == ps.SERVER_TYPES | ps.CLIENT_TYPES     # every message type has a sample


@pytest.mark.parametrize("name", ["happy-path", "rejected-config", "reconnect"])
def test_example_conversations_validate(name):
    example = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    messages = [s for s in example["steps"] if "message" in s]
    assert messages
    for step in messages:
        if step["direction"] == "server->client":
            ps.validate_server_message(step["message"])
        else:
            ps.validate_client_message(step["message"])


def test_invalid_examples_do_not_validate():
    cases = json.loads((EXAMPLES / "invalid-messages.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 10
    for case in cases:
        assert ps.errors(case["message"], case["schema"]), f"should be invalid: {case['why']}"


def test_server_and_client_unions_pick_the_right_branch():
    assert not ps.errors({"type": "pong"}, "server_message")
    assert ps.errors({"type": "states", "items": []}, "server_message")   # a client message
    assert ps.errors({"type": "hello"}, "client_message")                  # a server message
    assert ps.errors({"type": "bogus"}, "client_message")


def test_document_states_the_key_facts():
    text = DOC.read_text(encoding="utf-8")
    for fact in ("403", "4000", "4429", "1009", "<instance id>.<sensor id>", "version 2",
                 "deleted together with their stored history", "90%"):
        assert fact in text, fact


@pytest.mark.parametrize("value", [13.5, 0, -1, "auto", "", True, False])
def test_states_values_may_be_a_number_a_string_or_a_boolean(value):
    ps.validate_client_message({"type": "states", "items": [{"key": "battery-1.capacity", "value": value}]})


@pytest.mark.parametrize("value", [None, [1], {"a": 1}])
def test_other_states_value_types_are_invalid(value):
    assert ps.errors({"type": "states", "items": [{"key": "a.b", "value": value}]}, "states")


def test_config_registers_a_static_slot_with_the_source_marker():
    cfg = {"type": "config", "instances": [{"id": "b", "device": "battery", "name": "B",
                                            "sensors": {"capacity": {"source": "static"}}}]}
    ps.validate_client_message(cfg)
    cfg["instances"][0]["sensors"]["capacity"] = {"fixed": 1}
    assert ps.errors(cfg, "config")


@pytest.mark.parametrize("selector", [
    {"entity": {"domain": "sensor"}}, {"entity": {}}, {"number": {"min": 0, "step": "any"}},
    {"select": {"options": ["a", {"value": "b", "label": "B"}], "custom_value": True}}, {"boolean": {}},
])
def test_hello_accepts_every_native_selector_type(selector):
    sensor = {"id": "s", "name": "S", "description": "", "kind": "gauge", "unit": "", "selector": selector}
    hello = {"type": "hello", "version": 2, "site": {"id": "a", "name": "A"}, "server_time": 1,
             "limits": {"max_message_bytes": 1, "max_items_per_batch": 1, "max_messages_per_second": 1,
                        "max_instances": 1},
             "devices": [{"id": "d", "name": "D", "description": "", "category": "x", "sensors": [sensor]}]}
    ps.validate_server_message(hello)
