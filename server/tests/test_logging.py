import json
import logging

from app.main import JsonFormatter


def test_json_formatter_outputs_one_object_with_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = logging.LogRecord("energy_hub.x", logging.ERROR, __file__, 1, "failed %s", ("now",), sys.exc_info())
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "ERROR" and out["msg"] == "failed now" and "boom" in out["exc"]
