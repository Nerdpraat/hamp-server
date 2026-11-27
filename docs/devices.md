# The device catalogue: `devices.yaml`

`devices.yaml` lists the device types a site can add. The server sends it to every client in the
protocol `hello`. In Home Assistant the user picks a device type, names the instance and assigns
one entity to each sensor slot.

```yaml
devices:
  - id: battery
    name: "Battery"
    description: "Home battery storage level and charge/discharge flow."
    category: energy
    sensors:
      - id: state_of_charge
        name: "State of Charge"
        description: "Current battery percentage."
        role: battery_soc
        selector:
          domain: sensor
          device_class: battery
      - id: energy_charged
        name: "Energy Charged"
        description: "Total energy stored into the battery."
        role: battery_charge
        selector:
          domain: sensor
          device_class: energy
      - id: capacity
        name: "Capacity"
        description: "Usable battery capacity in Wh, entered as a fixed number."
        role: battery_capacity
        selector:
          number:
            min: 0
            max: 100000
            step: 0.1
            unit_of_measurement: "Wh"
            mode: box
```

The shipped file is `server/config.example/devices.yaml`. The server checks the whole file when
it starts and refuses to run with a clear message if something is wrong. Edit it and restart the
server: the dashboard picks the change up immediately, because roles, units and names are read from
the catalogue at request time.

## Fields

| Level | Field | Required | Meaning |
|---|---|---|---|
| device | `id` | yes | lower case letters, digits and `_`; unique |
| device | `name`, `description` | `name` yes | shown when adding a device |
| device | `category` | no | groups the device types in the picker (`energy`, `climate`, `utilities`, ...) |
| device | `sensors` | yes | at least one |
| sensor | `id` | yes | lower case letters, digits and `_`; unique within the device |
| sensor | `name` | yes | unique within the device. **Labels the form field in Home Assistant**, so names must differ |
| sensor | `description` | no | explained under the form |
| sensor | `selector` | no | which entities fit: `domain` (default `sensor`) and `device_class`. Each is a string or a list. Slots that take a fixed number (price roles, `battery_capacity`) may add `number`, see below |
| sensor | `role` | no | how the dashboard uses the sensor, see below. Without a role the sensor only appears in the Devices tab |
| sensor | `kind` | no | `counter` (a cumulative meter) or `gauge` (an instantaneous value). Default: from the role, else from the `device_class` |
| sensor | `unit` | no | the unit the server stores. Default: from the role, else from the `device_class`. The client converts Home Assistant's unit to it |
| sensor | `sign` | no | `-1` makes a counter negative in totals (export, battery charge). Default: from the role |
| sensor | `color` | no | chart colour. Default: from the role |

A slot takes exactly one entity. Several of the same device (two batteries, five smart plugs) are
several *instances*, not several entities in one slot.

## Defaults from the device class

When a sensor has no role, or the role does not say, the first `device_class` of the selector decides:

| `device_class` | kind | unit |
|---|---|---|
| `energy` | counter | kWh |
| `gas`, `water` | counter | m3 |
| `power` | gauge | W |
| `temperature` | gauge | °C |
| `battery` | gauge | % |
| `volume_flow_rate` | gauge | L/min |
| anything else, or none | gauge | (none) |

## Roles

A role tells the dashboard what a sensor means. Roles are fixed in the server code.

| Role | Kind | Unit | Sign | Used for |
|---|---|---|---|---|
| `grid_import` | counter | kWh | + | energy bought from the grid |
| `grid_export` | counter | kWh | - | energy fed back |
| `solar_energy` | counter | kWh | + | solar production |
| `battery_charge` | counter | kWh | - | energy into the battery |
| `battery_discharge` | counter | kWh | + | energy out of the battery |
| `gas` | counter | m3 | + | gas consumption |
| `water` | counter | m3 | + | water consumption (Devices tab only) |
| `device_energy` | counter | kWh | + | individual device energy, listed under Individual devices |
| `grid_power_import`, `grid_power_export` | gauge | W | | both positive; the Power sources chart shows import minus export |
| `solar_power` | gauge | W | | solar power |
| `battery_power` | gauge | W | | positive while discharging |
| `battery_soc` | gauge | % | | battery level (the mean over all batteries is shown) |
| `device_power` | gauge | W | | device power |
| `battery_capacity` | gauge | kWh | | usable battery capacity; fixed number only, used to compute stored energy |
| `battery_capacity` | gauge | Wh | | usable battery capacity; fixed number only, used to compute stored energy |
| `price_import`, `price_export` | gauge | /kWh | | price slots, see below |
| `price_gas` | gauge | /m3 | | price slot |

Existing deployments must add the `capacity` slot (id `capacity`, role `battery_capacity`, with the
`selector.number` block shown above) to the battery device in their own `devices.yaml`, or change an
older kWh version to this one. The shipped example already has it. Capacities that were entered in
kWh before the change must be entered again in Wh (10 kWh becomes 10000).

### Price slots and cost

Most sensors take an entity. Some roles also accept a **fixed number**: the price roles
(entity or number) and `battery_capacity` (number only, the role is `fixed_only`). In the protocol
this is `allow_fixed`, and `fixed_only` for slots that never take an entity.

A price slot is an ordinary sensor with a `price_*` role. The client can fill it with an entity
(a tariff sensor, which may change over time) **or with a fixed number**: the options flow shows a
second field "(fixed value)" for these slots. Cost is the energy delta of a counter times the price
of the same instance:

* `grid_import` uses `price_import`, `grid_export` uses `price_export` (shown as negative),
  `gas` uses `price_gas`.
* No price slot filled means no cost for that counter.

### Battery capacity and stored energy

The `capacity` slot of a battery takes a fixed number of Wh. In Home Assistant it is a number box
named "Capacity (fixed value)" with no entity picker. Stored energy per battery is state of charge
x capacity, shown in kWh. It is shown:

* in the overview, per site and in total, next to the average percentage;
* as the "<battery> · Stored energy" tile on the Now tab;
* as the "Battery stored energy" chart on the Electricity tab.

A battery without a capacity shows only its percentage and is left out of the kWh sums. The average
percentage is the plain mean over all batteries, not weighted by capacity.

### The number box: `selector.number`

A slot that takes a fixed number (a price role or `battery_capacity`) can describe its number box in
`selector.number`. All fields are optional:

| Field | Meaning |
|---|---|
| `min`, `max` | allowed range. The **server enforces them** on incoming fixed values, so every client is held to them |
| `step` | a number, or `any` |
| `unit_of_measurement` | shown next to the box. For a `fixed_only` slot the default is the sensor's unit |
| `mode` | `box` (default) or `slider` |

Without `number` the Home Assistant box is 0 to 1000 with step `any`. `number` on any other sensor is
refused at startup.

## Instances and names

A site can hold any number of instances of a device type, each with a name. Series are named
"Instance name · Sensor name" (for example "Battery 1 · Energy Charged"). For the roles
`solar_energy`, `device_energy`, `gas` and `water` the sensor name would only repeat the instance
name, so they are shown as just the instance name ("Roof south", "Car charger").

## The Devices tab

An instance appears in the Devices tab when at least one of its sensors has no dedicated view: it has
no role, or the role `water`. A heat pump with its temperatures and COP therefore appears, a battery
(all sensors have roles) does not. The tab is driven only by this file, so a new device type shows
up there with no code change.

## Adding a device type

Append an entry. For example an EV charger whose sensors have no role (so it appears in the Devices tab):

```yaml
  - id: ev_charger
    name: "EV Charger"
    description: "Car charger with session energy."
    category: appliances
    sensors:
      - id: power
        name: "Power"
        selector: {domain: sensor, device_class: power}
      - id: session_energy
        name: "Session Energy"
        selector: {domain: sensor, device_class: energy}
```

After a restart of the server, "EV Charger" is offered in the options flow of every Home
Assistant site (the integration reads the catalogue each time it connects) and its instances show up
in the Devices tab.

## What the server checks

Startup stops with a message naming the file path of the problem (for example
`devices.0.sensors.2.role: unknown role 'flux'`) when: a device or sensor id is invalid or repeated,
two sensors of a device share a name, a role is unknown, a `kind` conflicts with the role's kind, or a
device has no sensors.
