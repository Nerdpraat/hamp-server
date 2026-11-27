
devices.yaml

```yaml
devices:
    - name: "Solar Inverter"
      sensors:
        - name: "Power"
          selectors:
            device_class: power

        - name: "Energy"
          selectors:
            device_class: energy
    - name: "Heatpump"
      [...]





```


auth.yaml

```yaml
keys:
    key-dru05-ha: ""

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