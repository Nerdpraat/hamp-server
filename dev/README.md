# Local test stack

Home Assistant, the Energy Hub server and a simulator in one `docker compose`. No clicking needed.

```
cd dev
docker compose up --build
```

The first start takes a minute or two while Home Assistant boots. The `simulator` container then:

1. creates the Home Assistant user,
2. creates demo sensors for a household: grid, **two solar inverters**, **two batteries**, gas, five
   smart plugs, a heat pump and a water meter,
3. fills the Home Assistant energy dashboard from them (grid, solar, batteries, gas, plugs),
4. adds the **Energy Hub** integration (server `http://energy-hub:8000`, site `home`) and accepts
   everything the energy dashboard offers,
5. adds a **heat pump** and a **water meter** through the integration's options flow, the way a user
   would, since they are not part of the energy dashboard,
6. updates the sensor values once per second.

| What | URL | Login |
|---|---|---|
| Home Assistant | http://localhost:8123 | `dev` / `dev` |
| Energy Hub dashboard | http://localhost:8000 | access key `dev-dashboard-key` |

If a port is taken: `HUB_PORT=18000 HA_PORT=18123 docker compose up`.

The dev server configuration is in `dev/config/` (the four files described in
`docs/deployment.md`). The secrets in it are public development values. It is mounted read-only, and
`devices.yaml` is a copy of `server/config.example/devices.yaml` (a test keeps them identical).

Things to try:

* **Devices** tab on the Energy Hub dashboard: the heat pump (power, energy, two temperatures, COP)
  and the water meter, with a chart per sensor.
* **Now** tab: values update live.
* In Home Assistant: Settings, Devices & services, **Energy Hub**, Configure. Add a device (for
  example another Battery), edit the heat pump, or remove a device, and watch it appear or disappear
  on the dashboard without restarting anything.
* Change the energy dashboard in Home Assistant (Settings, Dashboards, Energy). The change follows to
  the server automatically.
* Add a device type to `dev/config/devices.yaml` (and the same to `server/config.example/devices.yaml`),
  `docker compose restart energy-hub homeassistant`, and it is offered in the options flow.
* Edit the integration in `client/custom_components/energy_hub`, then
  `docker compose restart homeassistant`. The folder is mounted read-only into the container.
* `docker compose logs -f homeassistant | grep energy_hub` shows the integration's debug log.

Notes:

* Only live data is stored, so the history charts fill up while the stack runs. To see full days
  right away, use `tools/seed_demo.py` against a local server (see the main README).
* The simulated household follows the clock of your machine, so solar only appears during the day.
* On SELinux hosts the bind mounts use `:z`. This is harmless elsewhere.
* Stop with `docker compose down`. Reset everything, including Home Assistant's data and the stored
  history, with `docker compose down -v`.
