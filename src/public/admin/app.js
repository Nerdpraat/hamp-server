'use strict';

const API = '/api';
const TOKEN_KEY = 'np_token';

const PALETTE = ['#e0556b', '#e0a855', '#4f8cff', '#4caf74', '#a855e0', '#55c2e0', '#e08555', '#8cff4f'];
function colorFor(label) {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

const state = { user: null, token: null, sites: [], users: [], current: null, entities: [] };

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const el = (tag, attrs = {}, ...children) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return n;
};

async function api(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(API + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
  }
  if (!res.ok) {
    const msg = (data && (data.error || data.message)) || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function setErr(node, err) {
  if (node) node.textContent = err ? (err.message || String(err)) : '';
}

function show(viewId) {
  ['authView', 'sitesView', 'detailView', 'accountView'].forEach((id) => {
    const n = document.getElementById(id);
    if (n) n.classList.toggle('hidden', id !== viewId);
  });
  $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === viewId.replace('View', '')));
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  state.token = null;
  state.user = null;
  $('#nav').classList.add('hidden');
  show('authView');
}

// ---------- auth ----------
async function bootstrap() {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) return show('authView');
  state.token = token;
  try {
    const data = await api('/auth/me');
    state.user = data.user;
    afterLogin();
  } catch {
    logout();
  }
}

function afterLogin() {
  $('#nav').classList.remove('hidden');
  $('#userBadge').textContent = `${state.user.email}${state.user.role === 'admin' ? ' · admin' : ''}`;
  const admin = state.user.role === 'admin';
  $('#ownerWrap').classList.toggle('hidden', !admin);
  $('#editOwnerWrap').classList.toggle('hidden', !admin);
  show('sitesView');
  loadSites();
  if (admin) loadUsers();
}

async function loadUsers() {
  try {
    const data = await api('/users');
    state.users = data.users || [];
    populateOwnerSelects();
  } catch (err) {
    state.users = [];
    populateOwnerSelects();
    const wrap = $('#ownerWrap');
    let note = wrap.querySelector('.load-err');
    if (!note) {
      note = el('p', { class: 'err load-err' });
      wrap.appendChild(note);
    }
    note.textContent = `Could not load users (${err.message}). Owner will be set to you.`;
    console.error('loadUsers failed', err);
  }
}

function populateOwnerSelects(value) {
  for (const sel of [$('#ownerWrap select'), $('#editOwnerWrap select')]) {
    if (!sel) continue;
    sel.innerHTML = '';
    sel.appendChild(el('option', { value: '', text: '(auto)' }));
    for (const u of state.users) {
      const opt = el('option', { value: String(u.id), text: `${u.email}${u.name ? ' · ' + u.name : ''}${u.role === 'admin' ? ' · admin' : ''}` });
      if (value !== undefined && String(u.id) === String(value)) opt.selected = true;
      sel.appendChild(opt);
    }
  }
}

function bindAuthForms() {
  $$('.tab').forEach((t) => t.addEventListener('click', () => {
    $$('.tab').forEach((x) => x.classList.toggle('active', x === t));
    $('#loginForm').classList.toggle('hidden', t.dataset.tab !== 'login');
    $('#registerForm').classList.toggle('hidden', t.dataset.tab !== 'register');
  }));

  $('#loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    setErr($('#loginErr'), null);
    const fd = new FormData(e.target);
    try {
      const data = await api('/auth/login', { method: 'POST', auth: false, body: {
        email: fd.get('email'), password: fd.get('password'),
      } });
      state.token = data.token;
      state.user = data.user;
      localStorage.setItem(TOKEN_KEY, data.token);
      e.target.reset();
      afterLogin();
    } catch (err) { setErr($('#loginErr'), err); }
  });

  $('#registerForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    setErr($('#registerErr'), null);
    const fd = new FormData(e.target);
    try {
      const data = await api('/auth/register', { method: 'POST', auth: false, body: {
        name: fd.get('name') || undefined,
        email: fd.get('email'),
        password: fd.get('password'),
      } });
      state.token = data.token;
      state.user = data.user;
      localStorage.setItem(TOKEN_KEY, data.token);
      e.target.reset();
      afterLogin();
    } catch (err) { setErr($('#registerErr'), err); }
  });

  $('#logoutBtn').addEventListener('click', logout);
}

// ---------- sites ----------
async function loadSites() {
  setErr($('#sitesErr'), null);
  try {
    const data = await api('/sites');
    state.sites = data.sites;
    renderSites();
  } catch (err) { setErr($('#sitesErr'), err); }
}

function renderSites() {
  const root = $('#sitesList');
  root.innerHTML = '';
  if (state.sites.length === 0) {
    root.appendChild(el('p', { class: 'hint' }, 'No sites yet. Create one with “+ New site”.'));
    return;
  }
  for (const s of state.sites) {
    const card = el('div', { class: 'site-card' },
      el('div', { class: 'name', text: s.name }),
      el('div', { class: 'slug', text: '/' + s.slug }),
      el('span', { class: 'tag ' + (s.isPublic ? 'public' : 'private'), text: s.isPublic ? 'public' : 'private' }),
    );
    card.addEventListener('click', () => openSite(s.id));
    root.appendChild(card);
  }
}

function bindSiteForms() {
  $('#newSiteBtn').addEventListener('click', () => {
    $('#siteFormTitle').textContent = 'Create site';
    $('#siteForm').reset();
    $('#siteForm [name=isPublic]').checked = true;
    populateOwnerSelects(state.user.id);
    $('#siteForm').classList.remove('hidden');
    $('#siteForm').dataset.mode = 'create';
  });
  $('#siteForm [data-cancel]').addEventListener('click', () => $('#siteForm').classList.add('hidden'));

  $('#siteForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    setErr($('#siteFormErr'), null);
    const fd = new FormData(e.target);
    const body = { name: fd.get('name'), isPublic: fd.get('isPublic') === 'on' };
    if (state.user.role === 'admin' && fd.get('ownerId')) body.ownerId = Number(fd.get('ownerId'));
    try {
      await api('/sites', { method: 'POST', body });
      $('#siteForm').classList.add('hidden');
      await loadSites();
    } catch (err) { setErr($('#siteFormErr'), err); }
  });
}

// ---------- site detail ----------
async function openSite(siteId) {
  try {
    const data = await api(`/sites/${siteId}`);
    state.current = data.site;
    const ents = await api(`/sites/${siteId}/entities`);
    state.entities = ents.entities;
    populateEntityFilter();
    renderDetail();
    await loadLatest();
    await loadReadings();
    show('detailView');
  } catch (err) {
    setErr($('#sitesErr'), err);
    show('sitesView');
  }
}

function renderDetail() {
  const s = state.current;
  $('#detailName').textContent = s.name;
  const meta = $('#detailMeta');
  meta.innerHTML = '';
  meta.appendChild(el('div', { }, el('span', { text: 'slug: ' }), el('code', { text: s.slug })));
  meta.appendChild(el('div', {}, el('span', { text: 'api key: ' }), el('code', { text: s.apiKey })));
  meta.appendChild(el('div', {}, el('span', { text: 'id: ' }), el('code', { text: String(s.id) })));
  meta.appendChild(el('div', { text: 'visibility: ' + (s.isPublic ? 'public' : 'private') }));
  if (s.ownerId) meta.appendChild(el('div', { text: 'owner id: ' + s.ownerId }));

  const editForm = $('#editSiteForm');
  editForm.querySelector('[name=name]').value = s.name;
  editForm.querySelector('[name=isPublic]').checked = s.isPublic;
  populateOwnerSelects(s.ownerId);

  renderEntityRows();
}

function renderEntityRows() {
  const root = $('#entityRows');
  root.innerHTML = '';
  $('#entitiesEmpty').classList.toggle('hidden', state.entities.length > 0);
  for (const e of state.entities) {
    const row = el('div', { class: 'entity-row' },
      el('div', { class: 'kind', text: e.entityId, title: e.entityId }),
      el('input', { type: 'text', name: `name-${e.entityId}`, placeholder: 'display name', value: e.name || '' }),
      el('input', { type: 'text', name: `unit-${e.entityId}`, placeholder: 'W', value: e.unit || '' }),
      el('button', { type: 'button', class: 'btn btn-ghost btn-del', text: 'Remove', onclick: () => removeEntity(e.entityId) })
    );
    row.dataset.entityId = e.entityId;
    root.appendChild(row);
  }
}

async function removeEntity(entityId) {
  try {
    await api(`/sites/${state.current.id}/entities`, { method: 'DELETE', body: { entityId } });
    state.entities = state.entities.filter((e) => e.entityId !== entityId);
    renderEntityRows();
  } catch (err) { alert(err.message); }
}

function addEntityRow(entityId) {
  if (state.entities.some((e) => e.entityId === entityId)) return;
  state.entities.push({ entityId, name: null, unit: '' });
  renderEntityRows();
  const inp = $(`#entityRows [data-entity-id="${CSS.escape(entityId)}"] input[name^=name-]`);
  if (inp) inp.focus();
}

function bindDetail() {
  $('#backBtn').addEventListener('click', () => show('sitesView'));

  $('#editSiteBtn').addEventListener('click', () => {
    $('#editSiteForm').classList.toggle('hidden');
  });
  $('#editSiteForm [data-cancel]').addEventListener('click', () => $('#editSiteForm').classList.add('hidden'));

  $('#editSiteForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = { name: fd.get('name'), isPublic: fd.get('isPublic') === 'on' };
    if (state.user.role === 'admin' && fd.get('ownerId')) body.ownerId = Number(fd.get('ownerId'));
    try {
      const data = await api(`/sites/${state.current.id}`, { method: 'PATCH', body });
      state.current = data.site;
      renderDetail();
      $('#editSiteForm').classList.add('hidden');
      await loadSites();
    } catch (err) { alert(err.message); }
  });

  $('#regenKeyBtn').addEventListener('click', async () => {
    if (!confirm('Regenerate the API key? The old key will stop working immediately.')) return;
    try {
      const data = await api(`/sites/${state.current.id}`, { method: 'PATCH', body: { regenerateApiKey: true } });
      state.current = data.site;
      renderDetail();
    } catch (err) { alert(err.message); }
  });

  $('#deleteSiteBtn').addEventListener('click', async () => {
    if (!confirm(`Delete site “${state.current.name}”? This cannot be undone.`)) return;
    try {
      await api(`/sites/${state.current.id}`, { method: 'DELETE' });
      state.current = null;
      await loadSites();
      show('sitesView');
    } catch (err) { alert(err.message); }
  });

  $('#entitiesForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const entities = [];
    for (const ent of state.entities) {
      const name = (fd.get(`name-${ent.entityId}`) || '').trim();
      const unit = (fd.get(`unit-${ent.entityId}`) || '').trim();
      entities.push({
        entityId: ent.entityId,
        name: name || undefined,
        unit: unit || undefined,
      });
    }
    setErr($('#entitiesMsg'), null);
    try {
      const data = await api(`/sites/${state.current.id}/entities`, { method: 'PUT', body: { entities } });
      state.entities = data.entities;
      renderEntityRows();
      populateEntityFilter();
      $('#entitiesMsg').textContent = 'Saved.';
      $('#entitiesMsg').className = 'hint';
      setTimeout(() => { $('#entitiesMsg').textContent = ''; }, 2000);
    } catch (err) {
      $('#entitiesMsg').className = 'err';
      setErr($('#entitiesMsg'), err);
    }
  });

  $('#addEntityBtn').addEventListener('click', () => {
    const inp = $('#newEntityId');
    const entityId = (inp.value || '').trim();
    if (!entityId) { inp.focus(); return; }
    addEntityRow(entityId);
    inp.value = '';
  });

  $('#refreshReadings').addEventListener('click', () => { loadLatest(); loadReadings(); });
  $('#entityFilter').addEventListener('change', () => loadReadings());
}

function populateEntityFilter() {
  const sel = $('#entityFilter');
  sel.innerHTML = '';
  sel.appendChild(el('option', { value: '', text: 'all' }));
  for (const e of state.entities) {
    sel.appendChild(el('option', { value: e.entityId, text: e.name || e.entityId }));
  }
}

async function loadLatest() {
  const root = $('#latestGrid');
  root.innerHTML = '';
  try {
    const data = await api(`/sites/${state.current.id}/latest`);
    if (!data.latest || data.latest.length === 0) {
      root.appendChild(el('p', { class: 'hint' }, 'No readings yet.'));
      return;
    }
    for (const r of data.latest) {
      const ent = state.entities.find((e) => e.entityId === r.entityId);
      const label = (ent && ent.name) || r.entityId;
      root.appendChild(el('div', { class: 'latest-tile' },
        el('div', { class: 'k', text: label, title: r.entityId }),
        el('div', { class: 'v', text: String(r.value) }),
        el('div', { class: 'u', text: r.unit || '' }),
        el('div', { class: 't', text: new Date(r.recordedAt || r.ts || Date.now()).toLocaleString() }),
      ));
    }
  } catch (err) {
    root.appendChild(el('p', { class: 'err', text: err.message }));
  }
}

async function loadReadings() {
  const entityId = $('#entityFilter').value;
  const msg = $('#readingsMsg');
  try {
    const data = await api(`/sites/${state.current.id}/readings` + (entityId ? `?entityId=${encodeURIComponent(entityId)}` : ''));
    drawChart(data.readings || []);
    msg.textContent = `${(data.readings || []).length} points`;
  } catch (err) {
    drawChart([]);
    msg.textContent = err.message;
  }
}

// ---------- chart ----------
function drawChart(readings) {
  const canvas = $('#chart');
  const ctx = canvas.getContext('2d');
  const W = canvas.width = canvas.clientWidth * window.devicePixelRatio;
  const H = canvas.height = 320 * window.devicePixelRatio;
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
    const key = r.entityId;
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push({ ts: new Date(r.recordedAt || r.ts).getTime(), v: Number(r.value) });
  }
  const keys = Object.keys(grouped);
  let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity;
  for (const k of keys) for (const p of grouped[k]) {
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

  // axes
  ctx.strokeStyle = '#2a2f3a';
  ctx.lineWidth = 1 * window.devicePixelRatio;
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t); ctx.lineTo(pad.l, pad.t + plotH);
  ctx.lineTo(pad.l + plotW, pad.t + plotH);
  ctx.stroke();

  // y labels
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
  // x labels
  ctx.textAlign = 'center';
  for (let i = 0; i <= 4; i++) {
    const t = tMin + ((tMax - tMin) * i) / 4;
    const d = new Date(t);
    ctx.fillText(`${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`, x(t), pad.t + plotH + 18);
  }

  // series
  for (const k of keys) {
    const pts = grouped[k].sort((a, b) => a.ts - b.ts);
    ctx.strokeStyle = colorFor(k);
    ctx.lineWidth = 2 * window.devicePixelRatio;
    ctx.beginPath();
    pts.forEach((p, i) => {
      const px = x(p.ts), py = y(p.v);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }

  // legend
  ctx.textAlign = 'left';
  let lx = pad.l + 8;
  for (const k of keys) {
    const ent = state.entities.find((e) => e.entityId === k);
    const label = (ent && ent.name) || k;
    ctx.fillStyle = colorFor(k);
    ctx.fillRect(lx, pad.t + 2, 12 * window.devicePixelRatio, 12 * window.devicePixelRatio);
    ctx.fillStyle = '#e6e9ef';
    ctx.fillText(label, lx + 16 * window.devicePixelRatio, pad.t + 12 * window.devicePixelRatio);
    lx += (16 + ctx.measureText(label).width + 24) * window.devicePixelRatio;
  }
}

// ---------- account ----------
function renderAccount() {
  $('#accountInfo').textContent = JSON.stringify(state.user, null, 2);
}

// ---------- nav ----------
function bindNav() {
  $$('.nav-btn').forEach((b) => b.addEventListener('click', () => {
    const v = b.dataset.view;
    if (v === 'sites') { show('sitesView'); loadSites(); }
    else if (v === 'account') { renderAccount(); show('accountView'); }
  }));
}

// ---------- init ----------
document.addEventListener('DOMContentLoaded', () => {
  bindAuthForms();
  bindSiteForms();
  bindDetail();
  bindNav();
  bootstrap();
});
