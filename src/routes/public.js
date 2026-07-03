const { Router } = require('express');

const Site = require('../models/site');
const Reading = require('../models/reading');
const { resolvePublicSite } = require('../middleware/site');

const router = Router();

// List all public sites (no auth required).
router.get('/sites', async (req, res, next) => {
  try {
    const sites = await Site.listPublic();
    res.json({ sites: sites.map(Site.publicView) });
  } catch (err) {
    next(err);
  }
});

// Get a single public site metadata.
router.get('/sites/:slug', resolvePublicSite, async (req, res) => {
  res.json({ site: Site.publicView(req.site) });
});

// Readings for a site (last 24h by default). Public for public sites.
router.get('/sites/:slug/readings', resolvePublicSite, async (req, res, next) => {
  try {
    const sinceMs = req.query.since ? Number(req.query.since) : undefined;
    const untilMs = req.query.until ? Number(req.query.until) : undefined;
    const kind = req.query.kind && Reading.KINDS.includes(req.query.kind) ? req.query.kind : undefined;
    const rows = await Reading.getForSite(req.site.id, { sinceMs, untilMs, kind });
    res.json({ site: Site.publicView(req.site), readings: rows.map(Reading.sanitize) });
  } catch (err) {
    next(err);
  }
});

// Latest value per kind.
router.get('/sites/:slug/latest', resolvePublicSite, async (req, res, next) => {
  try {
    const rows = await Reading.latestPerKind(req.site.id);
    res.json({ site: Site.publicView(req.site), latest: rows.map(Reading.sanitize) });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
