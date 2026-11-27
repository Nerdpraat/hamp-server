# Energy Hub

Collect the energy data of several Home Assistant installations in one place and look at it in a
dashboard that works like Home Assistant's own Energy dashboard, with a site selector.

```
┌──────────────────────┐  WebSocket (wss)   ┌────────────────────────────┐  HTTP / WS   ┌────────┐
│ Home Assistant       │ ─────────────────► │ Energy Hub server (Python) │ ◄─────────── │  User  │
│ custom integration   │  site key          │ FastAPI + SQLite           │  shared key  └────────┘
│ (one per site)       │                    │ 1-minute buckets, 7 days   │
└──────────────────────┘                    └────────────────────────────┘
        ▲                                              │
        └── device catalogue (devices.yaml) ◄──────────┘
```

* **Server** (`server/`): receives readings, stores them per minute in SQLite, deletes them after
  7 days (configurable) and serves the dashboard. What a site can send is defined by a **device
  catalogue**, `devices.yaml`: device types such as Battery, Solar Inverter, Electricity Meter or
  Heatpump, each with named sensor slots.
* **Client** (`client/custom_components/energy_hub`): a Home Assistant integration. It reads your
  energy dashboard, so onboarding needs no entity picking. In its options you add any other device
  from the catalogue: pick the device type, name it, and assign one entity to each sensor slot.
* **Dashboard**: Overview (all sites at a glance), Summary, Electricity, Gas, Devices and Now (live). Pick the site at the top right
  and the day, week or month at the bottom. While you look at the current period the views update
  themselves when new data arrives and keep your legend selection and scroll position.
* **Protocol**: [docs/protocol.md](docs/protocol.md). It does not depend on Home Assistant, so you
  can write your own client in any language.

## Quick start

### 1. Server

```
mkdir -p config data
cp server/config.example/* config/             # then edit topology.yaml (your sites) and, if you like, devices.yaml
HUB_USER=$(id -u):$(id -g) docker compose up -d --build
```

The server keeps its configuration in four files in one directory (`config/`):

| File | What it holds |
|---|---|
| `config.yaml` | settings: retention (7 days), database path, cookie security, ... All optional |
| `auth.yaml` | **all secrets**: the dashboard key, the session secret and one key per site. Leave a value empty and the server generates it on the first start and writes it into the file |
| `topology.yaml` | the sites. Each site names the key (from `auth.yaml`) that it uses |
| `devices.yaml` | the device catalogue, see [docs/devices.md](docs/devices.md) |

After the first start, open `config/auth.yaml`: it now contains the dashboard key (`ui_key`) and
the key of every site. The server never logs secret values. Details, TLS and SELinux notes are in
[docs/deployment.md](docs/deployment.md).

### 2. Home Assistant (each site)

1. Copy `client/custom_components/energy_hub` to `<ha config>/custom_components/energy_hub`
   (or add this repository to HACS as a custom repository) and restart Home Assistant.
2. Settings, Devices & services, Add integration, **Energy Hub**.
3. Enter the server URL (`https://energy.example.com`) and **this site's key** from `auth.yaml`.
4. Untick anything from the energy dashboard that you do not want to send. Done.

Later, **Configure** on the integration offers:

| Option | What it does |
|---|---|
| Add a device | Pick a device type from the server's catalogue (heat pump, battery, water meter, ...), name it, and assign an entity to each sensor you have. Price slots and battery capacity take a fixed number |
| Edit a device you added | Change the entities of a device you added |
| Remove devices | Stop sending devices. Their stored history on the server is deleted |
| Re-run discovery | Follow the energy dashboard again and bring back every device you removed from it |
| Settings | Minimum seconds between updates per sensor (default 5), follow dashboard changes automatically |

Changes apply immediately, without a reload or a restart. While *follow dashboard changes* is on,
editing the Home Assistant energy dashboard updates what is sent.

## Several batteries, inverters and meters

A device type can be added any number of times. Two batteries, three solar strings or a dozen
smart plugs are simply that many *instances*, each with its own name. The dashboard adds instances
up for totals, the distribution diagram, the energy flow and the self-consumption figures, and also
keeps one series or row per instance. The battery level shown in the distribution diagram is the
mean of all batteries. Discovery creates one instance per solar source, battery and device in your
energy dashboard.

## Dashboard tabs

| Tab | Shows |
|---|---|
| Overview | the landing page: one card per site with live grid (net import/export), solar and battery (power and level) numbers, pushed over a websocket (`/ws/overview`) as they change. An "All sites" card at the top sums them. The Grid value is blue while importing and purple while exporting. Click a site card to open that site's Summary. Each site has a green (connected) or red (disconnected) dot, and hovering over it shows the last connected time. Offline sites are greyed out. A value shows "–" when the site has no fresh sensor for it. Uses the roles `grid_power_import`, `grid_power_export`, `solar_power`, `battery_power` and `battery_soc` |
| Summary | energy distribution, totals with cost, power sources, electricity, gas |
| Electricity | import, export, solar, battery, devices, untracked consumption, energy flow |
| Gas | gas consumption |
| Devices | every device that has no dedicated view, such as a heat pump or a water meter: a headline value and a chart per sensor. New device types in `devices.yaml` show up here without code changes |
| Now | live values of every instantaneous sensor and the last hour |

## Security

* Every site has its own key. A leaked key only affects that site's data. A site can be removed
  or its key replaced by editing `auth.yaml` (an emptied value is generated again).
* The dashboard needs the shared key. The session cookie is signed, `HttpOnly` and
  `SameSite=Lax`. Login attempts are rate limited per client address.
* Home Assistant entity ids are stored on the server but never sent to the browser.
* Strict security headers and a Content-Security-Policy are set. The browser WebSocket checks
  the `Origin` header.
* The shared dashboard key gives access to all sites.

## Writing another client

[docs/protocol.md](docs/protocol.md) describes the WebSocket protocol, with JSON Schemas and
example conversations in `docs/protocol/`. `tools/reference_client.py` is a small complete client
(only `websockets` is needed) and `tools/protocol_check.py` checks any server against the schemas.

## Limitations

* **No backfill.** Only live changes are pushed. While a site is offline there is a gap, and the
  client only buffers the last 120 batches. Totals can differ slightly from Home Assistant's own
  statistics.
* Costs are computed as energy delta times price, so they are approximate.
* Self-consumed solar energy and the energy flow use a simple allocation (solar first covers
  export, then battery charge, then the home), not hour-by-hour accounting.
* A device discovered from the energy dashboard can be removed and re-added, but not edited. To
  change one, remove it and add it by hand.
* Water meters are not read from the energy dashboard. Add them by hand.
* Home Assistant checks only the entity domain in the entity pickers. The integration checks the
  device class itself, but only when the entity already has a state.

## Development

```
python -m venv .venv-ha && .venv-ha/bin/pip install -r requirements-dev.txt

cd server && ../.venv-ha/bin/pytest                      # API, aggregation, config, secrets, protocol v2, ...
node --test tests/js/format_transform.test.mjs           # dashboard data transforms
cd ../client && ../.venv-ha/bin/pytest tests             # mapper, forwarder, config and options flow
cd .. && .venv-ha/bin/pytest tests/protocol tests/e2e -s # schemas, then a real server with a real client, restart and load
```

To test the Home Assistant integration end to end, `cd dev && docker compose up --build` starts a
real Home Assistant, the server and a simulator that sets everything up (see
[dev/README.md](dev/README.md)).

Try the dashboard with simulated data and no Home Assistant:

```
python tools/seed_demo.py --config-dir config --site dru05 --days 3 --state /tmp/sim.json
python tools/fake_site.py --key <site key from auth.yaml> --state /tmp/sim.json     # live 1 Hz data
```

Protocol details: [docs/protocol.md](docs/protocol.md). Device catalogue reference:
[docs/devices.md](docs/devices.md). Deployment notes: [docs/deployment.md](docs/deployment.md).
