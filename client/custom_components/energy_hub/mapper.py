"""Map the Home Assistant energy dashboard to Energy Hub device instances.

An *instance* is one device of a catalogue device type (see docs/protocol.md) whose sensor slots
are filled with Home Assistant entities. This module has no Home Assistant imports so it can be
unit tested on its own.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_ID = 40

POSITIVE = "positive"    # send max(value, 0): the import half of a signed power sensor
NEGATIVE = "negative"    # send max(-value, 0): the export half


@dataclass(frozen=True)
class Slot:
    """One sensor slot of an instance: an entity, or a fixed number (price slots)."""

    sensor_id: str
    entity_id: str | None = None
    fixed: float | None = None
    transform: str | None = None

    def to_option(self) -> dict[str, Any]:
        return {"fixed": self.fixed} if self.entity_id is None else {"entity_id": self.entity_id}


@dataclass(frozen=True)
class InstanceDef:
    id: str
    device: str
    name: str
    slots: tuple[Slot, ...]
    discovered: bool = False

    def to_message(self) -> dict[str, Any]:
        """The instance as sent in the protocol `config` message."""
        sensors: dict[str, Any] = {}
        for s in self.slots:
            sensors[s.sensor_id] = {"fixed": s.fixed} if s.entity_id is None else {"source": s.entity_id}
        return {"id": self.id, "device": self.device, "name": self.name[:80], "sensors": sensors}

    def to_option(self) -> dict[str, Any]:
        """JSON-able form stored in the config entry options (manual instances)."""
        return {"id": self.id, "device": self.device, "name": self.name,
                "sensors": {s.sensor_id: s.to_option() for s in self.slots}}


def instance_from_option(data: Any) -> InstanceDef | None:
    """Read a stored manual instance. Anything malformed gives None, so stale options are ignored."""
    try:
        iid, device, name, sensors = data["id"], data["device"], data["name"], data["sensors"]
        if not (isinstance(iid, str) and INSTANCE_ID_RE.match(iid) and isinstance(device, str)
                and isinstance(name, str) and name and isinstance(sensors, dict)):
            return None
        slots = []
        for sid, entry in sensors.items():
            if "entity_id" in entry and isinstance(entry["entity_id"], str) and entry["entity_id"]:
                slots.append(Slot(sid, entity_id=entry["entity_id"]))
            elif isinstance(entry.get("fixed"), (int, float)) and not isinstance(entry["fixed"], bool):
                slots.append(Slot(sid, fixed=float(entry["fixed"])))
            else:
                return None
        return InstanceDef(iid, device, name, tuple(slots)) if slots else None
    except (KeyError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------------- ids
def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def discovered_id(device: str, primary_entity: str) -> str:
    """A stable instance id derived from the device type and the primary entity."""
    base = f"{device.replace('_', '-')}-{slug(primary_entity)}"
    if len(base) <= MAX_ID:
        return base
    digest = hashlib.sha1(primary_entity.encode()).hexdigest()[:8]
    return f"{base[:MAX_ID - 9].rstrip('-')}-{digest}"


def manual_id(name: str, device: str, taken: Iterable[str]) -> str:
    """An id for an instance the user adds: from its name, made unique among `taken`."""
    taken = set(taken)
    base = (slug(name) or device.replace("_", "-"))[: MAX_ID - 4].strip("-") or "device"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


# ---------------------------------------------------------------------- discovery
def _is_entity(stat_id: Any) -> bool:
    """Energy preferences may point at external statistics ("domain:name"). Only entities qualify."""
    return isinstance(stat_id, str) and "." in stat_id and ":" not in stat_id


def _power_stat(source: dict[str, Any]) -> str | None:
    rate = source.get("stat_rate")
    if rate:
        return rate
    return (source.get("power_config") or {}).get("stat_rate")


def _entity_slot(sensor_id: str, entity: Any, **kw: Any) -> Slot | None:
    return Slot(sensor_id, entity_id=entity, **kw) if _is_entity(entity) else None


def _price_slot(sensor_id: str, entity: Any, number: Any) -> Slot | None:
    if _is_entity(entity):
        return Slot(sensor_id, entity_id=entity)
    if isinstance(number, (int, float)) and not isinstance(number, bool) and number >= 0:
        return Slot(sensor_id, fixed=float(number))
    return None


def _build(device: str, name: str, primary: Any, slots: Iterable[Slot | None]) -> InstanceDef | None:
    real = tuple(s for s in slots if s is not None)
    if not real or not _is_entity(primary):
        return None
    return InstanceDef(discovered_id(device, primary), device, name, real, discovered=True)


def _numbered(base: str, index: int, total: int) -> str:
    return base if total == 1 else f"{base} {index}"


def _grid_instances(src: dict[str, Any]) -> list[tuple[str | None, list[Slot | None], str | None]]:
    """(name, slots, primary entity) for each electricity meter in a grid source."""
    out: list[tuple[str | None, list[Slot | None], str | None]] = []
    power = _power_stat(src)
    if "flow_from" in src or "flow_to" in src:               # layout with separate import/export lists
        froms, tos = src.get("flow_from", []), src.get("flow_to", [])
        for i in range(max(len(froms), len(tos))):
            f = froms[i] if i < len(froms) else {}
            t = tos[i] if i < len(tos) else {}
            slots = [
                _entity_slot("energy_import", f.get("stat_energy_from")),
                _entity_slot("energy_export", t.get("stat_energy_to")),
                _price_slot("price_import", f.get("entity_energy_price"), f.get("number_energy_price")),
                _price_slot("price_export", t.get("entity_energy_price"), t.get("number_energy_price")),
            ]
            out.append((None, slots, f.get("stat_energy_from") or t.get("stat_energy_to")))
        for p in src.get("power", []):
            stat = _power_stat(p)
            if _is_entity(stat) and out:
                out[0][1].extend([Slot("power_import", stat, transform=POSITIVE),
                                  Slot("power_export", stat, transform=NEGATIVE)])
            elif _is_entity(stat):
                out.append((None, [Slot("power_import", stat, transform=POSITIVE),
                                   Slot("power_export", stat, transform=NEGATIVE)], stat))
        return out
    slots = [
        _entity_slot("energy_import", src.get("stat_energy_from")),
        _entity_slot("energy_export", src.get("stat_energy_to")),
        _price_slot("price_import", src.get("entity_energy_price"), src.get("number_energy_price")),
        _price_slot("price_export", src.get("entity_energy_price_export"),
                    src.get("number_energy_price_export")),
    ]
    if _is_entity(power):
        # The energy dashboard has one signed power sensor (import positive, export negative);
        # the catalogue has an import and an export power slot, so split it.
        slots += [Slot("power_import", power, transform=POSITIVE), Slot("power_export", power, transform=NEGATIVE)]
    out.append((src.get("name"), slots, src.get("stat_energy_from") or src.get("stat_energy_to") or power))
    return out


def discover(prefs: dict[str, Any] | None, name_for: Callable[[str], str]) -> list[InstanceDef]:
    """Translate `energy/get_prefs` data into device instances (electricity meter, solar inverter,
    battery, gas meter, consumer devices). Water sources are not discovered."""
    if not prefs:
        return []
    sources = prefs.get("energy_sources", [])
    grid_meters = {i: _grid_instances(s) for i, s in enumerate(sources) if s.get("type") == "grid"}
    totals = {"grid": sum(len(m) for m in grid_meters.values()),
              "solar": sum(1 for s in sources if s.get("type") == "solar"),
              "battery": sum(1 for s in sources if s.get("type") == "battery")}
    seen = {"grid": 0, "solar": 0, "battery": 0}
    found: list[InstanceDef | None] = []

    for index, src in enumerate(sources):
        kind = src.get("type")
        if kind == "grid":
            for name, slots, primary in grid_meters[index]:
                seen["grid"] += 1
                label = name or _numbered("Electricity Meter", seen["grid"], totals["grid"])
                found.append(_build("electricitymeter", label, primary, slots))
        elif kind == "solar":
            seen["solar"] += 1
            name = src.get("name") or _numbered("Solar Inverter", seen["solar"], totals["solar"])
            found.append(_build("solar_inverter", name, src.get("stat_energy_from"), [
                _entity_slot("energy", src.get("stat_energy_from")),
                _entity_slot("power", src.get("stat_rate"))]))
        elif kind == "battery":
            seen["battery"] += 1
            name = src.get("name") or _numbered("Battery", seen["battery"], totals["battery"])
            found.append(_build("battery", name, src.get("stat_energy_from"), [
                _entity_slot("energy_discharged", src.get("stat_energy_from")),
                _entity_slot("energy_charged", src.get("stat_energy_to")),
                _entity_slot("power", _power_stat(src)),
                _entity_slot("state_of_charge", src.get("stat_soc"))]))
        elif kind == "gas":
            found.append(_build("gasmeter", src.get("name") or "Gas Meter", src.get("stat_energy_from"), [
                _entity_slot("consumption", src.get("stat_energy_from")),
                _price_slot("price", src.get("entity_energy_price"), src.get("number_energy_price"))]))
        # other kinds (water, ...) are not part of the energy overview

    for dev in prefs.get("device_consumption", []):
        entity = dev.get("stat_consumption")
        if _is_entity(entity):
            found.append(_build("consumer_device", dev.get("name") or name_for(entity), entity, [
                _entity_slot("energy", entity), _entity_slot("power", dev.get("stat_rate"))]))

    result: dict[str, InstanceDef] = {}
    for inst in found:
        if inst is not None:
            result.setdefault(inst.id, inst)           # the same entity twice: first one wins
    return list(result.values())


def effective_instances(discovered: Iterable[InstanceDef], manual: Iterable[InstanceDef],
                        excluded: Iterable[str]) -> list[InstanceDef]:
    """Discovered instances minus the ones the user removed, plus the manual ones."""
    excluded = set(excluded)
    out: dict[str, InstanceDef] = {i.id: i for i in discovered if i.id not in excluded}
    for inst in manual:
        out[inst.id] = inst                           # a manual instance replaces a discovered one
    return list(out.values())


# ---------------------------------------------------------------------- bindings and values
@dataclass(frozen=True)
class Binding:
    """Entity -> key: where a reading goes."""

    key: str
    instance: str
    sensor_id: str
    entity_id: str
    transform: str | None = None


def bindings(instances: Iterable[InstanceDef]) -> list[Binding]:
    return [Binding(f"{i.id}.{s.sensor_id}", i.id, s.sensor_id, s.entity_id, s.transform)
            for i in instances for s in i.slots if s.entity_id is not None]


def apply_transform(value: float, transform: str | None) -> float:
    if transform == POSITIVE:
        return max(value, 0.0)
    if transform == NEGATIVE:
        return max(-value, 0.0)
    return value


# ---------------------------------------------------------------------- units
_ENERGY = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0, "GWh": 1e6, "MJ": 1 / 3.6, "GJ": 1000 / 3.6}
_POWER = {"W": 1.0, "kW": 1000.0, "MW": 1e6, "GW": 1e9}
_VOLUME = {"m3": 1.0, "L": 0.001, "ft3": 0.0283168, "CCF": 2.83168, "MCF": 28.3168}
_FLOW = {"L/min": 1.0, "L/s": 60.0, "L/h": 1 / 60, "m3/h": 1000 / 60, "m3/min": 1000.0}
_TABLES = (_ENERGY, _POWER, _VOLUME, _FLOW)
_ALIASES = {"m³": "m3", "ft³": "ft3", "l": "L", "m³/h": "m3/h", "m³/min": "m3/min", "l/min": "L/min",
            "l/s": "L/s", "l/h": "L/h"}


def _norm_unit(u: str | None) -> str:
    u = (u or "").strip()
    return _ALIASES.get(u, u)


def convert(value: float, unit_from: str | None, unit_to: str | None) -> float:
    """Convert between common energy, power, volume, flow and temperature units.

    Unknown or unrelated units leave the value unchanged: the catalogue unit is what the dashboard
    displays, so a mismatch is the user's to fix.
    """
    a, b = _norm_unit(unit_from), _norm_unit(unit_to)
    if not a or not b or a == b:
        return value
    if a == "°F" and b == "°C":
        return (value - 32) * 5 / 9
    if a == "°C" and b == "°F":
        return value * 9 / 5 + 32
    for table in _TABLES:
        if a in table and b in table:
            return value * table[a] / table[b]
    return value
