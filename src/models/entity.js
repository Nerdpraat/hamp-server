const { pool } = require('../db/pool');

const KINDS = ['grid', 'solar', 'consumer', 'battery'];

async function listForSite(siteId) {
  const [rows] = await pool.query(
    'SELECT * FROM site_entities WHERE site_id = ? ORDER BY kind',
    [siteId]
  );
  return rows;
}

async function upsert(siteId, { kind, entityId, name, unit }) {
  await pool.query(
    `INSERT INTO site_entities (site_id, kind, entity_id, name, unit)
     VALUES (?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE entity_id = VALUES(entity_id), name = VALUES(name), unit = VALUES(unit)`,
    [siteId, kind, entityId, name || null, unit || 'W']
  );
  return findByKind(siteId, kind);
}

async function findByKind(siteId, kind) {
  const [rows] = await pool.query(
    'SELECT * FROM site_entities WHERE site_id = ? AND kind = ? LIMIT 1',
    [siteId, kind]
  );
  return rows[0] || null;
}

async function removeForSite(siteId) {
  await pool.query('DELETE FROM site_entities WHERE site_id = ?', [siteId]);
}

function sanitize(entity) {
  if (!entity) return null;
  return {
    id: entity.id,
    siteId: entity.site_id,
    kind: entity.kind,
    entityId: entity.entity_id,
    name: entity.name,
    unit: entity.unit,
    createdAt: entity.created_at,
  };
}

module.exports = { KINDS, listForSite, upsert, findByKind, removeForSite, sanitize };
