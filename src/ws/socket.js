const z = require('zod');
const logger = require('../utils/logger');
const Site = require('../models/site');
const Reading = require('../models/reading');

const KINDS = ['grid', 'solar', 'consumer', 'battery'];

const readingSchema = z.object({
  kind: z.enum(KINDS),
  value: z.number().finite(),
  unit: z.string().max(16).optional(),
  ts: z.union([z.number(), z.string()]).optional(),
});

const messageSchema = z.object({
  type: z.enum(['reading', 'readings']),
  reading: readingSchema.optional(),
  readings: z.array(readingSchema).max(500).optional(),
});

function normalize(r, siteId) {
  return {
    siteId,
    kind: r.kind,
    value: r.value,
    unit: r.unit || 'W',
    recordedAt: r.ts,
  };
}

async function authenticateOwner(socket, token) {
  if (!token || typeof token !== 'string') {
    throw new Error('Missing API key');
  }
  const site = await Site.findByApiKey(token);
  if (!site) {
    throw new Error('Invalid API key');
  }
  return site;
}

async function authenticateSubscriber(socket, ident) {
  if (!ident) throw new Error('Missing site identifier');
  let site;
  if (/^\d+$/.test(String(ident))) {
    site = await Site.findById(Number(ident));
  } else {
    site = await Site.findBySlug(String(ident));
  }
  if (!site) throw new Error('Site not found');
  if (!site.is_public) throw new Error('Site is private');
  return site;
}

/**
 * Attach socket.io server with api_key based auth and ingestion.
 */
function attach(io) {
  // Owners connect with an api_key (write access).
  // Public subscribers connect with { subscribe: slugOrId } (read-only).
  io.use(async (socket, next) => {
    try {
      const auth = socket.handshake.auth || {};
      const headerKey = socket.handshake.headers?.['x-api-key'];
      const queryKey = socket.handshake.query?.api_key;
      const apiKey = auth.token || auth.apiKey || queryKey || headerKey;

      if (apiKey) {
        const site = await authenticateOwner(socket, apiKey);
        socket.data.site = site;
        socket.data.readOnly = false;
        socket.join(`site:${site.id}`);
        return next();
      }

      const ident = auth.subscribe || socket.handshake.query?.subscribe;
      if (ident) {
        const site = await authenticateSubscriber(socket, ident);
        socket.data.site = site;
        socket.data.readOnly = true;
        socket.join(`site:${site.id}`);
        return next();
      }

      next(new Error('unauthorized: provide api_key or subscribe identifier'));
    } catch (err) {
      next(new Error('unauthorized: ' + err.message));
    }
  });

  io.on('connection', (socket) => {
    const site = socket.data.site;
    logger.info('ws client connected', {
      siteId: site.id,
      slug: site.slug,
      readOnly: socket.data.readOnly,
      id: socket.id,
    });
    socket.emit('ready', {
      siteId: site.id,
      slug: site.slug,
      name: site.name,
      readOnly: socket.data.readOnly,
    });

    socket.on('reading', async (payload, ack) => {
      if (socket.data.readOnly) {
        if (ack) ack({ ok: false, error: 'read-only connection' });
        return;
      }
      try {
        const parsed = messageSchema.safeParse({ type: 'reading', reading: payload });
        if (!parsed.success || !parsed.data.reading) {
          if (ack) ack({ ok: false, error: 'invalid payload', details: parsed.error?.flatten() });
          return;
        }
        await Reading.add(normalize(parsed.data.reading, site.id));
        // Broadcast to public listeners (dashboard) on the site room, minus the sender.
        socket.to(`site:${site.id}`).emit('reading', {
          siteId: site.id,
          ...parsed.data.reading,
          unit: parsed.data.reading.unit || 'W',
        });
        if (ack) ack({ ok: true });
      } catch (err) {
        if (ack) ack({ ok: false, error: err.message });
      }
    });

    socket.on('readings', async (payload, ack) => {
      if (socket.data.readOnly) {
        if (ack) ack({ ok: false, error: 'read-only connection' });
        return;
      }
      try {
        const parsed = messageSchema.safeParse({ type: 'readings', readings: payload });
        if (!parsed.success || !parsed.data.readings) {
          if (ack) ack({ ok: false, error: 'invalid payload', details: parsed.error?.flatten() });
          return;
        }
        const rows = parsed.data.readings.map((r) => normalize(r, site.id));
        const affected = await Reading.addMany(rows);
        if (ack) ack({ ok: true, stored: affected });
      } catch (err) {
        if (ack) ack({ ok: false, error: err.message });
      }
    });

    socket.on('error', (err) => {
      logger.error('socket error', { siteId: site.id, message: err.message });
    });

    socket.on('disconnect', (reason) => {
      logger.info('ws client disconnected', { siteId: site.id, id: socket.id, reason });
    });
  });
}

module.exports = { attach };
