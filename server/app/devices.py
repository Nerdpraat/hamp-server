"""The device catalogue (devices.yaml) and the role registry.

A *device type* (heatpump, battery, ...) has named *sensor slots*. A site fills the slots of one or
more *instances* of a device type. A sensor can carry a *role*: that is how the dashboard knows that
`electricitymeter.energy_import` is grid import. Roles are internal to the server and are not part
of the client protocol.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

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
    dashboard: bool = True         # False: only shown in the generic Devices tab
    static_only: bool = False      # slot holds a client-entered static value, never a series


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
    RoleSpec("battery_capacity", "gauge", "Wh", 1, None, "Battery capacity", "battery_capacity",
             static_only=True),
    RoleSpec("device_power", "gauge", "W", 1, None, "Device power", "device_power"),
    RoleSpec("price_import", "gauge", "/kWh", 1, None, "Import price", "price"),
    RoleSpec("price_export", "gauge", "/kWh", 1, None, "Export price", "price"),
    RoleSpec("price_gas", "gauge", "/m3", 1, None, "Gas price", "price"),
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


class NumberInput(BaseModel):
    """The settings of a Home Assistant number selector.

    The server checks them at startup and enforces `min` and `max` on static values. `step` is
    only a hint for the client's input widget.
    """

    model_config = ConfigDict(extra="forbid")

    min: float | None = None
    max: float | None = None
    step: float | Literal["any"] | None = None
    unit_of_measurement: str | None = None
    mode: Literal["box", "slider"] = "box"

    @model_validator(mode="after")
    def _range(self) -> "NumberInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"number.min ({self.min}) is larger than number.max ({self.max})")
        if isinstance(self.step, (int, float)) and self.step <= 0:
            raise ValueError("number.step must be greater than 0 (or 'any')")
        return self


SELECTOR_TYPES = ("entity", "number", "select", "boolean")


class Selector(BaseModel):
    """A native Home Assistant selector, such as `{"entity": {"domain": "sensor"}}` or
    `{"number": {"min": 0, "max": 100}}`. Exactly one type is allowed.

    `raw` is what the client receives in `hello`, exactly as written in devices.yaml. An `entity`
    selector picks a Home Assistant entity (the slot has a series). `number`, `select` and `boolean`
    describe a *static* slot: the client sends the value the user typed in a `states` message.
    """

    type: Literal["entity", "number", "select", "boolean"]
    raw: dict[str, Any]
    number: NumberInput | None = None
    options: tuple[str, ...] = ()
    custom_value: bool = False

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, data: Any) -> Any:
        if isinstance(data, cls) or (isinstance(data, dict) and "raw" in data and "type" in data):
            return data
        if isinstance(data, dict) and data.keys() & {"domain", "device_class"}:
            raise ValueError("selector uses the old flat form: write it as a native Home Assistant "
                             "selector, for example `entity: {domain: sensor, device_class: power}`")
        if not isinstance(data, dict) or len(data) != 1:
            raise ValueError(
                "selector must be a native Home Assistant selector with exactly one of "
                f"{', '.join(SELECTOR_TYPES)} (for example {{entity: {{domain: sensor}}}})")
        (kind, config), = data.items()
        if kind not in SELECTOR_TYPES:
            raise ValueError(f"unknown selector type {kind!r}: use one of {', '.join(SELECTOR_TYPES)}")
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ValueError(f"selector.{kind} must be a mapping")
        out: dict[str, Any] = {"type": kind, "raw": {kind: config}}
        if kind == "entity":
            if config.get("multiple"):
                raise ValueError("selector.entity.multiple is not supported: a slot takes one entity")
        elif kind == "number":
            out["number"] = NumberInput.model_validate(config)
        elif kind == "select":
            if config.get("multiple"):
                raise ValueError("selector.select.multiple is not supported: a slot takes one value")
            options = config.get("options")
            if not isinstance(options, list) or not options:
                raise ValueError("selector.select.options must be a non-empty list")
            values = []
            for opt in options:
                value = opt.get("value") if isinstance(opt, dict) else opt
                if not isinstance(value, str):
                    raise ValueError("selector.select.options must be strings or {value, label} mappings "
                                     "with a string value")
                values.append(value)
            if len(set(values)) != len(values):
                raise ValueError("selector.select.options has duplicate values")
            out["options"] = tuple(values)
            out["custom_value"] = config.get("custom_value") is True
        elif config:
            raise ValueError("selector.boolean takes no settings")
        return out

    @property
    def static(self) -> bool:
        """True when the client sends the value itself instead of an entity's readings."""
        return self.type != "entity"

    @property
    def entity(self) -> dict[str, Any]:
        """The settings of an `entity` selector (empty for the other types)."""
        return self.raw.get("entity", {}) if self.type == "entity" else {}

    @property
    def device_class(self) -> list[str]:
        return _as_list(self.entity.get("device_class"))


class SensorDef(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    selector: Selector = Field(default_factory=lambda: Selector.model_validate({"entity": {"domain": "sensor"}}))
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

    @model_validator(mode="after")
    def _selector_fits_role(self) -> "SensorDef":
        role = ROLES[self.role] if self.role else None
        if role and role.static_only:
            if self.selector.type != "number":
                raise ValueError(f"role {role.name!r} holds a static number, so its selector must be a "
                                 f"number selector, not {self.selector.type!r}")
        elif self.selector.static and role:
            raise ValueError(f"role {role.name!r} needs an entity selector: a {self.selector.type} "
                             "selector has no readings")
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

    @property
    def static(self) -> bool:
        """True when the client sends the value itself (a number, select or boolean slot)."""
        return self.selector.static

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
                          kind, unit, sign, color)


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
                sensors.append({
                    "id": rs.id, "name": rs.name, "description": rs.description,
                    "kind": rs.kind, "unit": rs.unit,
                    "selector": copy.deepcopy(rs.selector.raw),
                })
            out.append({"id": d.id, "name": d.name, "description": d.description,
                        "category": d.category, "sensors": sensors})
        return out


class StaticRejected(ValueError):
    """A static value does not fit the slot's selector. The message says why."""


def validate_static(sensor: ResolvedSensor, value: Any) -> float | str | bool:
    """Check `value` against the selector of a static slot and return what to store.

    Raises StaticRejected otherwise. Compare with `is`/`isinstance` only: 0, 0.0, False and "" are
    legitimate values and must never be treated as missing.
    """
    sel = sensor.selector
    if sel.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StaticRejected("must be a number")
        if not math.isfinite(value):
            raise StaticRejected("must be a finite number")
        limits = sel.number
        if limits.min is not None and value < limits.min:
            raise StaticRejected(f"must be at least {limits.min:g}")
        if limits.max is not None and value > limits.max:
            raise StaticRejected(f"must be at most {limits.max:g}")
        return float(value)
    if sel.type == "select":
        if not isinstance(value, str):
            raise StaticRejected("must be a string")
        if value not in sel.options and not sel.custom_value:
            raise StaticRejected(f"must be one of {', '.join(sel.options)}")
        return value
    if sel.type == "boolean":
        if not isinstance(value, bool):
            raise StaticRejected("must be true or false")
        return value
    raise StaticRejected("this slot takes readings of an entity, not a static value")


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
