// Reusable dashboard cards. Each function appends a card to `parent` and returns it.
import { barOption, colorFor, createChart, lineOption, PALETTE, TEXT } from './charts.js';
import { renderDistribution } from './distribution.js';
import { formatCost, formatNumber, formatPct, formatValue, prettyUnit } from './format.js';
import { devicesDetail, netGridPower, sankeyData, sensorSummary, storedSeries, totalsRows } from './transform.js';

export function h(tag, props = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else n.setAttribute(k, v);
  }
  n.append(...children);
  return n;
}

export function card(parent, title, badge) {
  const head = h('h2', {}, h('span', { text: title }));
  if (badge) head.append(h('span', { class: 'badge', text: badge }));
  const c = h('section', { class: 'card' }, head);
  parent.append(c);
  return c;
}

function chartBox(c, cls = '') {
  const box = h('div', { class: `chart ${cls}`.trim() });
  c.append(box);
  return box;
}

function empty(c, msg) {
  c.append(h('div', { class: 'empty', text: msg }));
}

const hasRole = (d, role) => d.entities.some((e) => e.role === role);
const unitOf = (d, role) => d.entities.find((e) => e.role === role)?.unit || 'kWh';

// ---------------------------------------------------------------- electricity
const ELECTRICITY_ROLES = ['grid_import', 'solar_energy', 'battery_discharge', 'grid_export', 'battery_charge'];

export function electricityCard(parent, d) {
  const ents = d.entities.filter((e) => ELECTRICITY_ROLES.includes(e.role));
  const total = d.summary.values.home;
  const c = card(parent, 'Electricity', ents.length ? formatValue(total, unitOf(d, 'grid_import')) : '');
  if (!ents.length) { empty(c, 'No electricity sources configured for this site.'); return c; }
  const list = ents.map((e) => ({
    name: e.name, color: colorFor(e), stack: 'elec',
    data: (d.arrays[e.key] || []).map((v) => (v === null ? null : v * e.sign)),
  }));
  createChart(chartBox(c, 'tall')).setOption(barOption(d.buckets, d.mode, list, unitOf(d, 'grid_import')));
  return c;
}

export function solarCard(parent, d) {
  const ents = d.entities.filter((e) => e.role === 'solar_energy');
  if (!ents.length) return null;
  const c = card(parent, 'Solar production', formatValue(d.summary.values.solar_energy, unitOf(d, 'solar_energy')));
  const list = ents.map((e) => ({ name: e.name, color: colorFor(e), stack: 'solar', data: d.arrays[e.key] || [] }));
  createChart(chartBox(c)).setOption(barOption(d.buckets, d.mode, list, unitOf(d, 'solar_energy')));
  return c;
}

export function batteryStoredCard(parent, d) {
  const series = storedSeries(d.instances, d.gauges);
  if (!series.length) return null;
  const c = card(parent, 'Battery stored energy');
  const list = series.map((s, i) => ({ name: s.name, color: colorFor({}, i), area: true, data: s.data }));
  createChart(chartBox(c)).setOption(
    lineOption(list, 'kWh', (v) => `${formatNumber(v, 1)} kWh`, { min: d.range.start, max: d.range.end }));
  return c;
}

export function gasCard(parent, d) {
  const ents = d.entities.filter((e) => e.role === 'gas');
  const total = d.summary.values.gas;
  const unit = unitOf(d, 'gas') === 'm3' ? 'm³' : unitOf(d, 'gas');
  const c = card(parent, 'Gas consumption', ents.length ? formatValue(total, unit) : '');
  if (!ents.length) { empty(c, 'No gas source configured for this site.'); return c; }
  const list = ents.map((e) => ({ name: e.name, color: colorFor(e), stack: 'gas', data: d.arrays[e.key] || [] }));
  createChart(chartBox(c)).setOption(barOption(d.buckets, d.mode, list, unit));
  return c;
}

// ---------------------------------------------------------------- totals
export function totalsCard(parent, d) {
  const rows = totalsRows(d.totals);
  const c = card(parent, 'Totals');
  if (!rows.length) { empty(c, 'No energy sources configured for this site.'); return c; }
  const showCost = d.totals.sources.some((s) => s.cost !== null);
  const table = h('table', { class: 'totals' });
  table.append(h('thead', {}, h('tr', {},
    h('th', { text: 'Source' }), h('th', { text: 'Energy' }),
    ...(showCost ? [h('th', { text: 'Cost' })] : []))));
  const body = h('tbody');
  for (const g of rows) {
    for (const s of g.sources) {
      const chip = h('span', { class: 'chip' });
      chip.style.background = s.color || PALETTE[0];
      const tr = h('tr', {}, h('td', { class: 'name' }, chip, s.name),
        h('td', { text: formatValue(s.signed_energy, s.unit === 'm3' ? 'm³' : s.unit) }));
      if (showCost) tr.append(h('td', { text: formatCost(s.cost, d.currency) }));
      body.append(tr);
    }
    const label = g.group.charAt(0).toUpperCase() + g.group.slice(1) + ' total';
    const tr = h('tr', { class: 'total' }, h('td', { class: 'name', text: label }),
      h('td', { text: formatValue(g.energy, g.unit === 'm3' ? 'm³' : g.unit) }));
    if (showCost) tr.append(h('td', { text: formatCost(g.cost, d.currency) }));
    body.append(tr);
  }
  if (showCost && d.totals.total_cost !== null) {
    const span = 2;
    body.append(h('tr', { class: 'total' }, h('td', { class: 'name', text: 'Total costs', colspan: span }),
      h('td', { text: formatCost(d.totals.total_cost, d.currency) })));
  }
  table.append(body);
  c.append(table);
  return c;
}

// ---------------------------------------------------------------- distribution & gauges
export function distributionCard(parent, d) {
  const c = card(parent, 'Energy distribution');
  const has = d.summary.has || {};
  if (!Object.values(has).some(Boolean)) { empty(c, 'No energy sources configured for this site.'); return c; }
  c.append(renderDistribution(d.summary, { unit: unitOf(d, 'grid_import') }));
  return c;
}

export function balanceCard(parent, d) {
  const v = d.summary.values;
  const unit = unitOf(d, 'grid_import');
  if (!hasRole(d, 'grid_import')) return null;
  const c = card(parent, 'Grid energy balance');
  c.classList.add('balance');
  c.append(h('div', { class: 'line',
    text: `${formatValue(v.grid_import, unit)} - ${formatValue(v.grid_export, unit)} = ${formatValue(d.summary.net_grid, unit)}` }));
  const total = v.grid_import + v.grid_export || 1;
  const bar = h('div', { class: 'bar' });
  const exp = h('div'); exp.style.width = `${(v.grid_export / total) * 100}%`; exp.style.background = '#8353d1';
  const imp = h('div'); imp.style.width = `${(v.grid_import / total) * 100}%`; imp.style.background = '#488fc2';
  bar.append(exp, imp);
  c.append(bar);
  return c;
}

function gaugeOption(pct, color, text) {
  return {
    backgroundColor: 'transparent',
    series: [{
      type: 'gauge', startAngle: 200, endAngle: -20, min: 0, max: 100, radius: '95%', center: ['50%', '62%'],
      progress: { show: true, width: 14, itemStyle: { color } },
      axisLine: { lineStyle: { width: 14, color: [[1, '#2a2a2a']] } },
      pointer: { show: false }, axisTick: { show: false }, splitLine: { show: false },
      axisLabel: { show: false },
      detail: { formatter: () => text, color: '#e1e1e1', fontSize: 20, offsetCenter: [0, 0] },
      data: [{ value: Math.max(0, Math.min(100, pct ?? 0)) }],
    }],
  };
}

export function gaugeTiles(parent, d) {
  const s = d.summary;
  const unit = unitOf(d, 'grid_import');
  const tiles = h('div', { class: 'tiles' });
  const add = (caption, pct, color, text) => {
    const box = h('div', { class: 'chart' });
    const t = h('div', { class: 'tile' }, box, h('div', { class: 'cap', text: caption }));
    tiles.append(t);
    createChart(box).setOption(gaugeOption(pct, color, text));
  };
  if (hasRole(d, 'grid_import')) {
    const keep = s.values.grid_import > 0 ? (s.net_grid / s.values.grid_import) * 100 : 0;
    add('Net imported from the grid', keep, '#488fc2', formatValue(s.net_grid, unit));
  }
  if (hasRole(d, 'solar_energy')) {
    add('Self-consumed solar energy', s.self_consumed_solar_pct, '#ff9800', formatPct(s.self_consumed_solar_pct));
  }
  if (hasRole(d, 'grid_import')) {
    add('Self-sufficiency', s.self_sufficiency_pct, '#4db6ac', formatPct(s.self_sufficiency_pct));
  }
  parent.append(tiles);
  return tiles;
}

// ---------------------------------------------------------------- devices & sankey
export function devicesDetailCard(parent, d) {
  const det = devicesDetail(d.entities, d.arrays, d.buckets.length);
  if (!det.devices.length) return null;
  const c = card(parent, 'Individual devices detail');
  const list = det.devices.map((x, i) => ({
    name: x.entity.name, color: colorFor(x.entity, i + 1), stack: 'dev', data: x.values }));
  list.push({ name: 'Untracked consumption', color: '#6b6b6b', stack: 'dev', data: det.untracked });
  if (det.overTotal < -0.0005) list.push({ name: 'Over-reported consumption', color: '#4a4a4a', stack: 'dev', data: det.over });
  createChart(chartBox(c, 'tall')).setOption(barOption(d.buckets, d.mode, list, 'kWh'));
  return c;
}

export function devicesBarCard(parent, d) {
  const devs = d.totals.sources.filter((s) => s.role === 'device_energy').sort((a, b) => b.energy - a.energy);
  if (!devs.length) return null;
  const c = card(parent, 'Individual devices');
  const shown = devs.slice(0, 12);
  createChart(chartBox(c)).setOption({
    backgroundColor: 'transparent', animation: false,
    grid: { left: 8, right: 24, top: 10, bottom: 24, containLabel: true },
    tooltip: { trigger: 'item', valueFormatter: (v) => formatValue(v, 'kWh') },
    xAxis: { type: 'value', name: 'kWh', axisLabel: { color: TEXT }, splitLine: { lineStyle: { color: '#2a2a2a' } } },
    yAxis: { type: 'category', inverse: true, data: shown.map((s) => s.name), axisLabel: { color: '#e1e1e1' } },
    series: [{
      type: 'bar', barMaxWidth: 22,
      data: shown.map((s, i) => ({ value: s.energy, itemStyle: { color: PALETTE[(i + 1) % PALETTE.length] } })),
    }],
  });
  return c;
}

export function sankeyCard(parent, d) {
  const det = devicesDetail(d.entities, d.arrays, d.buckets.length);
  const devices = d.totals.sources.filter((s) => s.role === 'device_energy').map((s) => ({ name: s.name, total: s.energy }));
  const data = sankeyData(d.summary, devices, det.untrackedTotal);
  const c = card(parent, 'Energy flow');
  if (!data.links.length) { empty(c, 'No energy flow in this period.'); return c; }
  createChart(chartBox(c, 'sankey')).setOption({
    backgroundColor: 'transparent', animation: false,
    tooltip: { trigger: 'item', valueFormatter: (v) => formatValue(v, 'kWh') },
    series: [{
      type: 'sankey', left: 8, right: 120, top: 8, bottom: 8, nodeWidth: 14, nodeGap: 10,
      emphasis: { focus: 'adjacency' }, lineStyle: { color: 'gradient', opacity: 0.35 },
      label: { color: '#e1e1e1' },
      data: data.nodes.map((n) => ({ ...n, itemStyle: { color: nodeColor(n.name) } })),
      links: data.links,
    }],
  });
  return c;
}

function nodeColor(name) {
  if (name.startsWith('Solar')) return '#ff9800';
  if (name.startsWith('Grid (in)')) return '#488fc2';
  if (name.startsWith('Grid')) return '#8353d1';
  if (name.startsWith('Battery (in)')) return '#f06292';
  if (name.startsWith('Battery')) return '#4db6ac';
  if (name.startsWith('Home')) return '#0288d1';
  if (name.startsWith('Untracked')) return '#6b6b6b';
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

// ---------------------------------------------------------------- power sources (gauges over time)
const POWER_ROLES = ['solar_power', 'battery_power'];

export function powerSourcesCard(parent, d) {
  const grid = netGridPower(d.entities, d.gauges);
  const ents = d.entities.filter((e) => POWER_ROLES.includes(e.role));
  const c = card(parent, 'Power sources');
  if (!grid.length && !ents.length) { empty(c, 'No power sensors configured for this site.'); return c; }
  const list = [];
  if (grid.length) {
    const colored = d.entities.find((e) => e.role === 'grid_power_import');
    list.push({ name: 'Grid', color: colorFor(colored || {}), area: true, data: grid.map(([t, w]) => [t, w / 1000]) });
  }
  for (const e of ents) {
    list.push({
      name: e.name, color: colorFor(e), area: true,
      data: (d.gauges[e.key]?.points || []).filter((p) => p[1] !== null).map((p) => [p[0], p[1] / 1000]),
    });
  }
  createChart(chartBox(c)).setOption(
    lineOption(list, 'kW', (v) => formatValue(v, 'kW'), { min: d.range.start, max: d.range.end }));
  return c;
}

// ---------------------------------------------------------------- generic devices
/**
 * A card for one device instance without a dedicated dashboard (heat pump, water meter, ...):
 * a headline value per sensor and a chart per sensor. Driven only by the catalogue data in `inst`.
 */
export function deviceInstanceCard(parent, d, inst) {
  const sensors = inst.sensors.filter((s) => s.key);
  const c = card(parent, inst.name, inst.device_name);

  const tiles = h('div', { class: 'now-tiles' });
  for (const s of sensors) {
    const { label, value } = sensorSummary(s, d.arrays, d.gauges);
    tiles.append(h('div', { class: 'tile' },
      h('div', { class: 'n', text: s.name }),
      h('div', { class: 'v', text: formatValue(value, prettyUnit(s.unit)) }),
      h('div', { class: 'n', text: label })));
  }
  c.append(tiles);

  // Build all the boxes first and create the charts afterwards, so each chart measures a laid-out box.
  const wrap = h('div', { class: 'device-charts' });
  c.append(wrap);
  const boxes = sensors.map((s) => {
    const box = h('div', { class: 'chart small' });
    const unit = prettyUnit(s.unit);
    wrap.append(h('div', { class: 'device-chart' },
      h('div', { class: 'n', text: unit ? `${s.name} (${unit})` : s.name }), box));
    return box;
  });
  sensors.forEach((s, i) => {
    const unit = prettyUnit(s.unit);
    const color = colorFor(s, i);
    if (s.kind === 'counter') {
      const bars = [{ name: s.name, color, data: d.arrays[s.key] || [] }];
      createChart(boxes[i]).setOption(barOption(d.buckets, d.mode, bars, unit));
    } else {
      const points = (d.gauges[s.key]?.points || []).filter((p) => p[1] !== null).map((p) => [p[0], p[1]]);
      createChart(boxes[i]).setOption(lineOption(
        [{ name: s.name, color, data: points, area: false }], unit, (v) => formatValue(v, unit),
        { min: d.range.start, max: d.range.end }));
    }
  });
  return c;
}
