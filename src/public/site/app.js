'use strict';

const API = '/api/public';

const PALETTE = ['#e0556b', '#e0a855', '#4f8cff', '#4caf74', '#a855e0', '#55c2e0', '#e08555', '#8cff4f'];
function colorFor(label) {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return n;
};

async function api(path) {
  const res = await fetch(API + path);
  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = { raw: text }; } }
  if (!res.ok) throw new Error((data && (data.error || data.message)) || `HTTP ${res.status}`);
  return data;
}

function labelFor(r) {
  return r.name || r.id;
}

// slug -> { section, grid, canvas, msg, readings: [], tiles: Map(id->element) }
const state = new Map();

const DAY_MS = 24 * 3600 * 1000;

async function loadAll() {
  $('#err').textContent = '';
  const root = $('#sites');
  root.innerHTML = '';
  state.clear();
  try {
    const { sites } = await api('/sites');
    if (!sites.length) {
      root.appendChild(el('p', { class: 'hint' }, 'No public sites available.'));
      return;
    }
    for (const s of sites) {
      const section = el('div', { class: 'site' },
        el('div', { class: 'site-head' },
          el('div', { class: 'site-name', text: s.name }),
          el('div', { class: 'site-slug', text: '/' + s.slug }),
        ),
        el('p', { class: 'hint', text: 'Loading…' }),
      );
      root.appendChild(section);
      state.set(s.slug, { section, grid: null, canvas: null, msg: null, readings: [], tiles: new Map() });
      loadSite(s.slug);
    }
  } catch (err) {
    $('#err').textContent = err.message;
  }
}

async function loadSite(slug) {
  const st = state.get(slug);
  if (!st) return;
  try {
    const [latestRes, readingsRes] = await Promise.all([
      api(`/sites/${encodeURIComponent(slug)}/latest`),
      api(`/sites/${encodeURIComponent(slug)}/readings`),
    ]);
    renderSite(st, latestRes.site, latestRes.latest || [], readingsRes.readings || []);
  } catch (err) {
    st.section.innerHTML = '';
    st.section.appendChild(el('div', { class: 'site-head' },
      el('div', { class: 'site-name', text: slug }),
    ));
    st.section.appendChild(el('p', { class: 'err', text: err.message }));
  }
}

function renderSite(st, site, latest, readings) {
  st.section.innerHTML = '';
  st.section.appendChild(el('div', { class: 'site-head' },
    el('div', { class: 'site-name', text: site.name }),
    el('div', { class: 'site-slug', text: '/' + site.slug }),
  ));

  const grid = el('div', { class: 'entities' });
  st.grid = grid;
  st.tiles = new Map();
  if (latest.length === 0) {
    grid.appendChild(el('p', { class: 'hint' }, 'No readings yet.'));
  } else {
    for (const r of latest) grid.appendChild(makeTile(st, r));
  }
  st.section.appendChild(grid);

  st.canvas = el('canvas', { width: 900, height: 260 });
  st.section.appendChild(st.canvas);
  st.msg = el('p', { class: 'hint', text: `${readings.length} points (last 24h)` });
  st.section.appendChild(st.msg);

  st.readings = readings.slice();
  drawChart(st.canvas, st.readings);
}

function makeTile(st, r) {
  const tile = el('div', { class: 'entity' },
    el('div', { class: 'k', text: labelFor(r), title: r.id }),
    el('div', { class: 'v', text: String(r.value) }),
    el('div', { class: 'u', text: r.unit || '' }),
    el('div', { class: 't', text: new Date(r.recordedAt || Date.now()).toLocaleString() }),
  );
  st.tiles.set(r.id, tile);
  return tile;
}

function upsertTile(st, r) {
  const existing = st.tiles.get(r.id);
  if (existing) {
    existing.querySelector('.v').textContent = String(r.value);
    existing.querySelector('.u').textContent = r.unit || '';
    existing.querySelector('.t').textContent = new Date(r.recordedAt || Date.now()).toLocaleString();
  } else {
    if (st.grid) {
      const hint = st.grid.querySelector('.hint');
      if (hint) hint.remove();
      st.grid.appendChild(makeTile(st, r));
    }
  }
}

function handleEvent(evt) {
  if (!evt || evt.type !== 'reading') return;
  const st = state.get(evt.site && evt.site.slug);
  if (!st || !st.grid) return;
  const r = evt.reading;
  upsertTile(st, r);
  st.readings.push(r);
  const cutoff = Date.now() - DAY_MS;
  st.readings = st.readings.filter((x) => new Date(x.recordedAt).getTime() >= cutoff);
  if (st.canvas) drawChart(st.canvas, st.readings);
  if (st.msg) st.msg.textContent = `${st.readings.length} points (last 24h)`;
}

// ---------- live events ----------
function connectEvents() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${window.location.host}/events`;
  let ws;
  let backoff = 1000;

  const open = () => {
    ws = new WebSocket(url);
    const badge = $('#live');
    ws.onopen = () => {
      backoff = 1000;
      if (badge) { badge.textContent = '● live'; badge.className = 'live on'; }
    };
    ws.onmessage = (e) => {
      try { handleEvent(JSON.parse(e.data)); } catch { /* ignore */ }
    };
    ws.onclose = () => {
      if (badge) { badge.textContent = '○ off'; badge.className = 'live off'; }
      setTimeout(open, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    ws.onerror = () => { if (ws.readyState === ws.OPEN) ws.close(); };
  };
  open();
}

// ---------- chart ----------
function drawChart(canvas, readings) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width = canvas.clientWidth * window.devicePixelRatio;
  const H = canvas.height = 260 * window.devicePixelRatio;
  ctx.clearRect(0, 0, W, H);

  const pad = { l: 50, r: 12, t: 16, b: 28 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;

  if (readings.length === 0) {
    ctx.fillStyle = '#9aa3b2';
    ctx.font = `${14 * window.devicePixelRatio}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillText('No readings', W / 2, H / 2);
    return;
  }

  const grouped = {};
  for (const r of readings) {
    if (!grouped[r.id]) grouped[r.id] = { label: labelFor(r), pts: [] };
    grouped[r.id].pts.push({ ts: new Date(r.recordedAt).getTime(), v: Number(r.value) });
  }
  const keys = Object.keys(grouped);
  let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity;
  for (const k of keys) for (const p of grouped[k].pts) {
    if (p.ts < tMin) tMin = p.ts;
    if (p.ts > tMax) tMax = p.ts;
    if (p.v < vMin) vMin = p.v;
    if (p.v > vMax) vMax = p.v;
  }
  if (tMin === tMax) tMax = tMin + 1;
  if (vMin === vMax) vMax = vMin + 1;
  const vPad = (vMax - vMin) * 0.1;
  vMin -= vPad; vMax += vPad;

  const x = (t) => pad.l + ((t - tMin) / (tMax - tMin)) * plotW;
  const y = (v) => pad.t + (1 - (v - vMin) / (vMax - vMin)) * plotH;

  ctx.strokeStyle = '#2a2f3a';
  ctx.lineWidth = 1 * window.devicePixelRatio;
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t); ctx.lineTo(pad.l, pad.t + plotH);
  ctx.lineTo(pad.l + plotW, pad.t + plotH);
  ctx.stroke();

  ctx.fillStyle = '#9aa3b2';
  ctx.font = `${11 * window.devicePixelRatio}px sans-serif`;
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const val = vMin + ((vMax - vMin) * i) / 4;
    const yy = y(val);
    ctx.fillText(val.toFixed(1), pad.l - 6, yy + 4);
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(pad.l + plotW, yy); ctx.stroke();
  }
  ctx.textAlign = 'center';
  for (let i = 0; i <= 4; i++) {
    const t = tMin + ((tMax - tMin) * i) / 4;
    const d = new Date(t);
    ctx.fillText(`${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`, x(t), pad.t + plotH + 18);
  }

  for (const k of keys) {
    const pts = grouped[k].pts.sort((a, b) => a.ts - b.ts);
    ctx.strokeStyle = colorFor(k);
    ctx.lineWidth = 2 * window.devicePixelRatio;
    ctx.beginPath();
    pts.forEach((p, i) => {
      const px = x(p.ts), py = y(p.v);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }

  ctx.textAlign = 'left';
  let lx = pad.l + 8;
  for (const k of keys) {
    const label = grouped[k].label;
    ctx.fillStyle = colorFor(k);
    ctx.fillRect(lx, pad.t + 2, 12 * window.devicePixelRatio, 12 * window.devicePixelRatio);
    ctx.fillStyle = '#e6e9ef';
    ctx.fillText(label, lx + 16 * window.devicePixelRatio, pad.t + 12 * window.devicePixelRatio);
    lx += (16 + ctx.measureText(label).width + 24) * window.devicePixelRatio;
  }
}

// ---------- init ----------
document.addEventListener('DOMContentLoaded', () => {
  $('#refreshBtn').addEventListener('click', loadAll);
  loadAll();
  connectEvents();
});
