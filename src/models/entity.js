const { pool } = require('../db/pool');

async function listForSite(siteId) {
  const [rows] = await pool.query(
    'SELECT * FROM site_entities WHERE site_id = ? ORDER BY entity_id',
    [siteId]
  );
  return rows;
}

async function findByEntityId(siteId, entityId) {
  const [rows] = await pool.query(
    'SELECT * FROM site_entities WHERE site_id = ? AND entity_id = ? LIMIT 1',
    [siteId, entityId]
  );
  return rows[0] || null;
}

/**
 * Upsert an entity config from the admin panel. Updates name/unit only.
 */
async function upsert(siteId, { entityId, name, unit }) {
  await pool.query(
    `INSERT INTO site_entities (site_id, entity_id, name, unit)
     VALUES (?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE name = VALUES(name), unit = VALUES(unit)`,
    [siteId, entityId, name || null, unit || 'W']
  );
  return findByEntityId(siteId, entityId);
}

/**
 * Auto-populate an entity row on first ingest. INSERT IGNORE preserves any
 * admin name/unit override for an existing entity.
 */
async function ensure(siteId, { entityId, unit = 'W' }) {
  if (!entityId) return null;
  await pool.query(
    `INSERT IGNORE INTO site_entities (site_id, entity_id, name, unit)
     VALUES (?, ?, NULL, ?)`,
    [siteId, entityId, unit || 'W']
  );
  return findByEntityId(siteId, entityId);
}

async function removeForSite(siteId) {
  await pool.query('DELETE FROM site_entities WHERE site_id = ?', [siteId]);
}

function sanitize(entity) {
  if (!entity) return null;
  return {
    id: entity.id,
    siteId: entity.site_id,
    entityId: entity.entity_id,
    name: entity.name,
    unit: entity.unit,
    createdAt: entity.created_at,
  };
}

module.exports = { listForSite, findByEntityId, upsert, ensure, removeForSite, sanitize };
