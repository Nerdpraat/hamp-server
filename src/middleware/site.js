const { pool } = require('../db/pool');
const Site = require('../models/site');

async function loadSite(req, res, next) {
  const id = Number(req.params.siteId || req.params.id);
  if (!Number.isInteger(id) || id <= 0) {
    return res.status(400).json({ error: 'Invalid site id' });
  }
  const site = await Site.findById(id);
  if (!site) return res.status(404).json({ error: 'Site not found' });
  req.site = site;
  next();
}

async function requireSiteAccess(req, res, next) {
  if (!req.site) return next(new Error('Site not loaded'));
  if (req.user.role === 'admin') return next();
  if (req.site.owner_id === req.user.id) return next();
  return res.status(403).json({ error: 'You do not have access to this site' });
}

/**
 * Find a site by slug or id, applying visibility rules:
 * - public sites visible to anyone
 * - private sites visible to owner or admin
 */
async function resolvePublicSite(req, res, next) {
  const ident = req.params.slug;
  let site;
  if (/^\d+$/.test(ident)) {
    site = await Site.findById(Number(ident));
  } else {
    site = await Site.findBySlug(ident);
  }
  if (!site) return res.status(404).json({ error: 'Site not found' });

  const isPublic = !!site.is_public;
  const isOwner = req.user && site.owner_id === req.user.id;
  const isAdmin = req.user && req.user.role === 'admin';
  if (!isPublic && !isOwner && !isAdmin) {
    return res.status(403).json({ error: 'Site is private' });
  }
  req.site = site;
  next();
}

async function withTransaction(fn) {
  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    const result = await fn(conn);
    await conn.commit();
    return result;
  } catch (err) {
    await conn.rollback();
    throw err;
  } finally {
    conn.release();
  }
}

module.exports = { loadSite, requireSiteAccess, resolvePublicSite, withTransaction };
