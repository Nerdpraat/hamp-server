// Energy distribution diagram (SVG), modelled on the Home Assistant energy card.
import { formatNumber } from './format.js';
import { ICONS } from './icons.js';

const NS = 'http://www.w3.org/2000/svg';
const COLORS = {
  solar: '#ff9800', grid: '#488fc2', export: '#8353d1', battery: '#4db6ac',
  batteryIn: '#f06292', gas: '#8e021b', home: '#488fc2',
};
const CARD_BG = '#1c1c1c';
const ICON_COLOR = '#e1e1e1';
const R = 41;            // node radius
const ICON = 22;         // icon size in px
const LINE = 14;         // text line height

function el(name, attrs = {}) {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

/** One line of text made of coloured segments: [{t, c}] or a plain string. */
function textLine(x, y, segments, cls = '', anchor = 'middle') {
  // Inline style: it wins over any (possibly stale, cached) stylesheet rule.
  const t = el('text', { x, y, class: cls, style: `text-anchor:${anchor}` });
  for (const seg of [].concat(segments)) {
    const s = typeof seg === 'string' ? { t: seg } : seg;
    const span = el('tspan', s.c ? { fill: s.c } : {});
    span.textContent = s.t;
    t.append(span);
  }
  return t;
}

function icon(name, x, y) {
  return el('path', {
    d: ICONS[name], fill: ICON_COLOR, transform: `translate(${x},${y}) scale(${ICON / 24})`,
  });
}

function batteryIcon(soc) {
  if (soc === null || soc === undefined) return 'battery-outline';
  if (soc < 10) return 'battery-alert-variant-outline';
  if (soc >= 95) return 'battery';
  return `battery-${Math.min(90, Math.round(soc / 10) * 10)}`;
}

/**
 * A circle with an icon on top and value lines below it.
 * `percent` puts a percentage next to the icon (battery level).
 */
function node(cx, cy, color, { iconName, percent, lines, label, labelAbove = false }) {
  const g = el('g');
  // Solid fill so connecting lines never show through the content.
  g.append(el('circle', { cx, cy, r: R, fill: CARD_BG, stroke: color, 'stroke-width': 3 }));

  const height = ICON + 2 + lines.length * LINE;
  const top = cy - height / 2;
  if (percent !== undefined) {
    // The battery glyph only fills x 6..18 of its 24 px box, so centre on that visible part.
    const glyphWidth = (12 / 24) * ICON;
    const glyphPad = (6 / 24) * ICON;
    const textWidth = percent.length * 6.4;              // about 6.4 px per character at 11 px
    const start = cx - (glyphWidth + 3 + textWidth) / 2;
    g.append(icon(iconName, start - glyphPad, top));
    g.append(textLine(start + glyphWidth + 3, top + 15, percent, '', 'start'));
  } else {
    g.append(icon(iconName, cx - ICON / 2, top));
  }
  lines.forEach((line, i) => g.append(textLine(cx, top + ICON + 2 + 11 + i * LINE, line)));
  if (label) {
    // Top-row nodes have their connecting lines below them, so their label goes above.
    g.append(textLine(cx, labelAbove ? cy - R - 9 : cy + R + 17, label, 'label'));
  }
  return g;
}

/** Ring around the Home node: where its energy comes from (solar, battery, grid). */
function homeRing(cx, cy, parts) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  const g = el('g', { transform: `rotate(-90 ${cx} ${cy})` });
  if (!(total > 0)) return g;
  const circ = 2 * Math.PI * R;
  let offset = 0;
  for (const p of parts) {
    if (!(p.value > 0)) continue;
    const len = (p.value / total) * circ;
    g.append(el('circle', {
      cx, cy, r: R, fill: 'none', stroke: p.color, 'stroke-width': 5,
      'stroke-dasharray': `${len} ${circ - len}`, 'stroke-dashoffset': -offset,
    }));
    offset += len;
  }
  return g;
}

// Node centres; the viewBox starts above 0 to leave room for the top-row labels.
const P = { solar: [200, 48], gas: [352, 48], grid: [48, 160], home: [352, 160], battery: [200, 272] };
// x where lines meet the side nodes' circle edges (a little inside, the filled circle hides overlap)
const GRID_EDGE = 90;
const HOME_EDGE = 312;

// Each flow has its own lane so lines do not run on top of each other, and corners are rounded.
const PATHS = {
  solar_to_battery: 'M200,89 V231',
  solar_to_home: 'M210,89 V122 Q210,148 236,148 H' + HOME_EDGE,
  solar_to_grid: 'M190,89 V122 Q190,148 164,148 H' + GRID_EDGE,
  grid_to_home: `M${GRID_EDGE},160 H${HOME_EDGE}`,
  battery_to_home: 'M210,231 V198 Q210,172 236,172 H' + HOME_EDGE,
  // Shared by both directions (grid charging the battery, battery exporting to the grid).
  battery_grid: 'M190,231 V198 Q190,172 164,172 H' + GRID_EDGE,
  gas_to_home: 'M352,89 V119',
};

/**
 * summary: server /summary payload; unit: energy unit.
 * Sources the site does not have (summary.has.*) are not drawn.
 */
export function renderDistribution(summary, { unit = 'kWh', gasUnit = 'm³', homeName = 'Home' } = {}) {
  const v = summary.values;
  const f = summary.flows;
  const has = summary.has || {};
  const svg = el('svg', { viewBox: '0 -18 400 368', class: 'dist', role: 'img' });
  const max = Math.max(1, v.home, v.solar_energy, v.grid_import, v.grid_export, v.battery_charge, v.battery_discharge);
  const width = (x) => 1.5 + (x / max) * 5;

  // Faint base lines for every connection this site can have, so the diagram always reads as
  // connected, even when little or no energy flows. Active flows are drawn on top.
  const base = (name) => svg.append(el('path', {
    d: PATHS[name], stroke: '#4a4a4a', 'stroke-width': 1.5, fill: 'none',
    'stroke-dasharray': '3 5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  }));
  if (has.solar_energy) base('solar_to_home');
  if (has.solar_energy && has.grid_export) base('solar_to_grid');
  if (has.solar_energy && has.battery_charge) base('solar_to_battery');
  if (has.grid_import) base('grid_to_home');
  if ((has.grid_import && has.battery_charge) || (has.battery_discharge && has.grid_export)) base('battery_grid');
  if (has.battery_discharge) base('battery_to_home');
  if (has.gas) base('gas_to_home');

  const flow = (name, color, value) => {
    if (!(value > 0)) return;
    svg.append(el('path', {
      d: PATHS[name], stroke: color, 'stroke-width': width(value), fill: 'none',
      'stroke-linecap': 'round', 'stroke-linejoin': 'round', opacity: 0.85,
    }));
  };
  if (has.solar_energy) {
    flow('solar_to_home', COLORS.solar, f.solar_to_home);
    flow('solar_to_grid', COLORS.export, f.solar_to_grid);
    if (has.battery_charge) flow('solar_to_battery', COLORS.solar, f.solar_to_battery);
  }
  if (has.grid_import) {
    flow('grid_to_home', COLORS.grid, f.grid_to_home);
    if (has.battery_charge) flow('battery_grid', COLORS.grid, f.grid_to_battery);
  }
  if (has.battery_discharge) {
    flow('battery_to_home', COLORS.battery, f.battery_to_home);
    flow('battery_grid', COLORS.battery, f.battery_to_grid);
  }
  if (has.gas) flow('gas_to_home', COLORS.gas, v.gas);

  const kwh = (x) => `${formatNumber(x)} ${unit}`;
  if (has.solar_energy) {
    svg.append(node(...P.solar, COLORS.solar, {
      iconName: 'solar-power-variant', lines: [kwh(v.solar_energy)], label: 'Solar', labelAbove: true }));
  }
  if (has.grid_import || has.grid_export) {
    svg.append(node(...P.grid, COLORS.grid, {
      iconName: 'transmission-tower',
      lines: [[{ t: `← ${kwh(v.grid_export)}`, c: COLORS.export }],
              [{ t: `→ ${kwh(v.grid_import)}`, c: COLORS.grid }]],
      label: 'Grid' }));
  }
  svg.append(node(...P.home, COLORS.home, {
    iconName: 'home', lines: [kwh(v.home)], label: homeName }));
  svg.append(homeRing(...P.home, [   // on top of the circle, so its colours are visible
    { value: f.solar_to_home, color: COLORS.solar },
    { value: f.battery_to_home, color: COLORS.battery },
    { value: f.grid_to_home, color: COLORS.grid },
  ]));
  if (has.battery_charge || has.battery_discharge) {
    const soc = summary.battery_soc;
    svg.append(node(...P.battery, COLORS.battery, {
      iconName: batteryIcon(soc),
      percent: soc === null || soc === undefined ? undefined : `${formatNumber(soc, 0)}%`,
      lines: [[{ t: `↓ ${kwh(v.battery_charge)}`, c: COLORS.batteryIn }],
              [{ t: `↑ ${kwh(v.battery_discharge)}`, c: COLORS.battery }]],
      label: 'Battery' }));
  }
  if (has.gas) {
    svg.append(node(...P.gas, COLORS.gas, {
      iconName: 'fire', lines: [`${formatNumber(v.gas)} ${gasUnit}`], label: 'Gas', labelAbove: true }));
  }
  return svg;
}
