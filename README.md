# Nerdpraat Telemetry — Home Assistant Multiplayer

Backend service that collects energy/power telemetry from multiple Home
Assistant instances over WebSocket and exposes it via a public REST API. Data
is stored in MySQL/MariaDB and retained for a configurable window (24h by
default).

Each site groups an arbitrary set of **entities** (e.g. `sensor.power_grid`,
`sensor.power_solar`). Entities are auto-registered on first ingest; the admin
dashboard can override their display `name` and `unit`.

## Stack

- **Express** REST API (JWT auth for users, per-site API keys for ingestion)
- **Socket.io** at `/` for HA ingestion + live public subscriptions
- **Raw WebSocket** at `/ws` for plain WS clients (RFC 6455)
- **MySQL/MariaDB** via `mysql2`
- Periodic **cleanup cron** enforcing retention
- Built-in **admin dashboard** at `/admin` (static, no build step)

## Requirements

- Node.js 20+
- MySQL or MariaDB 10.6+

## Setup

```bash
cp .env.example .env       # then edit DB credentials + JWT_SECRET
npm install
npm run migrate            # create / update schema (idempotent)
npm start                  # production
npm run dev                # auto-reload with node --watch
```

The **first** user registered becomes an `admin` (bootstrap). All others are
regular `user`s. Admins can manage every site; users only manage their own.

## Environment

| Var                  | Default     | Description                              |
| -------------------- | ----------- | ---------------------------------------- |
| `PORT`               | `3000`      | HTTP port                                |
| `DB_HOST/PORT/USER/PASSWORD/NAME` | — | MySQL connection             |
| `JWT_SECRET`         | `dev-insecure-secret-change-me` | Secret used to sign user JWTs |
| `JWT_EXPIRES_IN`     | `7d`        | JWT lifetime                             |
| `RETENTION_HOURS`    | `24`        | How long readings are kept               |
| `CLEANUP_INTERVAL_MS`| `3600000`   | How often the cleanup job runs           |
| `CORS_ORIGINS`       | `*`         | Comma-separated allowed origins          |
| `TRUST_PROXY`        | `1`         | Express `trust proxy` (set behind LiteSpeed/nginx/load balancer; `0` to disable) |

## Authentication

The API uses two independent auth mechanisms:

1. **JWT Bearer** — for the admin dashboard and all `/api` management routes.
   Obtained from `POST /api/auth/login` or `POST /api/auth/register`. Send as:
   ```
   Authorization: Bearer <token>
   ```

2. **Site API key** — for ingestion (WebSocket). Each site has a key like
   `np_dc99090c424d0abf221c3023c101666d91b2df9bea6c90e7`. Regenerate it from
   the admin dashboard (`PATCH /api/sites/:siteId` with `regenerateApiKey:true`).

## REST API

Base path: `/api`. All management routes require a `Bearer` JWT (except the
`/api/public` read endpoints and `/api/auth`).

### Auth (`/api/auth`)
| Method | Path        | Auth  | Body                          | Returns                  |
| ------ | ----------- | ----- | ----------------------------- | ------------------------ |
| POST   | `/register` | none  | `{email, password, name?}`    | `{token, user}` (201)    |
| POST   | `/login`    | none  | `{email, password}`           | `{token, user}`          |
| GET    | `/me`       | Bearer | —                            | `{user}`                 |

- `password` is 8–128 chars. First registered user is promoted to `admin`.
- Login is rate-limited (30 attempts / 15 min / IP).

### Site management (`/api/sites`) *(Bearer)*
Owners manage their own sites; admins manage all. Sanitized site objects
include the `apiKey`; `slug` is auto-derived from the name.

| Method | Path                  | Body                                                    | Returns                |
| ------ | --------------------- | ------------------------------------------------------- | ---------------------- |
| GET    | `/`                   | —                                                       | `{sites:[...]}`        |
| POST   | `/`                   | `{name, isPublic?, ownerId?}`                           | `{site}` (201)         |
| GET    | `/:siteId`            | —                                                       | `{site}`               |
| PATCH  | `/:siteId`            | `{name?, isPublic?, ownerId?, regenerateApiKey?}`       | `{site}`               |
| DELETE | `/:siteId`            | —                                                       | `{deleted:true}`       |
| GET    | `/:siteId/entities`   | —                                                       | `{entities:[...]}`     |
| PUT    | `/:siteId/entities`   | `{entities:[{entityId, name?, unit?}]}` (max 500)       | `{entities:[...]}`     |
| DELETE | `/:siteId/entities`  | `{entityId}` (body) or `?entityId=`                     | `{deleted:true}`       |

`ownerId` is admin-only. Entities are keyed by `entityId` (a Home Assistant
entity id, e.g. `sensor.power_grid`); there is no fixed `kind` limit.

### Users (`/api/users`) *(Bearer, admin only)*
| Method | Path | Returns            |
| ------ | ---- | ------------------ |
| GET    | `/`  | `{users:[...]}`    |

### Public read (`/api/public`) — no auth
Public sites only. `slug` identifies a site.

| Method | Path                          | Query                          | Returns              |
| ------ | ----------------------------- | ------------------------------ | -------------------- |
| GET    | `/sites`                      | —                              | `{sites:[...]}`      |
| GET    | `/sites/:slug`                | —                              | `{site}`             |
| GET    | `/sites/:slug/readings`       | `since`,`until` (ms), `entityId` | `{readings:[...]}` |
| GET    | `/sites/:slug/latest`         | —                              | `{latest:[...]}`     |

`readings` defaults to the last 24h. `latest` returns the most recent reading
per entity (deduped by primary key, so tied timestamps don't duplicate).

## WebSocket ingestion

There are **two** WebSocket endpoints. Both accept the same payload shape and
use the same site API key; pick whichever your client supports.

### Payload shape (both endpoints)

```jsonc
// single reading
{ "type": "reading", "reading": {
  "entityId": "sensor.power_grid",
  "value": 1842.5,
  "unit": "W",     // optional, defaults to "W"
  "ts": 1751580000 // optional: epoch ms or ISO string; defaults to server time
}}

// batch (max 500)
{ "type": "readings", "readings": [
  { "entityId": "sensor.power_grid",   "value": 1842.5, "unit": "W" },
  { "entityId": "sensor.power_solar",  "value": 3120.0, "unit": "W" },
  { "entityId": "sensor.power_battery","value": -650.0, "unit": "W" },
  { "entityId": "sensor.power_consumer","value": 4312.5,"unit": "W" }
]}
```

On success the server replies with an acknowledgement:
- Socket.io: callback `ack({ ok: true })` or `ack({ ok: true, stored: N })`
- Raw WS: `{"type":"ack","ok":true}` or `{"type":"ack","ok":true,"stored":N}`

Errors come back as `{ ok:false, error, details? }` / `{"type":"error",...}`.
Unknown/new entities are auto-registered on first ingest.

### 1. Raw WebSocket — `/ws` (RFC 6455)

For plain WS clients (Postman "WebSocket", `wscat`, browser `WebSocket`,
microcontroller clients). Connect to:

```
ws://<host>:<port>/ws
```

Authenticate with **any one** of:
- Header: `x-api-key: np_...`
- Query: `ws://<host>:<port>/ws?api_key=np_...`
- Auth frame as the first message: `{"type":"auth","api_key":"np_..."}`

After auth the server sends `{"type":"ready","siteId":..,"slug":..,"name":..,"readOnly":false}`.
Unauthenticated sockets are closed with code `4001`.

`wscat` example:
```bash
wscat -c "ws://localhost:3000/ws?api_key=np_dc99090c424d0abf221c3023c101666d91b2df9bea6c90e7"
# then type:
{"type":"reading","reading":{"entityId":"sensor.power_grid","value":1842.5,"unit":"W"}}
```

### 2. Socket.io — `/`

For clients using the Socket.io protocol (the admin dashboard, a Node
integration using `socket.io-client`). Connect to:

```
http://<host>:<port>
```

Handshake auth (owner, write access): `{ token: "<site apiKey>" }` (also
accepts `apiKey`, query `?api_key=`, or header `x-api-key`).

Events emitted by the client:
- `reading` `{entityId, value, unit?, ts?}` → ack `{ok}`
- `readings` `[{entityId, value, unit?, ts?}, ...]` (max 500) → ack `{ok, stored}`

Server emits:
- `ready` `{siteId, slug, name, readOnly}`
- `reading` (broadcast to other sockets in the site room — for dashboards)

#### Public subscriber (read only)
Handshake auth: `{ subscribe: "<slugOrId>" }`. Public sites only. Receives the
`ready` and live `reading` broadcasts but cannot ingest.

Node example:
```js
const { io } = require('socket.io-client');
const socket = io('http://localhost:3000', {
  auth: { token: 'np_dc99090c424d0abf221c3023c101666d91b2df9bea6c90e7' },
});
socket.on('ready', (info) => console.log('ready', info));
socket.on('connect_error', (err) => console.log('conn error:', err.message));
socket.emit('reading', { entityId: 'sensor.power_grid', value: 123.4, unit: 'W' },
  (ack) => console.log(ack));
```

## Data model

- `users` — id, email, password_hash, name, role(admin|user)
- `sites` — id, name, slug, owner_id, api_key, is_public
- `site_entities` — (site_id, entity_id) unique; name + unit overrides, auto-populated on first ingest
- `readings` — site_id, entity_id, value, unit, recorded_at (indexed; pruned by retention)

## Admin dashboard

A self-contained dashboard is served at **`/admin`** (static HTML + vanilla JS,
no build step). It uses the REST API above with JWT auth stored in
`localStorage`.

- Login / register (first registered user becomes `admin`)
- List, create, edit, and delete sites
- Regenerate site API keys
- Configure entity display `name` / `unit` per site
- View latest readings per entity + a 24h line chart (filterable by entity)
- Admin-only: assign site ownership

Open `https://<host>/admin` in a browser and sign in with a registered user.

## Scripts

- `npm start` / `npm run dev` — run server
- `npm run migrate` — apply schema
- `npm run lint` — eslint
