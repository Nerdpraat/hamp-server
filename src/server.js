const http = require('http');
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const morgan = require('morgan');

const path = require('path');
const config = require('./config');
const logger = require('./utils/logger');
const { pool, ping } = require('./db/pool');
const authRoutes = require('./routes/auth');
const siteRoutes = require('./routes/sites');
const publicRoutes = require('./routes/public');
const userRoutes = require('./routes/users');
const { attachRawWs } = require('./ws/raw');
const { attachEventsWs } = require('./ws/events');
const cleanup = require('./jobs/cleanup');

// Ensure any startup/runtime crash is captured in the log file instead of
// disappearing (LiteSpeed lsnode swallows stderr in some setups).
process.on('uncaughtException', (err) => {
  logger.error('uncaughtException', { message: err.message, stack: err.stack });
  process.exit(1);
});
process.on('unhandledRejection', (reason) => {
  logger.error('unhandledRejection', { message: reason && reason.message, stack: reason && reason.stack });
});

function buildApp() {
  const app = express();
  app.set('trust proxy', config.trustProxy);
  app.use(helmet());
  const origins = config.cors.origins;
  app.use(
    cors({
      origin: origins.includes('*') ? true : origins,
      credentials: true,
    })
  );
  app.use(express.json({ limit: '1mb' }));
  if (config.env !== 'test') {
    morgan.token('remote-addr', (req) => req.headers['x-forwarded-for'] || req.socket.remoteAddress);
    app.use(morgan(':remote-addr - :method :url :status :res[content-length] - :response-time ms', { stream: logger.stream }));
  }

  app.get('/health', (req, res) => res.json({ ok: true, env: config.env }));

  // Admin dashboard (static). Mounted under /admin so the public REST API
  // stays clean and the dashboard never collides with /api routes.
  app.use('/admin', express.static(path.join(__dirname, 'public', 'admin'), {
    index: 'index.html',
    fallthrough: true,
    etag: false,
    lastModified: false,
    setHeaders: (res) => {
      res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
    },
  }));
  app.get(/^\/admin\/?$/, (req, res) => res.sendFile(path.join(__dirname, 'public', 'admin', 'index.html')));

  // Public read-only dashboard (static). Mounted at root so it is the site
  // landing page; API routes under /api take precedence.
  app.use('/', express.static(path.join(__dirname, 'public', 'site'), {
    index: 'index.html',
    fallthrough: true,
    etag: false,
    lastModified: false,
    setHeaders: (res) => {
      res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
    },
  }));
  app.get(/^\/$/, (req, res) => res.sendFile(path.join(__dirname, 'public', 'site', 'index.html')));

  app.use('/api/auth', authRoutes);
  app.use('/api/sites', siteRoutes);
  app.use('/api/users', userRoutes);
  app.use('/api/public', publicRoutes); // public: /api/public/sites, /api/public/sites/:slug/...

  app.use((req, res) => res.status(404).json({ error: 'Not found' }));

  app.use((err, req, res, next) => {
    logger.error('unhandled error', { message: err.message, stack: err.stack });
    res.status(err.status || 500).json({ error: err.message || 'Internal server error' });
  });

  return app;
}

async function start() {
  logger.info('starting server', { env: config.env, logFile: logger.logFile });
  try {
    await ping();
    logger.info('db connected', { host: config.db.host, port: config.db.port, database: config.db.database });
  } catch (err) {
    logger.error('db connection failed', { message: err.message });
    process.exit(1);
  }

  const app = buildApp();
  const server = http.createServer(app);

  // Two WebSocket endpoints share one http.Server. Both WSS run in noServer
  // mode and a single 'upgrade' handler dispatches by path, so the two
  // servers never both touch the same socket (which previously produced
  // corrupt frames / 400 responses).
  const rawWss = attachRawWs();
  const eventsWss = attachEventsWs();
  app.set('rawWss', rawWss);
  app.set('eventsWss', eventsWss);

  server.on('upgrade', (req, socket, head) => {
    let pathname;
    try {
      pathname = new URL(req.url, 'http://localhost').pathname;
    } catch {
      socket.destroy();
      return;
    }
    if (pathname === '/ws') {
      rawWss.handleUpgrade(req, socket, head, (ws) => rawWss.emit('connection', ws, req));
    } else if (pathname === '/events') {
      eventsWss.handleUpgrade(req, socket, head, (ws) => eventsWss.emit('connection', ws, req));
    } else {
      socket.destroy();
    }
  });

  cleanup.start();

  server.listen(config.port, () => {
    logger.info('server listening', { port: config.port, env: config.env });
  });

  const shutdown = (sig) => {
    logger.info('shutdown signal received', { signal: sig });
    cleanup.stop();
    rawWss.close();
    eventsWss.close();
    server.close(() => pool.end().then(() => process.exit(0)));
    setTimeout(() => process.exit(1), 10000).unref();
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));

  return { app, server };
}

// Always boot when this module is loaded. Under LiteSpeed lsnode the entry
// file is require()'d by lsnode.js (so require.main !== module), therefore a
// guard would prevent the server from ever listening. We still allow callers
// to skip auto-start with AUTO_START=0 (e.g. for tests).
if (process.env.AUTO_START !== '0') {
  start().catch((err) => {
    logger.error('startup failed', { message: err.message, stack: err.stack });
    process.exit(1);
  });
}

module.exports = { buildApp, start };
