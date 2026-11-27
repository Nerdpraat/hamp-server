"""The device catalogue (devices.yaml) and the role registry.

A *device type* (heatpump, battery, ...) has named *sensor slots*. A site fills the slots of one or
more *instances* of a device type. A sensor can carry a *role*: that is how the dashboard knows that
`electricitymeter.energy_import` is grid import. Roles are internal to the server and are not part
of the client protocol.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator, model_validator

from .errors import ConfigError, validation_message

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class RoleSpec:
    name: str
    kind: Literal["counter", "gauge"]
    unit: str
    sign: int = 1                  # -1: subtracted in totals (export, battery charge)
    color: str | None = None
    label: str = ""
    group: str = "other"
    price: bool = False            # a price slot: may be a fixed number
    dashboard: bool = True         # False: only shown in the generic Devices tab


_ROLE_LIST = [
    RoleSpec("grid_import", "counter", "kWh", 1, "#488fc2", "Grid import", "grid"),
    RoleSpec("grid_export", "counter", "kWh", -1, "#8353d1", "Grid export", "grid"),
    RoleSpec("solar_energy", "counter", "kWh", 1, "#ff9800", "Solar", "solar"),
    RoleSpec("battery_charge", "counter", "kWh", -1, "#f06292", "Battery charged", "battery"),
    RoleSpec("battery_discharge", "counter", "kWh", 1, "#4db6ac", "Battery discharged", "battery"),
    RoleSpec("gas", "counter", "m3", 1, "#8e021b", "Gas", "gas"),
    RoleSpec("water", "counter", "m3", 1, "#29b6f6", "Water", "water", dashboard=False),
    RoleSpec("device_energy", "counter", "kWh", 1, None, "Device", "device"),
    RoleSpec("grid_power_import", "gauge", "W", 1, "#488fc2", "Grid import power", "power"),
    RoleSpec("grid_power_export", "gauge", "W", 1, "#8353d1", "Grid export power", "power"),
    RoleSpec("solar_power", "gauge", "W", 1, "#ff9800", "Solar power", "power"),
    RoleSpec("battery_power", "gauge", "W", 1, "#4db6ac", "Battery power", "power"),
    RoleSpec("battery_soc", "gauge", "%", 1, None, "Battery level", "battery_state"),
    RoleSpec("device_power", "gauge", "W", 1, None, "Device power", "device_power"),
    RoleSpec("price_import", "gauge", "/kWh", 1, None, "Import price", "price", price=True),
    RoleSpec("price_export", "gauge", "/kWh", 1, None, "Export price", "price", price=True),
    RoleSpec("price_gas", "gauge", "/m3", 1, None, "Gas price", "price", price=True),
]
ROLES: dict[str, RoleSpec] = {r.name: r for r in _ROLE_LIST}

# Used when a sensor has no role (or the role does not say): kind and unit from the HA device class.
DEVICE_CLASS_DEFAULTS: dict[str, tuple[str, str]] = {
    "energy": ("counter", "kWh"),
    "gas": ("counter", "m3"),
    "water": ("counter", "m3"),
    "power": ("gauge", "W"),
    "temperature": ("gauge", "°C"),
    "battery": ("gauge", "%"),
    "volume_flow_rate": ("gauge", "L/min"),
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


class Selector(BaseModel):
    """Which Home Assistant entities fit a slot (domain and device class)."""

    domain: list[str] = Field(default_factory=lambda: ["sensor"])
    device_class: list[str] = Field(default_factory=list)

    @field_validator("domain", "device_class", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> list[str]:
        return _as_list(v)

    @field_validator("domain")
    @classmethod
    def _domain_not_empty(cls, v: list[str]) -> list[str]:
        return v or ["sensor"]


class SensorDef(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    selector: Selector = Field(default_factory=Selector)
    role: str | None = None
    kind: Literal["counter", "gauge"] | None = None
    unit: str | None = None
    sign: Literal[1, -1] | None = None
    color: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"invalid sensor id {v!r} (use lower case letters, digits and _)")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v: str | None) -> str | None:
        if v is not None and v not in ROLES:
            raise ValueError(f"unknown role {v!r} (known roles: {', '.join(sorted(ROLES))})")
        return v

    @model_validator(mode="after")
    def _kind_matches_role(self) -> "SensorDef":
        if self.role and self.kind and self.kind != ROLES[self.role].kind:
            raise ValueError(
                f"kind {self.kind!r} conflicts with role {self.role!r}, which is a {ROLES[self.role].kind}")
        return self


class DeviceDef(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    category: str = "other"
    sensors: list[SensorDef] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"invalid device id {v!r} (use lower case letters, digits and _)")
        return v

    @model_validator(mode="after")
    def _unique(self) -> "DeviceDef":
        ids = [s.id for s in self.sensors]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate sensor id {', '.join(dup)} in device {self.id!r}")
        names = [s.name for s in self.sensors]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate sensor name {', '.join(dup)} in device {self.id!r} "
                             "(names label the form fields, so they must differ)")
        return self


@dataclass(frozen=True)
class ResolvedSensor:
    """A sensor slot with every default filled in."""

    device_id: str
    id: str
    name: str
    description: str
    selector: Selector
    role: str | None
    kind: str
    unit: str
    sign: int
    color: str | None
    allow_fixed: bool

    @property
    def group(self) -> str:
        return ROLES[self.role].group if self.role else "other"

    @property
    def dashboard(self) -> bool:
        """True when a dedicated dashboard view uses this sensor."""
        return self.role is not None and ROLES[self.role].dashboard


def _resolve(device: DeviceDef, s: SensorDef) -> ResolvedSensor:
    role = ROLES[s.role] if s.role else None
    klass = s.selector.device_class[0] if s.selector.device_class else None
    class_kind, class_unit = DEVICE_CLASS_DEFAULTS.get(klass or "", (None, None))
    kind = s.kind or (role.kind if role else None) or class_kind or "gauge"
    unit = s.unit if s.unit is not None else (role.unit if role else (class_unit or ""))
    sign = s.sign if s.sign is not None else (role.sign if role else 1)
    color = s.color or (role.color if role else None)
    return ResolvedSensor(device.id, s.id, s.name, s.description, s.selector, s.role,
                          kind, unit, sign, color, bool(role and role.price))


class Catalogue(BaseModel):
    devices: list[DeviceDef] = Field(min_length=1)
    _resolved: dict[tuple[str, str], ResolvedSensor] = PrivateAttr(default_factory=dict)
    _by_id: dict[str, DeviceDef] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> "Catalogue":
        ids = [d.id for d in self.devices]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate device id {', '.join(dup)}")
        return self

    def model_post_init(self, _ctx: Any) -> None:
        for d in self.devices:
            for s in d.sensors:
                self._resolved[(d.id, s.id)] = _resolve(d, s)
        self._by_id = {d.id: d for d in self.devices}

    # ---- lookups ------------------------------------------------------
    def device(self, device_id: str) -> DeviceDef | None:
        return self._by_id.get(device_id)

    def sensor(self, device_id: str, sensor_id: str) -> ResolvedSensor | None:
        return self._resolved.get((device_id, sensor_id))

    def sensors(self, device_id: str) -> list[ResolvedSensor]:
        d = self.device(device_id)
        return [self._resolved[(d.id, s.id)] for s in d.sensors] if d else []

    def hello(self) -> list[dict[str, Any]]:
        """The catalogue as sent to clients in `hello` (roles stay on the server)."""
        out = []
        for d in self.devices:
            sensors = []
            for rs in self.sensors(d.id):
                selector: dict[str, Any] = {"domain": list(rs.selector.domain)}
                if rs.selector.device_class:
                    selector["device_class"] = list(rs.selector.device_class)
                sensors.append({
                    "id": rs.id, "name": rs.name, "description": rs.description,
                    "kind": rs.kind, "unit": rs.unit, "allow_fixed": rs.allow_fixed,
                    "selector": selector,
                })
            out.append({"id": d.id, "name": d.name, "description": d.description,
                        "category": d.category, "sensors": sensors})
        return out


def parse_catalogue(data: object) -> Catalogue:
    try:
        return Catalogue.model_validate(data)
    except ValidationError as err:
        raise ConfigError(validation_message("devices.yaml", err)) from None


def load_catalogue(path: str | Path) -> Catalogue:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"devices.yaml not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as err:
        raise ConfigError(f"Cannot read {p}: {err.strerror or err}") from None
    except yaml.YAMLError as err:
        raise ConfigError(f"{p} is not valid YAML: {err}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{p} must contain a YAML mapping with a 'devices' list")
    return parse_catalogue(data)
