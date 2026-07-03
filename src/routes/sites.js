const { Router } = require('express');
const z = require('zod');

const Site = require('../models/site');
const Entity = require('../models/entity');
const Reading = require('../models/reading');
const { pool } = require('../db/pool');
const { authRequired } = require('../auth/middleware');
const { loadSite, requireSiteAccess } = require('../middleware/site');

const router = Router();

const createSiteSchema = z.object({
  name: z.string().min(1).max(160),
  isPublic: z.boolean().optional(),
  ownerId: z.number().int().positive().optional().nullable(),
});

const updateSiteSchema = z.object({
  name: z.string().min(1).max(160).optional(),
  isPublic: z.boolean().optional(),
  ownerId: z.number().int().positive().optional().nullable(),
  regenerateApiKey: z.boolean().optional(),
});

const entitySchema = z.object({
  entityId: z.string().min(1).max(255),
  name: z.string().max(160).optional(),
  unit: z.string().max(16).optional(),
});

const batchEntitiesSchema = z.object({
  entities: z.array(entitySchema).max(500),
});

// List sites visible to the user.
router.get('/', authRequired, async (req, res, next) => {
  try {
    const sites = await Site.listVisibleFor(req.user);
    res.json({ sites: sites.map(Site.sanitize) });
  } catch (err) {
    next(err);
  }
});

// Create site (admin can assign ownerId; users create their own).
router.post('/', authRequired, async (req, res, next) => {
  try {
    const parsed = createSiteSchema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({ error: 'Invalid input', details: parsed.error.flatten() });
    }
    let { name, isPublic, ownerId } = parsed.data;
    if (req.user.role !== 'admin' && ownerId !== undefined && ownerId !== req.user.id) {
      return res.status(403).json({ error: 'You can only create sites for yourself' });
    }
    if (ownerId === undefined) ownerId = req.user.id;
    const site = await Site.create({ name, ownerId, isPublic: isPublic ?? true });
    res.status(201).json({ site: Site.sanitize(site) });
  } catch (err) {
    next(err);
  }
});

// Get a single site (owner/admin only via this route; public use /public).
router.get('/:siteId', authRequired, loadSite, requireSiteAccess, async (req, res) => {
  res.json({ site: Site.sanitize(req.site) });
});

router.patch('/:siteId', authRequired, loadSite, requireSiteAccess, async (req, res, next) => {
  try {
    const parsed = updateSiteSchema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({ error: 'Invalid input', details: parsed.error.flatten() });
    }
    const { ownerId, ...rest } = parsed.data;
    if (ownerId !== undefined && req.user.role !== 'admin') {
      return res.status(403).json({ error: 'Only admins can reassign site ownership' });
    }
    const site = await Site.update(req.site.id, { ...rest, ownerId });
    res.json({ site: Site.sanitize(site) });
  } catch (err) {
    next(err);
  }
});

router.delete('/:siteId', authRequired, loadSite, requireSiteAccess, async (req, res, next) => {
  try {
    await Site.remove(req.site.id);
    res.json({ deleted: true });
  } catch (err) {
    next(err);
  }
});

// --- Readings (authenticated; exposes real entity_id for the admin panel) ---

router.get('/:siteId/readings', authRequired, loadSite, requireSiteAccess, async (req, res, next) => {
  try {
    const sinceMs = req.query.since ? Number(req.query.since) : undefined;
    const untilMs = req.query.until ? Number(req.query.until) : undefined;
    const entityId = req.query.entityId ? String(req.query.entityId) : undefined;
    const rows = await Reading.getForSite(req.site.id, { sinceMs, untilMs, entityId });
    res.json({ site: Site.sanitize(req.site), readings: rows.map(Reading.sanitize) });
  } catch (err) {
    next(err);
  }
});

router.get('/:siteId/latest', authRequired, loadSite, requireSiteAccess, async (req, res, next) => {
  try {
    const rows = await Reading.latestPerEntity(req.site.id);
    res.json({ site: Site.sanitize(req.site), latest: rows.map(Reading.sanitize) });
  } catch (err) {
    next(err);
  }
});

// --- Entity configuration ---

router.get('/:siteId/entities', authRequired, loadSite, requireSiteAccess, async (req, res, next) => {
  try {
    const entities = await Entity.listForSite(req.site.id);
    res.json({ entities: entities.map(Entity.sanitize) });
  } catch (err) {
    next(err);
  }
});

router.put('/:siteId/entities', authRequired, loadSite, requireSiteAccess, async (req, res, next) => {
  try {
    const parsed = batchEntitiesSchema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({ error: 'Invalid input', details: parsed.error.flatten() });
    }
    const results = [];
    for (const e of parsed.data.entities) {
      const entity = await Entity.upsert(req.site.id, e);
      results.push(Entity.sanitize(entity));
    }
    res.json({ entities: results });
  } catch (err) {
    next(err);
  }
});

// Delete an entity (and its readings stay historical). Body: { entityId }.
router.delete('/:siteId/entities', authRequired, loadSite, requireSiteAccess, async (req, res, next) => {
  try {
    const entityId = String(req.body?.entityId || req.query.entityId || '').trim();
    if (!entityId) return res.status(400).json({ error: 'entityId required' });
    await pool.query('DELETE FROM site_entities WHERE site_id = ? AND entity_id = ?', [req.site.id, entityId]);
    res.json({ deleted: true });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
