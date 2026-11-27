
devices.yaml

```yaml
# devices.yaml
devices:
  - id: heatpump
    name: "Heatpump"
    description: "Heating system power draw, energy usage, and water temperature."
    category: climate
    sensors:
      - id: power
        name: "Power"
        description: "Current electrical consumption."
        selector:
          domain: sensor
          device_class: power

      - id: energy
        name: "Energy"
        description: "Total electrical energy consumed."
        selector:
          domain: sensor
          device_class: energy

      - id: supply_temp
        name: "Supply Temperature"
        description: "Outgoing water temperature."
        selector:
          domain: sensor
          device_class: temperature

      - id: supply_temp
        name: "Supply Temperature"
        description: "Outgoing water temperature."
        selector:
          domain: sensor
          device_class: temperature

      - id: cop
        name: "COP"
        description: "Current Coefficient of Performance."
        selector:
          domain: sensor

  - id: solar_inverter
    name: "Solar Inverter"
    description: "PV generation and total solar yield."
    category: energy
    sensors:
      - id: power
        name: "Power"
        description: "Current solar power output."
        selector:
          domain: sensor
          device_class: power

      - id: energy
        name: "Energy"
        description: "Total solar energy produced."
        selector:
          domain: sensor
          device_class: energy

  - id: battery
    name: "Battery"
    description: "Home battery storage level and charge/discharge flow."
    category: energy
    sensors:
      - id: state_of_charge
        name: "State of Charge"
        description: "Current battery percentage."
        selector:
          domain: sensor
          device_class: battery

      - id: power
        name: "Power"
        description: "Current charging or discharging power."
        selector:
          domain: sensor
          device_class: power

      - id: energy_charged
        name: "Energy Charged"
        description: "Total energy stored into the battery."
        selector:
          domain: sensor
          device_class: energy

      - id: energy_discharged
        name: "Energy Discharged"
        description: "Total energy drawn from the battery."
        selector:
          domain: sensor
          device_class: energy

  - id: gasmeter
    name: "Gas Meter"
    description: "Main gas utility meter."
    category: utilities
    sensors:
      - id: consumption
        name: "Gas Consumption"
        description: "Total gas consumed."
        selector:
          domain: sensor
          device_class: gas

  - id: watermeter
    name: "Water Meter"
    description: "Main water utility meter."
    category: utilities
    sensors:
      - id: consumption
        name: "Water Consumption"
        description: "Total water consumed."
        selector:
          domain: sensor
          device_class: water

      - id: flow_rate
        name: "Flow Rate"
        description: "Current live water flow."
        selector:
          domain: sensor
          device_class: volume_flow_rate

  - id: electricitymeter
    name: "Electricity Meter"
    description: "Smart meter grid import and export."
    category: utilities
    sensors:
      - id: power_import
        name: "Power Import"
        description: "Current power drawn from the grid."
        selector:
          domain: sensor
          device_class: power

      - id: power_export
        name: "Power Export"
        description: "Current power fed back to the grid."
        selector:
          domain: sensor
          device_class: power

      - id: energy_import
        name: "Energy Import"
        description: "Total energy imported from the grid."
        selector:
          domain: sensor
          device_class: energy

      - id: energy_export
        name: "Energy Export"
        description: "Total energy exported to the grid."
        selector:
          domain: sensor
          device_class: energy

  - id: consumer_device
    name: "Consumer Device"
    description: "Generic appliances or smart plugs for tracking power consumption."
    category: appliances
    sensors:
      - id: power
        name: "Power"
        description: "Live power draw from one or more devices."
        selector:
          domain: sensor
          device_class: power
          multiple: true

      - id: energy
        name: "Energy"
        description: "Total energy consumed by one or more devices."
        selector:
          domain: sensor
          device_class: energy
          multiple: true

```


auth.yaml

```yaml
keys:
    key-dru05-ha: ""
    key-dru06-ha: ""
    key-dru07-ha: ""
    key-dru08-ha: ""

```

topology.yaml

```yaml
sites:
    - name: "DRU05"
      key: key-dru05-ha
    - name: "DRU06"
      key: key-dru06-ha
    - name: "DRU07"
      key: key-dru07-ha
    - name: "DRU08"
      key: key-dru08-ha


```