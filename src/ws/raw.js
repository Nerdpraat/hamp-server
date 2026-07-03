const { WebSocketServer } = require('ws');
const logger = require('../utils/logger');
const Site = require('../models/site');
const Reading = require('../models/reading');
const Entity = require('../models/entity');

const readingSchema = require('./shared').readingSchema;
const normalize = require('./shared').normalize;

/**
 * Attach a raw WebSocket server (RFC 6455) on the given path.
 *
 * Clients connect to ws://host:port/ws and send JSON messages of the form:
 *   { "type": "reading", "reading": { "entityId": "...", "value": 123.4, "unit": "W", "ts": 123 } }
 *   { "type": "readings", "readings": [ ... ] }
 *
 * Auth: the first message must be an auth frame:
 *   { "type": "auth", "api_key": "np_..." }
 * Until authenticated the socket is closed with code 4001 ("unauthorized").
 * The api key may also be supplied via the `x-api-key` header or `?api_key=` query.
 */
function attachRawWs(server, path = '/ws') {
  const wss = new WebSocketServer({ server, path });

  wss.on('connection', (ws, req) => {
    const ip = req.socket.remoteAddress;

    const headerKey = req.headers['x-api-key'];
    const url = new URL(req.url, 'http://localhost');
    const queryKey = url.searchParams.get('api_key');

    ws.readOnly = false;
    ws.authed = false;
    ws.site = null;

    const die = (code, reason) => {
      if (ws.readyState === ws.OPEN) ws.close(code, reason);
    };

    // Resolve an api_key immediately if provided out-of-band.
    const preAuth = async (apiKey) => {
      try {
        const site = await Site.findByApiKey(apiKey);
        if (!site) return die(4001, 'invalid api key');
        ws.site = site;
        ws.authed = true;
        ws.join = () => {}; // no rooms for raw ws
        logger.info('raw ws client connected', { siteId: site.id, slug: site.slug, ip });
        ws.send(JSON.stringify({ type: 'ready', siteId: site.id, slug: site.slug, name: site.name, readOnly: false }));
      } catch {
        die(4001, 'unauthorized');
      }
    };

    if (headerKey) preAuth(headerKey);
    else if (queryKey) preAuth(queryKey);

    ws.on('message', async (data) => {
      if (!ws.authed) {
        try {
          const msg = JSON.parse(data.toString());
          if (msg.type !== 'auth' || !msg.api_key) {
            return die(4001, 'unauthorized: send auth frame first');
          }
          await preAuth(msg.api_key);
        } catch {
          return die(4000, 'invalid json');
        }
        return;
      }

      let msg;
      try {
        msg = JSON.parse(data.toString());
      } catch {
        ws.send(JSON.stringify({ type: 'error', error: 'invalid json' }));
        return;
      }

      const site = ws.site;
      try {
        if (msg.type === 'reading') {
          const parsed = readingSchema.safeParse(msg.reading || msg);
          if (!parsed.success) {
            ws.send(JSON.stringify({ type: 'error', error: 'invalid payload', details: parsed.error?.flatten() }));
            return;
          }
          await Entity.ensure(site.id, { entityId: parsed.data.entityId, unit: parsed.data.unit });
          await Reading.add(normalize(parsed.data, site.id));
          ws.send(JSON.stringify({ type: 'ack', ok: true }));
        } else if (msg.type === 'readings') {
          const list = Array.isArray(msg.readings) ? msg.readings : [];
          const rows = [];
          const seen = new Set();
          for (const r of list) {
            const parsed = readingSchema.safeParse(r);
            if (!parsed.success) {
              ws.send(JSON.stringify({ type: 'error', error: 'invalid payload', details: parsed.error?.flatten() }));
              return;
            }
            rows.push(normalize(parsed.data, site.id));
            if (!seen.has(parsed.data.entityId)) {
              seen.add(parsed.data.entityId);
              await Entity.ensure(site.id, { entityId: parsed.data.entityId, unit: parsed.data.unit });
            }
          }
          const affected = await Reading.addMany(rows);
          ws.send(JSON.stringify({ type: 'ack', ok: true, stored: affected }));
        } else {
          ws.send(JSON.stringify({ type: 'error', error: 'unknown message type' }));
        }
      } catch (err) {
        ws.send(JSON.stringify({ type: 'error', error: err.message }));
      }
    });

    ws.on('error', (err) => {
      logger.error('raw ws error', { message: err.message, ip });
    });

    ws.on('close', (code, reason) => {
      logger.info('raw ws client disconnected', { code, reason: reason.toString(), ip });
    });
  });

  logger.info('raw ws server attached', { path });
  return wss;
}

module.exports = { attachRawWs };
