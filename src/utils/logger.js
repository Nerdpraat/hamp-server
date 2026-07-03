const fs = require('fs');
const path = require('path');

const LOG_DIR = path.resolve(process.cwd(), 'logs');
const LOG_FILE = process.env.LOG_FILE || path.join(LOG_DIR, 'server.log');
const LOG_LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };
const MIN_LEVEL = LOG_LEVELS[process.env.LOG_LEVEL || 'info'] || LOG_LEVELS.info;

let writeStream;
let fileDisabled = false;
function getStream() {
  if (fileDisabled) return null;
  if (!writeStream) {
    try {
      fs.mkdirSync(LOG_DIR, { recursive: true });
      writeStream = fs.createWriteStream(LOG_FILE, { flags: 'a' });
      writeStream.on('error', () => {
        fileDisabled = true;
        process.stderr.write(`[logger] failed writing to ${LOG_FILE}, file logging disabled\n`);
      });
    } catch (err) {
      fileDisabled = true;
      process.stderr.write(`[logger] cannot open ${LOG_FILE}: ${err.message}, file logging disabled\n`);
    }
  }
  return writeStream;
}

function timestamp() {
  return new Date().toISOString();
}

function write(level, msg, meta) {
  if (LOG_LEVELS[level] < MIN_LEVEL) return;
  const line =
    `${timestamp()} [${level.toUpperCase()}] ${msg}` +
    (meta !== undefined ? ' ' + (typeof meta === 'string' ? meta : safeJson(meta)) : '') +
    '\n';
  const s = getStream();
  if (s) s.write(line);
  // Mirror to stdout/stderr for process managers.
  if (level === 'error') {
    process.stderr.write(line);
  } else {
    process.stdout.write(line);
  }
}

function safeJson(obj) {
  try {
    return JSON.stringify(obj);
  } catch {
    return String(obj);
  }
}

module.exports = {
  logFile: LOG_FILE,
  stream: {
    write: (message) => {
      const s = getStream();
      if (s) s.write(message);
    },
  },
  debug: (msg, meta) => write('debug', msg, meta),
  info: (msg, meta) => write('info', msg, meta),
  warn: (msg, meta) => write('warn', msg, meta),
  error: (msg, meta) => write('error', msg, meta),
};
