const { pool } = require('../db/pool');

/**
 * Insert a single reading.
 * @param {object} r { siteId, entityId, value, unit, recordedAt (Date|undefined) }
 */
async function add({ siteId, entityId, value, unit = 'W', recordedAt }) {
  const [result] = await pool.query(
    `INSERT INTO readings (site_id, entity_id, value, unit, recorded_at)
     VALUES (?, ?, ?, ?, ?)`,
    [siteId, entityId, value, unit, recordedAt ? new Date(recordedAt) : new Date()]
  );
  return result.insertId;
}

/**
 * Bulk insert many readings.
 * @param {Array} readings array of { siteId, entityId, value, unit, recordedAt }
 */
async function addMany(readings) {
  if (!readings.length) return 0;
  const now = new Date();
  const values = readings.map((r) => [
    r.siteId,
    r.entityId,
    r.value,
    r.unit || 'W',
    r.recordedAt ? new Date(r.recordedAt) : now,
  ]);
  const placeholders = readings.map(() => '(?, ?, ?, ?, ?)').join(', ');
  const flat = values.flat();
  const [result] = await pool.query(
    `INSERT INTO readings (site_id, entity_id, value, unit, recorded_at) VALUES ${placeholders}`,
    flat
  );
  return result.affectedRows;
}

/**
 * Get readings for a site within a window (default last 24h).
 */
async function getForSite(siteId, { sinceMs, untilMs, entityId } = {}) {
  const until = untilMs ? new Date(untilMs) : new Date();
  const since = sinceMs ? new Date(sinceMs) : new Date(Date.now() - 24 * 3600 * 1000);
  const params = [siteId, since, until];
  let entityClause = '';
  if (entityId) {
    entityClause = ' AND entity_id = ?';
    params.push(entityId);
  }
  const [rows] = await pool.query(
    `SELECT id, site_id, entity_id, value, unit, recorded_at
     FROM readings
     WHERE site_id = ? AND recorded_at >= ? AND recorded_at <= ?${entityClause}
     ORDER BY recorded_at ASC, entity_id ASC`,
    params
  );
  return rows;
}

/**
 * Latest reading per entity for a site.
 */
async function latestPerEntity(siteId) {
  const [rows] = await pool.query(
    `SELECT r.id, r.site_id, r.entity_id, r.value, r.unit, r.recorded_at
     FROM readings r
     INNER JOIN (
       SELECT entity_id, MAX(recorded_at) AS max_ts
       FROM readings WHERE site_id = ? GROUP BY entity_id
     ) m ON m.entity_id = r.entity_id AND m.max_ts = r.recorded_at
     WHERE r.site_id = ?`,
    [siteId, siteId]
  );
  return rows;
}

async function deleteOlderThan(hours) {
  const cutoff = new Date(Date.now() - hours * 3600 * 1000);
  const [result] = await pool.query('DELETE FROM readings WHERE recorded_at < ?', [cutoff]);
  return result.affectedRows;
}

function sanitize(r) {
  if (!r) return null;
  return {
    id: r.id,
    siteId: r.site_id,
    entityId: r.entity_id,
    value: Number(r.value),
    unit: r.unit,
    recordedAt: r.recorded_at,
  };
}

module.exports = { add, addMany, getForSite, latestPerEntity, deleteOlderThan, sanitize };
