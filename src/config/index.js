const path = require('path');
require('dotenv').config({ path: path.resolve(process.cwd(), '.env') });

function required(key, fallback) {
  const v = process.env[key];
  if (v === undefined || v === '') {
    if (fallback !== undefined) return fallback;
    throw new Error(`Missing required env var: ${key}`);
  }
  return v;
}

/**
 * Parse TRUST_PROXY into a value Express accepts for `trust proxy`.
 * Accepts: "1"/"true" -> 1, "0"/"false" -> false, a number, or a comma list.
 * Defaults to 1 (behind a reverse proxy) when unset or empty.
 */
function parseTrustProxy(raw) {
  if (raw === undefined || raw === '') return 1;
  const v = raw.trim().toLowerCase();
  if (v === 'true' || v === '1') return 1;
  if (v === 'false' || v === '0') return false;
  if (/^\d+$/.test(v)) return parseInt(v, 10);
  return raw.trim();
}

const corsOrigins = (process.env.CORS_ORIGINS || '*')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

module.exports = {
  env: process.env.NODE_ENV || 'development',
  port: parseInt(process.env.PORT || '3000', 10),
  db: {
    host: process.env.DB_HOST || '127.0.0.1',
    port: parseInt(process.env.DB_PORT || '3306', 10),
    user: process.env.DB_USER || 'telemetry',
    password: process.env.DB_PASSWORD || '',
    database: process.env.DB_NAME || 'telemetry',
  },
  jwt: {
    secret: required('JWT_SECRET', process.env.SESSION_SECRET || 'dev-insecure-secret-change-me'),
    expiresIn: process.env.JWT_EXPIRES_IN || '7d',
  },
  retention: {
    hours: parseInt(String(process.env.RETENTION_HOURS || '24').replace(/\D/g, '') || '24', 10),
    cleanupIntervalMs: parseInt(String(process.env.CLEANUP_INTERVAL_MS || '3600000').replace(/\D/g, '') || '3600000', 10),
  },
  cors: {
    origins: corsOrigins,
  },
  // Number of proxies between client and server (LiteSpeed / nginx / load
  // balancer). Used for Express `trust proxy` so req.ip + X-Forwarded-For are
  // honored (required by express-rate-limit and morgan remote-addr).
  trustProxy: parseTrustProxy(process.env.TRUST_PROXY),
};
