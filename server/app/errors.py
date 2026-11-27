"""Errors shared by the configuration modules."""
from __future__ import annotations

from pydantic import ValidationError


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid. The message is meant for the operator."""


def validation_message(what: str, err: ValidationError) -> str:
    """Turn a pydantic error into a short, readable list with field paths."""
    lines = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        msg = e["msg"].removeprefix("Value error, ")
        lines.append(f"  {loc or '<root>'}: {msg}")
    return f"Invalid {what}:\n" + "\n".join(lines)
