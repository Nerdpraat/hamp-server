// Live view: current values over /ws/live plus a rolling one-hour chart per gauge group.
import { api, liveSocketUrl } from '../api.js';
import { h } from '../cards.js';
import { colorFor, createChart, lineOption } from '../charts.js';
import { formatNumber, formatPower } from '../format.js';
import { batteriesWithCapacity, storedEnergy, unitClass } from '../transform.js';

export const needsDate = false;

const WINDOW_MS = 3600 * 1000;
const GROUP_TITLES = {
  power: 'Power', temperature: 'Temperature', level: 'Level',
  '/kWh': 'Price', '/m3': 'Price', 'L/min': 'Flow', other: 'Other',
};

function formatLive(entity, value) {
  if (entity.unit === 'W') return formatPower(value);
  return `${formatNumber(value, 2)}${entity.unit ? ' ' + entity.unit : ''}`;
}

function setStored(t, soc) {
  const kwh = storedEnergy(soc, t.capacity);
  if (kwh !== null) t.v.textContent = `${formatNumber(kwh, 1)} kWh`;
}

/** Returns a dispose function. */
export async function renderNow(el, site, isStale) {
  const { entities, instances } = await api.devices(site);
  if (isStale()) return () => {};
  const gauges = entities.filter((e) => e.kind === 'gauge');
  if (!gauges.length) {
    el.append(h('div', { class: 'empty', text: 'No live (gauge) entities configured for this site.' }));
    return () => {};
  }

  const now = Date.now();
  const hist = await api.series(site, now - WINDOW_MS, now + 60_000, '1m', gauges.map((e) => e.key));
  if (isStale()) return () => {};

  const buffers = {};
  for (const e of gauges) {
    buffers[e.key] = (hist.series[e.key]?.points || []).filter((p) => p[1] !== null).map((p) => [p[0], p[1]]);
  }

  // tiles
  const tiles = h('div', { class: 'now-tiles' });
  const tileValue = {};
  for (const e of gauges) {
    const v = h('div', { class: 'v', text: '–' });
    tileValue[e.key] = v;
    tiles.append(h('div', { class: 'tile' }, h('div', { class: 'n', text: e.name }), v));
    const last = buffers[e.key].at(-1);
    if (last) v.textContent = formatLive(e, last[1]);
  }
  const storedTiles = [];
  for (const b of batteriesWithCapacity(instances)) {
    const v = h('div', { class: 'v', text: '–' });
    tiles.append(h('div', { class: 'tile' }, h('div', { class: 'n', text: `${b.name} · Stored energy` }), v));
    const t = { ...b, v };
    storedTiles.push(t);
    const last = buffers[b.socKey]?.at(-1);
    if (last) setStored(t, last[1]);
  }
  el.append(tiles);

  // one chart per group
  const groups = {};
  for (const e of gauges) (groups[unitClass(e)] ||= []).push(e);
  const charts = [];
  const wrap = h('div', { class: 'now-charts' });
  el.append(wrap); // attach before creating charts so they can measure their container
  const boxes = [];
  for (const [group, ents] of Object.entries(groups)) {
    const box = h('div', { class: 'chart' });
    wrap.append(h('section', { class: 'card' }, h('h2', {}, h('span', { text: GROUP_TITLES[group] || group })), box));
    boxes.push([box, ents]);
  }
  // Create charts only after the whole grid is laid out, otherwise they measure a partial layout.
  for (const [box, ents] of boxes) {
    const unit = ents[0].unit === 'W' ? 'kW' : ents[0].unit;
    const scale = ents[0].unit === 'W' ? 1000 : 1;
    charts.push({ chart: createChart(box), ents, unit, scale });
  }
  // First draw builds the chart; later draws only merge new data, so the legend page and any
  // series the user switched off stay as they are.
  let firstDraw = true;
  const draw = () => {
    const now = Date.now();
    const cutoff = now - WINDOW_MS;
    const window = { min: cutoff, max: now };
    for (const { chart, ents, unit, scale } of charts) {
      const list = ents.map((e, i) => {
        buffers[e.key] = buffers[e.key].filter((p) => p[0] >= cutoff);
        return {
          name: e.name, color: colorFor(e, i), area: scale === 1000,
          data: buffers[e.key].map((p) => [p[0], p[1] / scale]),
        };
      });
      if (firstDraw) {
        chart.setOption(lineOption(list, unit, (v) => `${formatNumber(v, 2)} ${unit}`, window));
      } else {
        chart.setOption({
          xAxis: { min: window.min, max: window.max },
          series: list.map((s) => ({ name: s.name, data: s.data })),
        });
      }
    }
    firstDraw = false;
  };
  draw();

  const byKey = Object.fromEntries(gauges.map((e) => [e.key, e]));
  const apply = (values) => {
    for (const t of storedTiles) if (values[t.socKey]) setStored(t, values[t.socKey][1]);
    for (const [key, [ts, value]] of Object.entries(values)) {
      const e = byKey[key];
      if (!e) continue;
      tileValue[key].textContent = formatLive(e, value);
      buffers[key].push([ts * 1000, value]);
    }
    draw();
  };

  let ws = null;
  let closed = false;
  let retry = 1000;
  const connect = () => {
    if (closed) return;
    ws = new WebSocket(liveSocketUrl(site));
    ws.onopen = () => { retry = 1000; };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'values') apply(msg.values);
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
