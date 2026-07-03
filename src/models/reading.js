const crypto = require('crypto');
const { pool } = require('../db/pool');

/**
 * Stable opaque id for an entity within a site, so the raw entity_id (which may
 * contain HA device serial numbers) is never exposed via the public API.
 */
function publicId(siteId, entityId) {
  return crypto
    .createHash('sha256')
    .update(`${siteId}:${entityId}`)
    .digest('hex')
    .slice(0, 16);
}

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
  const since = sinceMs ? new Date(sinceMs) : new Date(Date.now() - 24 * 3600 * 1000);
  const params = [siteId, since];
  let where = 'site_id = ? AND recorded_at >= ?';
  if (untilMs) {
    where += ' AND recorded_at <= ?';
    params.push(new Date(untilMs));
  }
  if (entityId) {
    where += ' AND r.entity_id = ?';
    params.push(entityId);
  }
  const [rows] = await pool.query(
    `SELECT r.id, r.site_id, r.entity_id, r.value,
            COALESCE(e.unit, r.unit) AS unit, e.name, r.recorded_at
     FROM readings r
     LEFT JOIN site_entities e
       ON e.site_id = r.site_id AND e.entity_id = r.entity_id
     WHERE r.${where}
     ORDER BY r.recorded_at ASC, r.entity_id ASC`,
    params
  );
  return rows;
}

/**
 * Latest reading per entity for a site. Dedupes by primary key id so that
 * multiple readings sharing the same recorded_at timestamp do not produce
 * duplicate rows.
 */
async function latestPerEntity(siteId) {
  const [rows] = await pool.query(
    `SELECT r.id, r.site_id, r.entity_id, r.value,
            COALESCE(e.unit, r.unit) AS unit, e.name, r.recorded_at
     FROM readings r
     INNER JOIN (
       SELECT entity_id, MAX(id) AS max_id
       FROM readings WHERE site_id = ? GROUP BY entity_id
     ) m ON m.max_id = r.id
     LEFT JOIN site_entities e
       ON e.site_id = r.site_id AND e.entity_id = r.entity_id
     WHERE r.site_id = ?
     ORDER BY r.entity_id`,
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
    name: r.name,
    recordedAt: r.recorded_at,
  };
}

/**
 * Public-facing view: never reveals the raw entity_id (which may embed HA
 * serial numbers). Exposes a stable hash of (siteId + entityId) as `id`.
 */
function publicView(r) {
  if (!r) return null;
  return {
    id: publicId(r.site_id, r.entity_id),
    value: Number(r.value),
    unit: r.unit,
    name: r.name,
    recordedAt: r.recorded_at,
  };
}

module.exports = { add, addMany, getForSite, latestPerEntity, deleteOlderThan, sanitize, publicView, publicId };
