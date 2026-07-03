'use strict';

/**
 * In-process pub/sub for live reading events. Ingest (raw ws) publishes,
 * the public events ws subscribes and fans out to connected frontends.
 *
 * Events are only published for public sites so private data never leaks.
 */
class Hub {
  constructor() {
    this.clients = new Set();
  }

  add(ws) {
    this.clients.add(ws);
  }

  remove(ws) {
    this.clients.delete(ws);
  }

  size() {
    return this.clients.size;
  }

  /**
   * Broadcast an event to every connected subscriber. Silently skips
   * sockets that are no longer open.
   * @param {object} event JSON-serialisable event payload.
   */
  broadcast(event) {
    const payload = JSON.stringify(event);
    for (const ws of this.clients) {
      if (ws.readyState === ws.OPEN) {
        try {
          ws.send(payload);
        } catch {
          this.clients.delete(ws);
        }
      }
    }
  }
}

module.exports = new Hub();
