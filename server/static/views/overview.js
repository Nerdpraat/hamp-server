// Overview: live grid, solar and battery status of every site, one card per site.
import { api } from '../api.js';
import { h } from '../cards.js';
import { formatNumber, formatPower } from '../format.js';
import { batteryLabel, gridLabel, lastSeenText } from '../transform.js';

export const needsDate = false;

const POLL_MS = 5000;

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

/** Returns a dispose function that stops polling. */
export async function renderOverview(el, onSelect, isStale) {
  const grid = h('div', { class: 'overview-grid' });
  el.append(grid);
  let closed = false;
  let timer = null;

  const draw = (sites) => {
    if (!sites.length) {
      grid.replaceChildren(h('div', { class: 'empty', text: 'No sites available.' }));
      return;
    }
    grid.replaceChildren(...sites.map((s) => siteCard(s, onSelect)));
  };

  const poll = async () => {
    timer = null;
    if (closed) return;
    try {
      const sites = await api.overview();
      if (closed || isStale()) return;
      draw(sites);
    } catch (err) {
      if (closed || isStale() || err.status === 401) return;
      if (!grid.children.length) {
        grid.replaceChildren(h('div', { class: 'error', text: `Could not load data: ${err.message}` }));
      }
    }
    if (!closed) timer = setTimeout(poll, document.hidden ? POLL_MS * 6 : POLL_MS);
  };

  await poll();
  return () => { closed = true; clearTimeout(timer); };
}
