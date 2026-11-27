// Overview: live grid, solar and battery status of every site, one card per site.
import { api, overviewSocketUrl } from '../api.js';
import { h } from '../cards.js';
import { formatNumber, formatPower } from '../format.js';
import { batteryLabel, gridLabel, lastSeenText } from '../transform.js';

export const needsDate = false;

function tile(name, value, sub, cls) {
  return h('div', { class: `tile ${cls}` },
    h('div', { class: 'n', text: name }),
    h('div', { class: 'v', text: value }),
    h('div', { class: 'sub', text: sub || '\u00a0' }));
}

function siteCard(site, onSelect) {
  const grid = gridLabel(site.grid);
  const bat = batteryLabel(site.battery);
  const socText = bat.soc === null ? '' : `${formatNumber(bat.soc, 0)} %`;
  const batSub = [bat.sub, socText].filter(Boolean).join(' · ');
  const head = h('h2', {}, h('span', { text: site.name }),
    h('span', { class: `dot${site.online ? ' online' : ''}`, title: site.online ? 'client connected' : 'client offline' }));
  const body = h('div', { class: 'tiles-row' },
    tile('Grid', formatPower(grid.w), grid.sub, 'grid'),
    tile('Solar', formatPower(site.solar ? site.solar.power_w : null), '', 'solar'),
    tile('Battery', formatPower(bat.w), batSub, 'battery'));
  const c = h('section', { class: `card site-card${site.online ? '' : ' offline'}`, tabindex: '0', role: 'button',
    'aria-label': `Open ${site.name}` }, head, body);
  if (!site.online) c.append(h('div', { class: 'seen', text: lastSeenText(site.last_seen, Date.now() / 1000) }));
  const open = () => onSelect(site.id);
  c.addEventListener('click', open);
  c.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } });
  return c;
}

/** Returns a dispose function that closes the websocket. */
export async function renderOverview(el, onSelect, isStale) {
  const grid = h('div', { class: 'overview-grid' });
  el.append(grid);
  let closed = false;
  let ws = null;
  let retryTimer = null;
  let retry = 1000;

  const draw = (sites) => {
    if (!sites.length) {
      grid.replaceChildren(h('div', { class: 'empty', text: 'No sites available.' }));
      return;
    }
    grid.replaceChildren(...sites.map((s) => siteCard(s, onSelect)));
  };

  // First paint over HTTP (also surfaces a 401 or a server error); updates then arrive by push.
  try {
    const sites = await api.overview();
    if (closed || isStale()) return () => {};
    draw(sites);
  } catch (err) {
    if (isStale() || err.status === 401) return () => {};
    grid.replaceChildren(h('div', { class: 'error', text: `Could not load data: ${err.message}` }));
  }

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(overviewSocketUrl());
    ws.onopen = () => { retry = 1000; };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'overview' && !closed) draw(msg.sites);
    };
    ws.onclose = () => {
      if (closed) return;
      retryTimer = setTimeout(connect, retry);
      retry = Math.min(retry * 2, 15000);
    };
  };
  connect();

  return () => { closed = true; clearTimeout(retryTimer); ws?.close(); };
}
