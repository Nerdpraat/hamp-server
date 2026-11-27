import os
import shutil
from pathlib import Path

import pytest
import yaml

from app.config import load_config, slugify
from app.errors import ConfigError

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example"


@pytest.fixture
def cfg_dir(tmp_path):
    """A writable copy of the shipped example directory (the server writes auth.yaml)."""
    d = tmp_path / "config"
    shutil.copytree(EXAMPLE, d)
    return d


def write(d: Path, name: str, data) -> None:
    (d / name).write_text(data if isinstance(data, str) else yaml.safe_dump(data))


def test_example_directory_loads_and_secrets_are_generated(cfg_dir):
    cfg = load_config(cfg_dir)
    assert [s.id for s in cfg.sites] == ["dru05", "dru06", "dru07", "dru08"]
    assert [s.name for s in cfg.sites] == ["DRU05", "DRU06", "DRU07", "DRU08"]
    assert all(len(s.token) >= 16 for s in cfg.sites)
    assert len({s.token for s in cfg.sites}) == 4
    assert len(cfg.ui_key) >= 8 and len(cfg.session_secret) >= 16
    assert cfg.catalogue.device("battery") is not None
    # the generated values are in the file, so the next start sees the same ones
    again = load_config(cfg_dir)
    assert [s.token for s in again.sites] == [s.token for s in cfg.sites]
    assert again.ui_key == cfg.ui_key and again.session_secret == cfg.session_secret


def test_defaults_without_config_yaml(cfg_dir):
    (cfg_dir / "config.yaml").unlink()
    cfg = load_config(cfg_dir)
    assert (cfg.retention_days, cfg.session_days, cfg.cookie_secure) == (7, 30, True)
    assert (cfg.db_path, cfg.flush_interval) == ("data/energy.db", 5)


def test_settings_are_read(cfg_dir):
    write(cfg_dir, "config.yaml", {"retention_days": 3, "cookie_secure": False,
                                   "db_path": "/data/x.db", "flush_interval": 2})
    cfg = load_config(cfg_dir)
    assert (cfg.retention_days, cfg.cookie_secure, cfg.db_path, cfg.flush_interval) == (3, False, "/data/x.db", 2)


def test_empty_config_yaml_is_fine(cfg_dir):
    write(cfg_dir, "config.yaml", "# nothing here\n")
    assert load_config(cfg_dir).retention_days == 7


def test_invalid_settings_name_the_field(cfg_dir):
    write(cfg_dir, "config.yaml", {"retention_days": -1})
    with pytest.raises(ConfigError, match="retention_days"):
        load_config(cfg_dir)
    write(cfg_dir, "config.yaml", {"retention_dayz": 5})          # unknown settings are errors
    with pytest.raises(ConfigError, match="retention_dayz"):
        load_config(cfg_dir)


def test_missing_directory_and_files(tmp_path, cfg_dir):
    with pytest.raises(ConfigError, match="ENERGY_HUB_CONFIG_DIR"):
        load_config(tmp_path / "nope")
    (cfg_dir / "topology.yaml").unlink()
    with pytest.raises(ConfigError, match="topology.yaml not found"):
        load_config(cfg_dir)
    shutil.copy(EXAMPLE / "topology.yaml", cfg_dir / "topology.yaml")
    (cfg_dir / "devices.yaml").unlink()
    with pytest.raises(ConfigError, match="devices.yaml not found"):
        load_config(cfg_dir)


def test_a_site_may_refer_to_a_key_that_is_not_in_auth_yaml_yet(cfg_dir):
    """Generation also covers new sites: the missing key is created and appended."""
    write(cfg_dir, "topology.yaml", {"sites": [{"name": "Cabin", "key": "key-cabin"}]})
    cfg = load_config(cfg_dir)
    assert cfg.sites[0].id == "cabin"
    assert yaml.safe_load((cfg_dir / "auth.yaml").read_text())["keys"]["key-cabin"] == cfg.sites[0].token


def test_two_sites_cannot_share_a_key(cfg_dir):
    write(cfg_dir, "topology.yaml", {"sites": [{"name": "A", "key": "k"}, {"name": "B", "key": "k"}]})
    with pytest.raises(ConfigError, match="key k is used by more than one site"):
        load_config(cfg_dir)


def test_slug_collisions_need_an_explicit_id(cfg_dir):
    write(cfg_dir, "topology.yaml", {"sites": [{"name": "DRU 05", "key": "k1"},
                                               {"name": "dru-05", "key": "k2"}]})
    with pytest.raises(ConfigError, match="duplicate site id dru-05"):
        load_config(cfg_dir)
    write(cfg_dir, "topology.yaml", {"sites": [{"name": "DRU 05", "key": "k1"},
                                               {"name": "dru-05", "key": "k2", "id": "other"}]})
    assert [s.id for s in load_config(cfg_dir).sites] == ["dru-05", "other"]


def test_invalid_site_ids_and_names(cfg_dir):
    write(cfg_dir, "topology.yaml", {"sites": [{"name": "A", "key": "k", "id": "has space"}]})
    with pytest.raises(ConfigError, match="invalid site id"):
        load_config(cfg_dir)
    write(cfg_dir, "topology.yaml", {"sites": [{"name": "!!!", "key": "k"}]})
    with pytest.raises(ConfigError, match="cannot derive a site id"):
        load_config(cfg_dir)
    write(cfg_dir, "topology.yaml", {"sites": []})
    with pytest.raises(ConfigError, match="sites"):
        load_config(cfg_dir)


def test_broken_yaml_and_unreadable_files(cfg_dir):
    (cfg_dir / "topology.yaml").write_text("sites: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(cfg_dir)
    (cfg_dir / "topology.yaml").write_text("- a\n- list\n")
    with pytest.raises(ConfigError, match="must contain a YAML mapping"):
        load_config(cfg_dir)
    shutil.copy(EXAMPLE / "topology.yaml", cfg_dir / "topology.yaml")
    if os.geteuid() != 0:
        (cfg_dir / "topology.yaml").chmod(0)
        with pytest.raises(ConfigError, match="Cannot read"):
            load_config(cfg_dir)


def test_site_lookup(cfg_dir):
    cfg = load_config(cfg_dir)
    assert cfg.site("dru06").name == "DRU06"
    assert cfg.site("nope") is None


@pytest.mark.parametrize("name,expected", [
    ("DRU05", "dru05"), ("Summer House", "summer-house"), ("  Café  ", "caf"), ("a_b-c", "a_b-c"),
    ("x" * 50, "x" * 32), ("!!!", ""),
])
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_healthz_with_the_loaded_example(cfg_dir):
    from fastapi.testclient import TestClient
    from app.main import create_app
    cfg = load_config(cfg_dir)
    cfg.db_path = str(cfg_dir / "test.db")
    with TestClient(create_app(cfg)) as c:
        assert c.get("/healthz").json() == {"status": "ok"}


DEV_CONFIG = Path(__file__).resolve().parents[2] / "dev" / "config"


def test_the_dev_stack_config_loads_without_writing_anything(tmp_path):
    """dev/config is mounted read-only: it must be complete, and its catalogue must be the shipped one."""
    assert (DEV_CONFIG / "devices.yaml").read_bytes() == (EXAMPLE / "devices.yaml").read_bytes()
    copy = tmp_path / "config"
    shutil.copytree(DEV_CONFIG, copy)
    before = {p.name: p.read_bytes() for p in copy.iterdir()}
    cfg = load_config(copy)
    assert [s.id for s in cfg.sites] == ["home"] and cfg.sites[0].token == "dev-key-home-0123456789"
    assert cfg.ui_key == "dev-dashboard-key" and cfg.cookie_secure is False
    assert {p.name: p.read_bytes() for p in copy.iterdir()} == before      # nothing was generated or written
