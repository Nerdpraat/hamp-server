"""Server configuration: four YAML files in one directory.

* config.yaml    settings (retention, database path, ...). Optional, every value has a default.
* auth.yaml      all secrets: the dashboard key, the session secret and one key per site.
                 Empty values are generated and written back into the file (see secrets_file.py).
* topology.yaml  the sites, each referring to a key in auth.yaml by name.
* devices.yaml   the device catalogue (see devices.py).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .devices import Catalogue, load_catalogue
from .errors import ConfigError, validation_message
from .secrets_file import Secrets, ensure_secrets

__all__ = ["Config", "ConfigError", "SiteDef", "load_config", "slugify"]

SITE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class SiteDef(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    name: str = Field(min_length=1, max_length=80)
    token: str = Field(min_length=16)


class Settings(BaseModel):
    """config.yaml"""

    model_config = {"extra": "forbid"}

    retention_days: float = Field(default=7, gt=0)
    session_days: int = Field(default=30, ge=1)
    cookie_secure: bool = True
    db_path: str = "data/energy.db"
    flush_interval: float = Field(default=5, gt=0)


class TopologySite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=1)
    id: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str | None) -> str | None:
        if v is not None and not SITE_ID_RE.match(v):
            raise ValueError(f"invalid site id {v!r} (letters, digits, - and _, at most 32 characters)")
        return v


class Topology(BaseModel):
    """topology.yaml"""

    sites: list[TopologySite] = Field(min_length=1)


class Config(BaseModel):
    """Everything the server needs at runtime, assembled from the four files."""

    retention_days: float = 7
    session_days: int = 30
    cookie_secure: bool = True
    db_path: str = "data/energy.db"
    flush_interval: float = 5
    ui_key: str
    session_secret: str
    sites: list[SiteDef]
    catalogue: Catalogue

    def site(self, site_id: str) -> SiteDef | None:
        return next((s for s in self.sites if s.id == site_id), None)


def slugify(name: str) -> str:
    """Lower-case id from a site name: "DRU05" becomes "dru05", "Summer House" "summer-house"."""
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:32].strip("-")


def _read_mapping(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise ConfigError(f"{path.name} not found in {path.parent}")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ConfigError(f"Cannot read {path}: {err.strerror or err}") from None
    except yaml.YAMLError as err:
        raise ConfigError(f"{path} is not valid YAML: {err}") from None
    if data is None and not required:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return data


def parse_settings(data: dict[str, Any]) -> Settings:
    try:
        return Settings.model_validate(data)
    except ValidationError as err:
        raise ConfigError(validation_message("config.yaml", err)) from None


def parse_topology(data: dict[str, Any]) -> Topology:
    try:
        return Topology.model_validate(data)
    except ValidationError as err:
        raise ConfigError(validation_message("topology.yaml", err)) from None


def build_sites(topology: Topology, secrets: Secrets) -> list[SiteDef]:
    sites: list[SiteDef] = []
    for s in topology.sites:
        site_id = s.id or slugify(s.name)
        if not SITE_ID_RE.match(site_id):
            raise ConfigError(f"topology.yaml: cannot derive a site id from name {s.name!r}; "
                              "add an explicit 'id:'")
        sites.append(SiteDef(id=site_id, name=s.name, token=secrets.keys[s.key]))
    ids = [s.id for s in sites]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ConfigError(f"topology.yaml: duplicate site id {', '.join(dup)} "
                          "(two names give the same id; set 'id:' on one of them)")
    return sites


def check_key_references(topology: Topology) -> list[str]:
    """Names of the keys the sites use. Each key may belong to one site only."""
    names = [s.key for s in topology.sites]
    shared = sorted({n for n in names if names.count(n) > 1})
    if shared:
        raise ConfigError(f"topology.yaml: key {', '.join(shared)} is used by more than one site; "
                          "every site needs its own key")
    return names


def load_config(config_dir: str | os.PathLike[str]) -> Config:
    d = Path(config_dir)
    if not d.is_dir():
        raise ConfigError(f"Config directory not found: {d} (set ENERGY_HUB_CONFIG_DIR)")
    settings = parse_settings(_read_mapping(d / "config.yaml", required=False))
    topology = parse_topology(_read_mapping(d / "topology.yaml"))
    needed = check_key_references(topology)
    secrets = ensure_secrets(d / "auth.yaml", needed)
    catalogue = load_catalogue(d / "devices.yaml")
    return Config(
        **settings.model_dump(), ui_key=secrets.ui_key, session_secret=secrets.session_secret,
        sites=build_sites(topology, secrets), catalogue=catalogue)
