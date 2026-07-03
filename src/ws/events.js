const { WebSocketServer } = require('ws');
const logger = require('../utils/logger');
const hub = require('./hub');

/**
 * Create a read-only public WebSocket server (noServer mode) that fans out
 * every reading ingested from Home Assistant instances (via the raw /ws
 * ingest endpoint) to subscribed frontends.
 *
 * Clients connect to ws://host:port/events with no authentication and receive:
 *   { "type": "hello" }
 * followed by a stream of:
 *   { "type": "reading", "site": {...}, "reading": {...} }
 *
 * Only readings belonging to public sites are broadcast (see hub + raw.js),
 * so connecting requires no credentials and leaks no private data.
 *
 * The caller routes the HTTP 'upgrade' event for the '/events' path to this
 * server's handleUpgrade (see src/server.js).
 */
function attachEventsWs() {
  const wss = new WebSocketServer({ noServer: true });

  wss.on('connection', (ws, req) => {
    const ip = req.socket.remoteAddress;
    hub.add(ws);

    ws.send(JSON.stringify({ type: 'hello', subscribers: hub.size() }));

    ws.on('error', (err) => {
      logger.error('events ws error', { message: err.message, ip });
    });

    ws.on('close', () => {
      hub.remove(ws);
    });

    logger.info('events ws client connected', { ip, subscribers: hub.size() });
  });

  logger.info('events ws server created', { path: '/events' });
  return wss;
}

module.exports = { attachEventsWs };
