# Energy Hub ingest protocol, version 2

A **client** pushes the readings of one site to the **server** over a single WebSocket. A client
needs only a WebSocket library and JSON. Nothing in this document is specific to Home Assistant, so
you can build your own client (any language) from it.

Shipped with this document:

| What | Where |
|---|---|
| JSON Schemas for every message | [`docs/protocol/schemas/`](protocol/schemas/) |
| Example conversations | [`docs/protocol/examples/`](protocol/examples/) |
| A small reference client (Python, only `websockets`) | `tools/reference_client.py` |
| A conformance check you can point at any server | `tools/protocol_check.py` |

The words MUST, SHOULD and MAY are used in the usual way.

## 1. Transport and authentication

* Endpoint: `GET /ws/ingest`, upgraded to a WebSocket (`wss://` in production). All frames are
  UTF-8 JSON **text** frames.
* Authentication: `Authorization: Bearer <site key>` on the handshake. The key is the site's entry
  in the server's `auth.yaml`. A wrong or missing key gives **HTTP 403** and the socket is never
  upgraded. Do not retry faster than every 5 minutes.
* One connection per key. A new connection replaces the old one, which is closed with code `4000`.
  A client that receives `4000` MUST wait at least 60 s before reconnecting, because another client
  uses the same key.
* Limits (also sent in `hello.limits`):
  * 256 KiB per frame, otherwise the server closes with code `1009`.
  * 1000 items per `states` message.
  * 100 messages per second, otherwise `error rate_limited` and the server closes with code `4429`.
* Liveness: send WebSocket protocol pings every 30 s. The application messages `ping` and `pong`
  exist for clients that cannot send protocol pings.

## 2. Message flow

```text
client connects with its key
server -> hello            version, site, server_time, limits, device catalogue
client -> config           which device instances exist and where their values come from
server -> ack (config)     or error (invalid_config)
client -> states ...       readings and static values;  server -> ack (states) for each message
client -> config           at any time: replaces the previous config
```

* Unknown fields in any message MUST be ignored by both sides, so additive changes do not need a
  new version.
* A client MUST refuse a `hello` whose `version` it does not know. The server speaks exactly one
  version (2).

## 3. Messages from the server

### `hello`

```json
{"type": "hello", "version": 2,
 "site": {"id": "dru05", "name": "DRU05"},
 "server_time": 1795805160.2,
 "limits": {"max_message_bytes": 262144, "max_items_per_batch": 1000,
            "max_messages_per_second": 100, "max_instances": 100},
 "devices": [
   {"id": "battery", "name": "Battery", "description": "Home battery storage level and charge/discharge flow.",
    "category": "energy",
    "sensors": [
      {"id": "state_of_charge", "name": "State of Charge", "description": "Current battery percentage.",
       "kind": "gauge", "unit": "%",
       "selector": {"entity": {"domain": "sensor", "device_class": "battery"}}},
      {"id": "energy_charged", "name": "Energy Charged", "description": "Total energy stored into the battery.",
       "kind": "counter", "unit": "kWh",
       "selector": {"entity": {"domain": "sensor", "device_class": "energy"}}},
      {"id": "capacity", "name": "Capacity", "description": "Usable battery capacity in Wh, entered as a number.",
       "kind": "gauge", "unit": "Wh",
       "selector": {"number": {"min": 0, "max": 100000, "step": 0.1,
                               "unit_of_measurement": "Wh", "mode": "box"}}}
    ]}
 ]}
```

* `devices` is the **catalogue** from the server's `devices.yaml`. A client lets its user choose a
  device type and then fill its sensor slots. Internal server details (such as how the dashboard
  uses a sensor) are not sent.
* `kind`: `counter` is a cumulative meter that only increases (kWh, m3). `gauge` is an
  instantaneous value (W, °C, %).
* `unit`: the unit the server expects. **The client converts to it before sending.** An empty unit
  means no conversion.
* `selector`: the slot's **native Home Assistant selector**, sent exactly as the server's
  `devices.yaml` has it, with exactly one type. A client can pass it straight to its input widget:
  * `entity`: the slot is filled with one entity, for example
    `{"entity": {"domain": "sensor", "device_class": "power"}}`. `domain` and `device_class` use
    Home Assistant's names and may be a string or a list. Other clients MAY ignore them.
  * `number`, `select` and `boolean`: a **static slot**. The user enters a value instead of choosing
    an entity. `number` has the optional settings `min`, `max`, `step` (a number or `"any"`),
    `unit_of_measurement` and `mode` (`box` or `slider`). `select` has `options` (strings or
    `{"value", "label"}` objects) and optionally `custom_value`. `boolean` has no settings.
    See [Static slots](#static-slots).
* `server_time` (Unix seconds) lets a client notice clock skew.

### `ack`

```json
{"type": "ack", "for": "config", "instances": 2, "sensors": 7,
 "removed": {"instances": ["battery-2"], "sensors": ["battery-1.power"]}}
```

```json
{"type": "ack", "for": "states", "accepted": 15, "rejected": 1, "unknown_keys": ["x.y"]}
```

* `unknown_keys` (at most 20) means the server does not know those keys. The client SHOULD send
  `config` again, at most once every 30 s.
* `rejected` counts dropped items. An entity reading is dropped when it is not a finite number, the
  key is unknown, the timestamp is more than 1 h old or more than 60 s in the future, or a counter
  reading is older than the previous one. A static value is dropped when it does not fit the slot's
  selector or could not be stored.
* The server handles the items of a message, and the messages of a connection, in the order it
  receives them, and sends one `ack` per `states` message in that order. For a static value
  `accepted` means it is **stored**: a single-item message with `accepted: 1` confirms that value.

### `error`

```json
{"type": "error", "code": "invalid_config", "message": "unknown sensor 'foo' for device 'battery'",
 "path": "instances[0].sensors.foo"}
```

| code | meaning | connection |
|---|---|---|
| `bad_json` | the frame is not JSON | stays open |
| `bad_message` | the JSON is not an object | stays open |
| `unknown_type` | unknown `type` | stays open |
| `bad_states` | `items` is missing, not a list, or too long | stays open |
| `invalid_config` | the whole `config` was rejected and the previous one stays in force. `path` points at the first problem | stays open |
| `rate_limited` | too many messages | closed with `4429` |

### `pong`

```json
{"type": "pong"}
```

Answer to `ping`.

## 4. Messages from the client

### `config`

Replaces everything the server knew about this site.

```json
{"type": "config", "currency": "EUR",
 "instances": [
   {"id": "battery-1", "device": "battery", "name": "Battery 1",
    "sensors": {
      "state_of_charge": {"source": "sensor.b1_soc"},
      "power": {"source": "sensor.b1_power"},
      "energy_charged": {"source": "sensor.b1_in"},
      "capacity": {"source": "static"}}},
   {"id": "electricity-meter", "device": "electricitymeter", "name": "Electricity Meter",
    "sensors": {
      "energy_import": {"source": "sensor.p1_import"},
      "price_import": {"source": "sensor.tariff"}}}
 ]}
```

* **Instance.** `id` matches `^[a-z0-9][a-z0-9_-]{0,39}$`, is unique within the site and MUST stay
  stable: a new id is a new history. `device` is a device `id` from the catalogue. `name` is 1 to 80
  characters. A site may have any number of instances of one device type, up to
  `limits.max_instances` in total. Each instance has at least one sensor.
* **Sensor entry.** The key is a sensor `id` of that device. The value is `{"source": "<string>"}`:
  * for an `entity` slot, `source` says where the readings come from (Home Assistant clients send
    the entity id). It is stored but never shown in the dashboard. The same `source` may fill
    several sensors.
  * for a static slot (`number`, `select` or `boolean` selector), `source` MUST be the marker
    `"static"`. It is not an entity id. The `config` message does not carry the value, see
    [Static slots](#static-slots). The marker is refused on an `entity` slot, and an entity id is
    refused on a static slot.

  A slot that has no entry simply has no data.
* `currency`: an ISO code of up to 8 characters, used to format costs.
* **Effect.** Instances and sensors that were in the previous `config` but are missing from this
  one are **deleted together with their stored history**. The `ack` lists what was removed:
  `removed.instances` are the ids of instances that are gone, `removed.sensors` the keys of sensors
  that were taken out of instances that still exist (the sensors of a removed instance are not
  repeated there). A static slot that stays in the config keeps its stored value when `config` is
  sent again.

### Keys

Every registered sensor, entity or static, gets the key **`<instance id>.<sensor id>`**, for example
`battery-1.power` or `battery-1.capacity`. The `states` message uses keys.

### `states`

```json
{"type": "states", "items": [
  {"key": "battery-1.power", "ts": 1795805160.2, "value": -1234.5},
  {"key": "electricity-meter.energy_import", "ts": 1795805160.2, "value": 1092.84}]}
```

```json
{"type": "states", "items": [
  {"key": "battery-1.capacity", "ts": 1730000000, "value": 13500},
  {"key": "heater-1.enabled", "ts": 1730000000, "value": false},
  {"key": "heater-1.mode", "ts": 1730000000, "value": "auto"}]}
```

* An **entity** reading: `value` MUST be a finite JSON **number** (strings and booleans are
  rejected), in the sensor's catalogue unit. `ts` is Unix seconds (a float is fine) and optional;
  the server clock is used when it is missing. Per key, timestamps MUST NOT go backwards.
* A **static** value: `value` is a JSON number, string or boolean, according to the slot's selector.
  `ts` is optional and ignored.

### Static slots

A slot with a `number`, `select` or `boolean` selector holds a value the user entered, such as the
capacity of a battery. It has no entity and no history, only a latest value.

* **Register** it in `config` with `{"source": "static"}`. The slot then has a key.
* **Send** the value in `states` when the user submits or changes it. The client does not need to
  repeat it periodically. The server keeps the latest value, also across restarts and reconnects,
  and a re-sent `config` does not clear it.
* **Validation**, against the selector the server advertised in `hello`:
  * `number`: a finite JSON number, not a boolean, within `min` and `max` when they are set. `step`
    is only a hint for the input box and is not enforced. Zero is valid.
  * `select`: a string that is one of the option values, or any string when `custom_value` is true.
  * `boolean`: JSON `true` or `false`. `0`, `1` and strings are rejected. `false` is valid.
* **Confirm** each value by sending it alone: the client SHOULD send a pending static update in a
  single-item `states` message, so `accepted: 1` confirms that value and `rejected: 1` says it was
  refused.
* **Retry.** A client keeps a changed value until it was accepted and sends it again after a
  reconnect (after `config`). Retries may repeat a value, which is harmless. If several changes are
  pending for one slot, send them oldest first: the last one received is the one that is kept.

### `ping`

```json
{"type": "ping"}
```

## 5. How the server stores values

The server never keeps raw readings of an entity, only one bucket per key per minute. A static slot
keeps one value, the latest one that was accepted.

* **Counter:** the sum of the increases. The first reading has no increase. A fall to below 90% of
  the previous value is a meter reset, and the new value is the increase. A smaller fall is noise:
  no increase, and the baseline follows the meter. A gap of more than 1 h between two readings is
  not counted, so an outage does not show up as one huge increase.
* **Gauge:** minimum, average, maximum and last value.

## 6. Client guidance

* **Throttle.** At most one reading per key every few seconds (5 s is a good default) and send only
  the latest value. Skip unchanged values, **but send every key at least once per 60 s** so that
  charts have no gaps.
* **Reconnect.** Exponential backoff from 1 s to 60 s with jitter. Always send `config` first after
  reconnecting.
* **Offline buffer.** Keep a bounded buffer (for example the last 120 batches) and flush it in order
  after the `config` ack. Counters are cumulative, so keeping only the latest value per key is also
  acceptable.
* **Static values.** Send them when the user submits or changes them, not periodically. Keep an
  unconfirmed value and send it again after reconnecting, see [Static slots](#static-slots).
* **Never** send readings for a key that was not declared in `config`.

## 7. Versioning

Additive changes (new optional fields, new catalogue entries, new error codes) keep `version: 2`.
Anything else raises the version.

## 8. Checking a client or a server

```text
python tools/protocol_check.py ws://localhost:8000/ws/ingest <site key>
```

runs scripted scenarios against a server (authentication failure, replacement by a second
connection, every error code, limits) and validates every message the server sends against the
schemas. `tools/reference_client.py` is a complete, small client that follows this document.
