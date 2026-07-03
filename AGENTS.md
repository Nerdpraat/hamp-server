# AGENTS.md

Notes for AI agents working in this repo.

## Commands
- `npm install` — install deps
- `npm run migrate` — apply `src/db/schema.sql` (idempotent, splits on `;\n`)
- `npm start` / `npm run dev` — run the server (uses `.env`)
- `npm run lint` — eslint (flat config `eslint.config.mjs`)
- `npm run typecheck` — no-op (plain JS, no TS)

## Architecture
- `src/server.js` — express app + http server + raw WebSocket + cleanup job bootstrap
- `src/config/index.js` — env loading
- `src/db/` — `pool.js` (mysql2 pool), `schema.sql`, `migrate.js`
- `src/auth/` — `password.js` (bcrypt), `jwt.js`, `middleware.js` (authRequired/adminRequired)
- `src/middleware/site.js` — loadSite, requireSiteAccess, resolvePublicSite, withTransaction
- `src/models/` — user, site, entity, reading (plain functions over pool)
- `src/routes/` — `auth.js`, `sites.js` (auth, CRUD, entity config), `public.js` (public read)
- `src/ws/raw.js` — raw WebSocket (RFC 6455) auth + ingest
- `src/jobs/cleanup.js` — periodic retention delete

## Conventions
- CommonJS, no TypeScript. Validate input with `zod`.
- Models expose `sanitize()` to strip internal column names / secrets before output.
- First registered user auto-promoted to `admin` (bootstrap).
- Readings are stored as UTC (`timezone: 'Z'` on the pool).
- `kind` is always one of `grid | solar | consumer | battery`.

## Local DB (no sudo) for testing
```bash
D=/tmp/kilo/mariadb
mariadb-install-db --datadir=$D --auth-root-authentication-method=normal --user=$USER
/usr/sbin/mariadbd --datadir=$D --socket=$D/mysql.sock --port=3307 --user=$USER &
mysql --socket=$D/mysql.sock -u root -e "CREATE DATABASE telemetry; \
  CREATE USER 'telemetry'@'127.0.0.1' IDENTIFIED BY 'changeme'; \
  GRANT ALL ON telemetry.* TO 'telemetry'@'127.0.0.1';"
# then: DB_PORT=3307 in .env, npm run migrate, npm start
```
