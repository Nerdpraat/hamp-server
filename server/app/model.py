"""A site's configured instances joined with the device catalogue.

The database stores only what the client declared (instances, sensor sources, static values).
Roles, kinds, units, signs and colours come from the catalogue at read time, so editing
devices.yaml takes effect immediately. Sensors or devices that were removed from the catalogue are
skipped with a warning.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .db import Database
from .devices import Catalogue, ResolvedSensor, StaticRejected, validate_static

log = logging.getLogger("energy_hub.model")

# Roles whose series are named after the instance alone ("Roof south", "Gas"), because the sensor
# name would only repeat it. Every other sensor is named "Instance · Sensor".
_INSTANCE_NAME_ROLES = {"solar_energy", "device_energy", "gas", "water"}


@dataclass(frozen=True)
class Entity:
    """One sensor of one instance that has a source, so it has data."""

    key: str
    instance: str
    instance_name: str
    device: str
    device_name: str
    category: str
    sensor: ResolvedSensor
    source: str

    @property
    def role(self) -> str | None:
        return self.sensor.role

    @property
    def kind(self) -> str:
        return self.sensor.kind

    @property
    def display_name(self) -> str:
        if self.sensor.role in _INSTANCE_NAME_ROLES:
            return self.instance_name
        return f"{self.instance_name} · {self.sensor.name}"


@dataclass(frozen=True)
class StaticValue:
    """A static slot of an instance and the latest value the client sent (None: none yet)."""

    instance: str
    sensor: ResolvedSensor
    value: float | str | bool | None


@dataclass(frozen=True)
class Instance:
    id: str
    device: str
    name: str
    device_name: str
    category: str
    entities: list[Entity] = field(default_factory=list)
    statics: list[StaticValue] = field(default_factory=list)

    @property
    def generic(self) -> bool:
        """True when some sensor has no dedicated dashboard, so the Devices tab shows this instance."""
        return any(not e.sensor.dashboard for e in self.entities)


@dataclass(frozen=True)
class SiteModel:
    instances: list[Instance]

    @property
    def entities(self) -> list[Entity]:
        return [e for i in self.instances for e in i.entities]

    @property
    def statics(self) -> list[StaticValue]:
        return [v for i in self.instances for v in i.statics]

    def entity(self, key: str) -> Entity | None:
        return next((e for e in self.entities if e.key == key), None)


async def load_site_model(db: Database, catalogue: Catalogue, site_id: str) -> SiteModel:
    instances: dict[str, Instance] = {}
    for row in await db.list_instances(site_id):
        device = catalogue.device(row["device"])
        if device is None:
            log.warning("site %s: instance %s uses device %r that is no longer in devices.yaml",
                        site_id, row["instance"], row["device"])
            continue
        instances[row["instance"]] = Instance(
            row["instance"], row["device"], row["name"], device.name, device.category)

    for row in await db.list_entities(site_id):
        inst = instances.get(row["instance"])
        sensor = catalogue.sensor(inst.device, row["sensor_id"]) if inst else None
        if inst is None or sensor is None or sensor.static:
            if inst is not None:
                log.warning("site %s: %s.%s is no longer an entity slot in devices.yaml", site_id,
                            row["instance"], row["sensor_id"])
            continue
        inst.entities.append(Entity(
            row["key"], inst.id, inst.name, inst.device, inst.device_name, inst.category,
            sensor, row["source"]))

    for row in await db.list_static(site_id):
        inst = instances.get(row["instance"])
        sensor = catalogue.sensor(inst.device, row["sensor_id"]) if inst else None
        if inst is None or sensor is None or not sensor.static:
            if inst is not None:
                log.warning("site %s: %s.%s is no longer a static slot in devices.yaml", site_id,
                            row["instance"], row["sensor_id"])
            continue
        value = row["value"]
        if value is not None:
            try:                         # the catalogue may have changed since the value was stored
                value = validate_static(sensor, value)
            except StaticRejected as why:
                log.warning("site %s: stored value of %s.%s ignored: %s", site_id, inst.id, sensor.id, why)
                value = None
        inst.statics.append(StaticValue(inst.id, sensor, value))

    return SiteModel(sorted(instances.values(), key=lambda i: (i.name.lower(), i.id)))
