const config = require('../config');
const logger = require('../utils/logger');
const Reading = require('../models/reading');

let timer = null;

async function runOnce() {
  try {
    const deleted = await Reading.deleteOlderThan(config.retention.hours);
    logger.info('cleanup completed', { deleted, retentionHours: config.retention.hours });
    return deleted;
  } catch (err) {
    logger.error('cleanup failed', { message: err.message });
    return 0;
  }
}

function start() {
  if (timer) return;
  logger.info('cleanup job starting', {
    intervalMs: config.retention.cleanupIntervalMs,
    retentionHours: config.retention.hours,
  });
  timer = setInterval(runOnce, config.retention.cleanupIntervalMs);
  timer.unref?.();
}

function stop() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

module.exports = { start, stop, runOnce };
