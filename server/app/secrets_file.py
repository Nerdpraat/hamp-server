"""auth.yaml: read the secrets and generate the ones that are empty or missing.

Generated values are written back into the file with line-level edits, so comments and the order
of entries survive. The write is atomic and the file keeps (or gets) mode 0600. Secret values are
never logged, only the names of the entries that were generated.
"""
from __future__ import annotations

import errno
import logging
import os
import re
import secrets as _secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

log = logging.getLogger("energy_hub.auth")

MIN_KEY = 16
MIN_UI_KEY = 8
MIN_SESSION_SECRET = 16
# token_urlsafe(n) gives about 1.33 n characters
KEY_BYTES = 24
SESSION_BYTES = 48

TEMPLATE_HEAD = """\
# auth.yaml: every secret of this server lives in this file. Keep it private.
# Leave a value empty ("") and the server generates a random one when it starts and writes it here.
#
#   ui_key          the shared key you type to open the dashboard
#   session_secret  signs the dashboard session cookies (changing it logs everybody out)
#   keys            one key per site, referenced by name from topology.yaml. Give a site's key to
#                   the Home Assistant integration (or any other client) of that site.
ui_key: ""
session_secret: ""
keys:
"""


@dataclass(frozen=True)
class Secrets:
    ui_key: str
    session_secret: str
    keys: dict[str, str]


def _template(key_names: list[str]) -> str:
    return TEMPLATE_HEAD + "".join(f'  {name}: ""\n' for name in key_names)


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _line_pattern(name: str, indent: str) -> re.Pattern[str]:
    """A `name: ""` line (empty, null or ~ value, optional trailing comment)."""
    q = re.escape(name)
    return re.compile(
        rf"^(?P<head>{indent}(?P<q>[\"']?){q}(?P=q)\s*:\s*)(?P<val>\"\"|''|~|null)?(?P<tail>\s*(#.*)?)$")


def _set_top_level(lines: list[str], name: str, value: str) -> None:
    pat = _line_pattern(name, "")
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            tail = m.group("tail")
            lines[i] = f'{m.group("head").rstrip()} "{value}"{tail if tail.strip() else ""}'
            return
    # missing: put it before the `keys:` section, or at the end
    for i, line in enumerate(lines):
        if re.match(r"^keys\s*:", line):
            lines.insert(i, f'{name}: "{value}"')
            return
    lines.append(f'{name}: "{value}"')


def _set_key(lines: list[str], name: str, value: str) -> None:
    pat = _line_pattern(name, r"[ \t]+")
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            tail = m.group("tail")
            lines[i] = f'{m.group("head").rstrip()} "{value}"{tail if tail.strip() else ""}'
            return
    # add a new entry at the end of the `keys:` block
    start = next((i for i, line in enumerate(lines) if re.match(r"^keys\s*:\s*(#.*)?$", line)), None)
    if start is None:
        if any(re.match(r"^keys\s*:", line) for line in lines):
            raise ConfigError("auth.yaml: write 'keys:' as a block (one 'name: value' per line) "
                              "so the server can add generated keys to it")
        lines.extend(["keys:", f'  {name}: "{value}"'])
        return
    indent, end = "  ", start + 1
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped and not stripped.startswith("#"):
            if not lines[j].startswith((" ", "\t")):
                break                                  # next top-level entry
            indent = re.match(r"[ \t]*", lines[j]).group(0)
        end = j + 1 if stripped else end
    lines.insert(end, f'{indent}{name}: "{value}"')


def _write(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".auth-", suffix=".tmp")
    except OSError as err:
        raise ConfigError(
            f"Cannot write {path}: {err.strerror or err}. The server generates missing secrets "
            "and stores them there, so the config directory must be writable (or fill in every "
            "secret yourself).") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.replace(tmp, path)
        except OSError as err:
            if err.errno not in (errno.EBUSY, errno.EXDEV):
                raise
            # A single file bind-mounted into a container cannot be replaced: write it in place.
            path.write_text(text, encoding="utf-8")
    except OSError as err:
        raise ConfigError(f"Cannot write {path}: {err.strerror or err}") from None
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def ensure_secrets(path: str | Path, key_names: list[str]) -> Secrets:
    """Return the secrets, generating and storing the empty or missing ones."""
    path = Path(path)
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as err:
            raise ConfigError(f"Cannot read {path}: {err.strerror or err}") from None
    else:
        text = _template(key_names)

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ConfigError(f"{path} is not valid YAML: {err}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    unknown = sorted(set(data) - {"ui_key", "session_secret", "keys"})
    if unknown:
        raise ConfigError(f"{path.name}: unknown entry {', '.join(unknown)} "
                          "(expected ui_key, session_secret and keys)")
    keys_in = data.get("keys")
    if keys_in is None:
        keys_in = {}
    if not isinstance(keys_in, dict):
        raise ConfigError(f"{path.name}: 'keys' must be a mapping of key name to value")

    values: dict[str, str] = {}
    generate: list[tuple[str, int]] = []               # (entry name, random bytes)
    for label, raw, minimum, nbytes in (
        ("ui_key", data.get("ui_key"), MIN_UI_KEY, KEY_BYTES),
        ("session_secret", data.get("session_secret"), MIN_SESSION_SECRET, SESSION_BYTES),
        *((name, keys_in.get(name), MIN_KEY, KEY_BYTES) for name in key_names),
    ):
        if _is_empty(raw):
            generate.append((label, nbytes))
            continue
        if not isinstance(raw, str):
            raise ConfigError(f"{path.name}: {label} must be a text value")
        if len(raw) < minimum:
            raise ConfigError(f"{path.name}: {label} is too short ({len(raw)} characters, "
                              f"at least {minimum} needed). Use a longer value or leave it "
                              "empty to have one generated.")
        values[label] = raw

    created = not path.exists()
    if not created and path.stat().st_mode & 0o077:
        log.warning("%s is readable by other users (mode %o) and holds secrets; consider chmod 600",
                    path, path.stat().st_mode & 0o777)
    if generate:
        lines = text.splitlines()
        for label, nbytes in generate:
            value = _secrets.token_urlsafe(nbytes)
            if label in ("ui_key", "session_secret"):
                _set_top_level(lines, label, value)
            else:
                _set_key(lines, label, value)
            values[label] = value
        new_text = "\n".join(lines) + "\n"
        _verify(new_text, values, key_names, path)
        _write(path, new_text)
        log.warning("generated %d secret(s) and stored them in %s: %s",
                    len(generate), path, ", ".join(label for label, _n in generate))
    elif created:
        _write(path, text)

    keys = {name: values[name] for name in key_names}
    shared = sorted({n for n in keys if sum(v == keys[n] for v in keys.values()) > 1})
    if shared:
        raise ConfigError(f"{path.name}: keys {', '.join(shared)} have the same value; "
                          "every site needs its own key")
    return Secrets(values["ui_key"], values["session_secret"], keys)


def _verify(new_text: str, values: dict[str, str], key_names: list[str], path: Path) -> None:
    """The edited text must parse back to exactly the values we hold, or we do not write it."""
    parsed = yaml.safe_load(new_text) or {}
    keys = parsed.get("keys") or {}
    ok = (parsed.get("ui_key") == values["ui_key"]
          and parsed.get("session_secret") == values["session_secret"]
          and all(keys.get(n) == values[n] for n in key_names))
    if not ok:
        raise ConfigError(f"{path.name}: could not add the generated secrets without changing "
                          "the file's structure; please fill in the empty values by hand")
