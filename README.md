# Nerdpraat Telemetry — Home Assistant Multiplayer

Backend service that collects energy telemetry (grid, solar, consumer, battery)
from multiple Home Assistant instances over WebSocket and exposes it via a
public REST API. Data is stored in MySQL/MariaDB and retained for 24 hours.

## Stack

- **Express** REST API
- **Socket.io** for HA ingestion + live public subscriptions
- **MySQL/MariaDB** via `mysql2`
- **JWT** auth for users (admin / owner roles)
- Per-site **API keys** for HA WebSocket authentication
- Periodic **cleanup cron** enforcing 24h retention
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
| `JWT_SECRET`         | —           | Secret used to sign user JWTs            |
| `JWT_EXPIRES_IN`     | `7d`        | JWT lifetime                             |
| `RETENTION_HOURS`    | `24`        | How long readings are kept               |
| `CLEANUP_INTERVAL_MS`| `3600000`   | How often the cleanup job runs           |
| `CORS_ORIGINS`       | `*`         | Comma-separated allowed origins          |
| `TRUST_PROXY`        | `1`         | Express `trust proxy` (set behind LiteSpeed/nginx/load balancer; `0` to disable) |

## REST API

### Auth (`/api/auth`)
- `POST /register` `{email, password, name?}` → `{token, user}`
- `POST /login` `{email, password}` → `{token, user}`
- `GET /me` *(Bearer)* → current user

### Site management (`/api/sites`) *(Bearer)*
Owners manage their own sites; admins manage all.
- `GET /` → sites visible to the user (with `apiKey`)
- `POST /` `{name, isPublic?, ownerId?}` → create site, returns `apiKey`
- `GET /:siteId`
- `PATCH /:siteId` `{name?, isPublic?, ownerId?, regenerateApiKey?}`
- `DELETE /:siteId`
- `GET /:siteId/entities` → entity configuration
- `PUT /:siteId/entities` `{entities:[{kind, entityId, name?, unit?}]}` — upsert (max 4)
- `GET /:siteId/entities/:kind`

`kind` ∈ `grid | solar | consumer | battery`

### Users (`/api/users`) *(Bearer, admin only)*
- `GET /` → list all users (for the admin owner dropdown)

### Public read (`/api/public`) — no auth
- `GET /sites` → list of public sites
- `GET /sites/:slug` → public site metadata
- `GET /sites/:slug/readings?since=&until=&kind=` → readings (last 24h default)
- `GET /sites/:slug/latest` → latest value per kind

## WebSocket (`/`)

Connect with Socket.io. Two connection modes:

### Owner (Home Assistant integration) — write access
Handshake auth: `{ token: "<site apiKey>" }` (also accepts `apiKey`, query
`?api_key=`, or header `x-api-key`).

Events the client emits:
- `reading` `{kind, value, unit?, ts?}` → ack `{ok}`
- `readings` `[{kind, value, unit?, ts?}, ...]` (max 500) → ack `{ok, stored}`

Server emits:
- `ready` `{siteId, slug, name, readOnly}`
- `reading` (broadcast to other sockets in the site room — for dashboards)

### Public subscriber (dashboard) — read only
Handshake auth: `{ subscribe: "<slugOrId>" }`. Public sites only. Receives the
`ready` and live `reading` broadcasts but cannot ingest.

### Example HA payload

```jsonc
// emit "reading"
{ "kind": "grid", "value": 1234.5, "unit": "W" }
{ "kind": "solar", "value": 800, "unit": "W" }
{ "kind": "consumer", "value": 2000, "unit": "W" }
{ "kind": "battery", "value": -300, "unit": "W" }
```

`ts` is optional (epoch ms or ISO string); defaults to server time.

## Data model

- `users` — id, email, password_hash, name, role(admin|user)
- `sites` — id, name, slug, owner_id, api_key, is_public
- `site_entities` — maps a HA `entity_id` to a `kind` per site (one per kind)
- `readings` — site_id, entity_id, value, unit, recorded_at (indexed; pruned)

## Admin dashboard

A self-contained dashboard is served at **`/admin`** (static HTML + vanilla JS,
no build step). It uses the REST API above with JWT auth stored in
`localStorage`.

- Login / register (first registered user becomes `admin`)
- List, create, edit, and delete sites
- Regenerate site API keys
- Configure the four entity kinds (`grid | solar | consumer | battery`) per
  site
- View latest readings per kind + a 24h line chart (filterable by kind)
- Admin-only: assign site ownership

Open `https://<host>/admin` in a browser and sign in with a registered user.

## Scripts

- `npm start` / `npm run dev` — run server
- `npm run migrate` — apply schema
- `npm run lint` — eslint
