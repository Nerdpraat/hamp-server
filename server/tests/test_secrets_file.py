import errno
import logging
import os
import stat

import pytest
import yaml

from app import secrets_file
from app.errors import ConfigError
from app.secrets_file import ensure_secrets

NAMES = ["key-a", "key-b"]

COMMENTED = """\
# my own header comment
ui_key: ""            # shared dashboard key
session_secret: ""
keys:
  # one per site
  key-a: ""           # site A
  key-b: "already-a-good-secret-value"
  # trailing note
"""


def read(path):
    return yaml.safe_load(path.read_text())


def test_creates_the_file_from_a_template_with_mode_600(tmp_path):
    p = tmp_path / "auth.yaml"
    s = ensure_secrets(p, NAMES)
    assert p.exists() and stat.S_IMODE(p.stat().st_mode) == 0o600
    data = read(p)
    assert data["ui_key"] == s.ui_key and len(s.ui_key) >= 8
    assert data["session_secret"] == s.session_secret and len(s.session_secret) >= 16
    assert data["keys"] == s.keys and set(s.keys) == set(NAMES)
    assert all(len(v) >= 16 for v in s.keys.values()) and len(set(s.keys.values())) == 2
    assert "Leave a value empty" in p.read_text()                  # the explanation is in the file


def test_only_empty_values_change_and_comments_survive(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text(COMMENTED)
    s = ensure_secrets(p, NAMES)
    text = p.read_text()
    for kept in ("# my own header comment", "# shared dashboard key", "# one per site",
                 "# site A", "# trailing note"):
        assert kept in text
    assert s.keys["key-b"] == "already-a-good-secret-value"          # untouched
    assert 'key-b: "already-a-good-secret-value"' in text
    lines = [line.split(":")[0].strip() for line in text.splitlines() if ":" in line and "#" not in line.split(":")[0]]
    assert lines.index("ui_key") < lines.index("session_secret") < lines.index("keys")
    assert read(p)["keys"]["key-a"] == s.keys["key-a"] != ""


def test_second_start_changes_nothing(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text(COMMENTED)
    first = ensure_secrets(p, NAMES)
    before = p.read_text()
    mtime = p.stat().st_mtime_ns
    second = ensure_secrets(p, NAMES)
    assert second == first
    assert p.read_text() == before and p.stat().st_mtime_ns == mtime


def test_missing_entries_are_added(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text('ui_key: "a-dashboard-key"\nkeys:\n  key-a: "a-site-key-0123456789"\n  # note\n')
    s = ensure_secrets(p, ["key-a", "key-new"])
    data = read(p)
    assert data["ui_key"] == "a-dashboard-key" and data["keys"]["key-a"] == "a-site-key-0123456789"
    assert data["keys"]["key-new"] == s.keys["key-new"] and len(s.keys["key-new"]) >= 16
    assert data["session_secret"] == s.session_secret
    assert "# note" in p.read_text()


def test_file_without_keys_section(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text('ui_key: "a-dashboard-key"\nsession_secret: "a-session-secret-0123456"\n')
    s = ensure_secrets(p, ["key-a"])
    assert read(p)["keys"] == {"key-a": s.keys["key-a"]}


def test_quoted_and_null_values_are_filled(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text("ui_key: ''\nsession_secret: ~\nkeys:\n  \"key-a\": null\n  'key-b':\n")
    s = ensure_secrets(p, NAMES)
    assert read(p)["keys"] == s.keys and all(s.keys.values())


def test_secret_values_are_never_logged(tmp_path, caplog):
    p = tmp_path / "auth.yaml"
    with caplog.at_level(logging.DEBUG):
        s = ensure_secrets(p, NAMES)
    log_text = caplog.text
    assert "generated 4 secret(s)" in log_text and "key-a" in log_text and "ui_key" in log_text
    for value in (s.ui_key, s.session_secret, *s.keys.values()):
        assert value not in log_text


def test_short_supplied_values_are_rejected_with_the_name_not_the_value(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text('ui_key: "Zq7x"\nsession_secret: ""\nkeys:\n  key-a: "Wv9short"\n  key-b: ""\n')
    with pytest.raises(ConfigError) as err:
        ensure_secrets(p, NAMES)
    assert "ui_key is too short" in str(err.value) and "Zq7x" not in str(err.value)

    text = ('ui_key: "long-enough-key"\nsession_secret: "long-enough-session-secret"\n'
            'keys:\n  key-a: "Wv9short"\n  key-b: ""\n')
    p.write_text(text)
    with pytest.raises(ConfigError) as err:
        ensure_secrets(p, NAMES)
    assert "key-a is too short" in str(err.value) and "Wv9short" not in str(err.value)
    assert p.read_text() == text                  # nothing was generated or written on the error


def test_the_same_value_for_two_keys_is_rejected(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text('ui_key: "long-enough-key"\nsession_secret: "long-enough-session-secret"\n'
                 'keys:\n  key-a: "the-same-key-0123456789"\n  key-b: "the-same-key-0123456789"\n')
    with pytest.raises(ConfigError, match="key-a, key-b have the same value"):
        ensure_secrets(p, NAMES)


def test_unwritable_directory_gives_a_clear_error(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        with pytest.raises(ConfigError, match="directory must be writable"):
            ensure_secrets(d / "auth.yaml", NAMES)
    finally:
        d.chmod(0o700)


def test_all_secrets_supplied_means_no_write_even_in_a_read_only_directory(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text('ui_key: "long-enough-key"\nsession_secret: "long-enough-session-secret"\n'
                 'keys:\n  key-a: "site-key-a-0123456789"\n  key-b: "site-key-b-0123456789"\n')
    p.chmod(0o400)
    tmp_path.chmod(0o500)
    try:
        s = ensure_secrets(p, NAMES)
    finally:
        tmp_path.chmod(0o700)
    assert s.keys == {"key-a": "site-key-a-0123456789", "key-b": "site-key-b-0123456789"}


def test_existing_file_mode_is_kept(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text(COMMENTED)
    p.chmod(0o640)
    ensure_secrets(p, NAMES)
    assert stat.S_IMODE(p.stat().st_mode) == 0o640


def test_single_file_bind_mount_falls_back_to_writing_in_place(tmp_path, monkeypatch):
    p = tmp_path / "auth.yaml"
    p.write_text(COMMENTED)

    def busy(src, dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(secrets_file.os, "replace", busy)
    s = ensure_secrets(p, NAMES)
    assert read(p)["keys"]["key-a"] == s.keys["key-a"]
    assert not list(tmp_path.glob(".auth-*"))                       # no temp file left behind


def test_no_temp_files_are_left_behind(tmp_path):
    ensure_secrets(tmp_path / "auth.yaml", NAMES)
    assert [p.name for p in tmp_path.iterdir()] == ["auth.yaml"]


def test_flow_style_keys_are_refused_with_advice(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text('ui_key: "long-enough-key"\nsession_secret: "long-enough-session-secret"\n'
                 'keys: {key-a: ""}\n')
    with pytest.raises(ConfigError, match="block"):
        ensure_secrets(p, ["key-a"])


def test_invalid_files(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text("- a list\n")
    with pytest.raises(ConfigError, match="mapping"):
        ensure_secrets(p, NAMES)
    p.write_text("ui_key: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        ensure_secrets(p, NAMES)
    p.write_text("ui_keyy: x\n")
    with pytest.raises(ConfigError, match="unknown entry ui_keyy"):
        ensure_secrets(p, NAMES)
    p.write_text("keys: [a, b]\n")
    with pytest.raises(ConfigError, match="'keys' must be a mapping"):
        ensure_secrets(p, NAMES)


def test_a_group_or_world_readable_file_gets_a_warning_without_secrets(tmp_path, caplog):
    p = tmp_path / "auth.yaml"
    p.write_text('ui_key: "long-enough-key"\nsession_secret: "long-enough-session-secret"\n'
                 'keys:\n  key-a: "site-key-a-0123456789"\n  key-b: "site-key-b-0123456789"\n')
    p.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        ensure_secrets(p, NAMES)
    assert "readable by other users" in caplog.text and "site-key-a" not in caplog.text
    caplog.clear()
    p.chmod(0o600)
    with caplog.at_level(logging.WARNING):
        ensure_secrets(p, NAMES)
    assert "readable by other users" not in caplog.text
