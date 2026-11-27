// Thin fetch wrapper. A 401 anywhere triggers the login overlay.

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function request(path, options) {
  const res = await fetch(path, { credentials: 'same-origin', ...options });
  if (res.status === 401 && !path.startsWith('/api/login')) onUnauthorized();
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

const sec = (ms) => Math.floor(ms / 1000);
const qs = (o) => new URLSearchParams(o).toString();

export const api = {
  login: (key) => request('/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) }),
  logout: () => request('/api/logout', { method: 'POST' }),
  me: () => request('/api/me'),
  sites: () => request('/api/sites'),
  overview: () => request('/api/overview'),
  devices: (site) => request(`/api/sites/${encodeURIComponent(site)}/devices`),
  series: (site, startMs, endMs, resolution, keys) => request(
    `/api/sites/${encodeURIComponent(site)}/series?` +
    qs({ start: sec(startMs), end: sec(endMs), resolution, keys: keys.join(',') })),
  totals: (site, startMs, endMs) => request(
    `/api/sites/${encodeURIComponent(site)}/totals?` + qs({ start: sec(startMs), end: sec(endMs) })),
  summary: (site, startMs, endMs) => request(
    `/api/sites/${encodeURIComponent(site)}/summary?` + qs({ start: sec(startMs), end: sec(endMs) })),
  live: (site) => request(`/api/sites/${encodeURIComponent(site)}/live`),
};

export function liveSocketUrl(site) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws/live?site=${encodeURIComponent(site)}`;
}
