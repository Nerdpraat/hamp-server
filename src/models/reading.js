const { pool } = require('../db/pool');

const KINDS = ['grid', 'solar', 'consumer', 'battery'];

/**
 * Insert a single reading.
 * @param {object} r { siteId, kind, value, unit, recordedAt (Date|undefined) }
 */
async function add({ siteId, kind, value, unit = 'W', recordedAt }) {
  const [result] = await pool.query(
    `INSERT INTO energy_readings (site_id, kind, value, unit, recorded_at)
     VALUES (?, ?, ?, ?, ?)`,
    [siteId, kind, value, unit, recordedAt ? new Date(recordedAt) : new Date()]
  );
  return result.insertId;
}

/**
 * Bulk insert many readings.
 * @param {Array} readings array of { siteId, kind, value, unit, recordedAt }
 */
async function addMany(readings) {
  if (!readings.length) return 0;
  const now = new Date();
  const values = readings.map((r) => [
    r.siteId,
    r.kind,
    r.value,
    r.unit || 'W',
    r.recordedAt ? new Date(r.recordedAt) : now,
  ]);
  const placeholders = readings.map(() => '(?, ?, ?, ?, ?)').join(', ');
  const flat = values.flat();
  const [result] = await pool.query(
    `INSERT INTO energy_readings (site_id, kind, value, unit, recorded_at) VALUES ${placeholders}`,
    flat
  );
  return result.affectedRows;
}

/**
 * Get readings for a site within a window (default last 24h).
 */
async function getForSite(siteId, { sinceMs, untilMs, kind } = {}) {
  const until = untilMs ? new Date(untilMs) : new Date();
  const since = sinceMs ? new Date(sinceMs) : new Date(Date.now() - 24 * 3600 * 1000);
  const params = [siteId, since, until];
  let kindClause = '';
  if (kind) {
    kindClause = ' AND kind = ?';
    params.push(kind);
  }
  const [rows] = await pool.query(
    `SELECT id, site_id, kind, value, unit, recorded_at
     FROM energy_readings
     WHERE site_id = ? AND recorded_at >= ? AND recorded_at <= ?${kindClause}
     ORDER BY recorded_at ASC, kind ASC`,
    params
  );
  return rows;
}

/**
 * Latest reading per kind for a site.
 */
async function latestPerKind(siteId) {
  const [rows] = await pool.query(
    `SELECT r.id, r.site_id, r.kind, r.value, r.unit, r.recorded_at
     FROM energy_readings r
     INNER JOIN (
       SELECT kind, MAX(recorded_at) AS max_ts
       FROM energy_readings WHERE site_id = ? GROUP BY kind
     ) m ON m.kind = r.kind AND m.max_ts = r.recorded_at
     WHERE r.site_id = ?`,
    [siteId, siteId]
  );
  return rows;
}

async function deleteOlderThan(hours) {
  const cutoff = new Date(Date.now() - hours * 3600 * 1000);
  const [result] = await pool.query('DELETE FROM energy_readings WHERE recorded_at < ?', [cutoff]);
  return result.affectedRows;
}

function sanitize(r) {
  if (!r) return null;
  return {
    id: r.id,
    siteId: r.site_id,
    kind: r.kind,
    value: Number(r.value),
    unit: r.unit,
    recordedAt: r.recorded_at,
  };
}

module.exports = { KINDS, add, addMany, getForSite, latestPerKind, deleteOlderThan, sanitize };
