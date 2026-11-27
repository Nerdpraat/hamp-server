// Subscribe to /ws/live for one site; reconnects with backoff. Returns an unsubscribe function.
import { liveSocketUrl } from './api.js';

/** onValues(values) is called for every push after the initial snapshot that follows connecting. */
export function subscribeLive(site, onValues) {
  let ws = null;
  let closed = false;
  let retry = 1000;
  const connect = () => {
    if (closed) return;
    let initial = true;
    ws = new WebSocket(liveSocketUrl(site));
    ws.onopen = () => { retry = 1000; };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type !== 'values') return;
      if (initial) { initial = false; return; }   // the first message is only a snapshot
      if (Object.keys(msg.values).length) onValues(msg.values);
    };
    ws.onclose = () => {
      if (closed) return;
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 15000);
    };
  };
  connect();
  return () => { closed = true; ws?.close(); };
}
