const crypto = require('crypto');
const { pool } = require('../db/pool');

function generateApiKey() {
  return 'np_' + crypto.randomBytes(24).toString('hex');
}

function slugify(name) {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
}

async function uniqueSlug(base) {
  let slug = slugify(base) || 'site';
  let suffix = 1;
  while (true) {
    const [rows] = await pool.query('SELECT id FROM sites WHERE slug = ? LIMIT 1', [slug]);
    if (rows.length === 0) return slug;
    suffix += 1;
    slug = `${slugify(base)}-${suffix}`.slice(0, 80);
  }
}

async function create({ name, ownerId, isPublic = true, apiKey }) {
  const slug = await uniqueSlug(name);
  const key = apiKey || generateApiKey();
  const [result] = await pool.query(
    'INSERT INTO sites (name, slug, owner_id, api_key, is_public) VALUES (?, ?, ?, ?, ?)',
    [name, slug, ownerId || null, key, isPublic ? 1 : 0]
  );
  return findById(result.insertId);
}

async function findById(id) {
  const [rows] = await pool.query('SELECT * FROM sites WHERE id = ? LIMIT 1', [id]);
  return rows[0] || null;
}

async function findBySlug(slug) {
  const [rows] = await pool.query('SELECT * FROM sites WHERE slug = ? LIMIT 1', [slug]);
  return rows[0] || null;
}

async function findByApiKey(apiKey) {
  const [rows] = await pool.query('SELECT * FROM sites WHERE api_key = ? LIMIT 1', [apiKey]);
  return rows[0] || null;
}

async function listVisibleFor(user) {
  if (user.role === 'admin') {
    const [rows] = await pool.query('SELECT * FROM sites ORDER BY created_at DESC');
    return rows;
  }
  const [rows] = await pool.query(
    'SELECT * FROM sites WHERE owner_id = ? ORDER BY created_at DESC',
    [user.id]
  );
  return rows;
}

async function listPublic() {
  const [rows] = await pool.query(
    'SELECT id, name, slug, is_public, created_at FROM sites WHERE is_public = 1 ORDER BY created_at DESC'
  );
  return rows;
}

async function update(id, fields) {
  const allowed = ['name', 'is_public', 'owner_id'];
  const sets = [];
  const vals = [];
  for (const key of allowed) {
    if (fields[key] !== undefined) {
      sets.push(`${key} = ?`);
      vals.push(fields[key]);
    }
  }
  if (fields.regenerateApiKey) {
    sets.push('api_key = ?');
    vals.push(generateApiKey());
  }
  if (sets.length === 0) return findById(id);
  vals.push(id);
  await pool.query(`UPDATE sites SET ${sets.join(', ')} WHERE id = ?`, vals);
  return findById(id);
}

async function remove(id) {
  await pool.query('DELETE FROM sites WHERE id = ?', [id]);
}

function sanitize(site) {
  if (!site) return null;
  return {
    id: site.id,
    name: site.name,
    slug: site.slug,
    ownerId: site.owner_id,
    apiKey: site.api_key,
    isPublic: !!site.is_public,
    createdAt: site.created_at,
  };
}

function publicView(site) {
  if (!site) return null;
  return {
    id: site.id,
    name: site.name,
    slug: site.slug,
    isPublic: !!site.is_public,
    createdAt: site.created_at,
  };
}

module.exports = {
  create,
  findById,
  findBySlug,
  findByApiKey,
  listVisibleFor,
  listPublic,
  update,
  remove,
  sanitize,
  publicView,
  generateApiKey,
};
