// App shell: login, site selector, tab routing and the bottom date bar.
import { api, setUnauthorizedHandler } from './api.js';
import { disposeAll, restoreLegends, snapshotLegends } from './charts.js';
import { loadHistory } from './data.js';
import { isCurrent, rangeLabel, shiftDate } from './format.js';
import * as summary from './views/summary.js';
import * as electricity from './views/electricity.js';
import * as gas from './views/gas.js';
import * as devices from './views/devices.js';
import { subscribeLive } from './livefeed.js';
import { renderNow } from './views/now.js';
import { renderOverview } from './views/overview.js';

const HISTORY_VIEWS = { summary, electricity, gas, devices };
const VIEWS = ['overview', 'summary', 'electricity', 'gas', 'devices', 'now'];
const LIVE_VIEWS = ['overview', 'now'];   // no date bar, no history refresh
const $ = (id) => document.getElementById(id);

const state = {
  view: 'overview', site: localStorage.getItem('site'), sites: [],
  date: new Date(), mode: localStorage.getItem('mode') || 'day',
};
let renderToken = 0;
let disposeLive = null;

// ---------------------------------------------------------------- login
function showLogin() { $('login').hidden = false; $('login-key').focus(); }
setUnauthorizedHandler(showLogin);

$('login-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  $('login-error').textContent = '';
  try {
    await api.login($('login-key').value);
    $('login-key').value = '';
    $('login').hidden = true;
    await start();
  } catch (err) {
    $('login-error').textContent = err.status === 429 ? 'Too many attempts. Try again in a minute.' : 'Invalid key.';
  }
});

$('logout').addEventListener('click', async () => {
  await api.logout().catch(() => {});
  cleanup();
  $('view').replaceChildren();
  showLogin();
});

// ---------------------------------------------------------------- shell
function route() {
  const name = location.hash.replace(/^#\//, '') || 'overview';
  state.view = VIEWS.includes(name) ? name : 'overview';
  for (const a of document.querySelectorAll('#tabs a')) a.classList.toggle('active', a.dataset.view === state.view);
  // The overview covers all sites, so the site selector does not apply there.
  $('site').hidden = state.view === 'overview';
  $('site-dot').hidden = state.view === 'overview';
}

function cleanup() {
  if (disposeLive) { disposeLive(); disposeLive = null; }
  stopLiveRefresh();
  disposeAll();
}

// ---------------------------------------------------------------- live refresh of history views
// While a current period (today, this week, this month) is shown, the live websocket tells us
// that new data arrived. We then reload the data, at most every REFRESH_MIN_MS, and redraw
// without the loading placeholder, keeping legend selection, legend page and scroll position.
const REFRESH_MIN_MS = 15000;
let unsubTrigger = null;
let refreshTimer = null;
let lastRefresh = 0;

function stopLiveRefresh() {
  if (unsubTrigger) { unsubTrigger(); unsubTrigger = null; }
  clearTimeout(refreshTimer);
  refreshTimer = null;
}

function startLiveRefresh() {
  stopLiveRefresh();
  if (LIVE_VIEWS.includes(state.view) || !state.site || !isCurrent(state.date, state.mode)) return;
  unsubTrigger = subscribeLive(state.site, scheduleRefresh);
}

function scheduleRefresh() {
  if (refreshTimer || document.hidden) return;
  const wait = Math.max(0, REFRESH_MIN_MS - (Date.now() - lastRefresh));
  refreshTimer = setTimeout(() => { refreshTimer = null; silentRefresh(); }, wait);
}

async function silentRefresh() {
  if (document.hidden || LIVE_VIEWS.includes(state.view) || !isCurrent(state.date, state.mode)) return;
  const token = renderToken;       // a normal render started meanwhile makes this one obsolete
  lastRefresh = Date.now();
  try {
    const data = await loadHistory(state.site, state.date, state.mode);
    if (token !== renderToken) return;
    const legends = snapshotLegends();
    const scrollY = window.scrollY;
    disposeAll();
    const el = $('view');
    el.replaceChildren();
    HISTORY_VIEWS[state.view].render(el, data);
    restoreLegends(legends);
    window.scrollTo(0, scrollY);
  } catch (_) { /* keep showing the previous data; the next push retries */ }
}

document.addEventListener('visibilitychange', () => { if (!document.hidden && unsubTrigger) scheduleRefresh(); });

function updateDateBar() {
  const show = !LIVE_VIEWS.includes(state.view);
  $('datebar').hidden = !show;
  if (!show) return;
  $('mode').value = state.mode;
  $('range-label').textContent = rangeLabel(state.date, state.mode);
  $('now-btn').disabled = isCurrent(state.date, state.mode);
  $('next').disabled = isCurrent(state.date, state.mode);
}

async function render() {
  const token = ++renderToken;
  const stale = () => token !== renderToken;
  cleanup();
  const el = $('view');
  el.replaceChildren();
  updateDateBar();
  if (!state.site && state.view !== 'overview') {
    el.append(Object.assign(document.createElement('div'), { className: 'empty', textContent: 'No sites available.' }));
    return;
  }
  el.append(Object.assign(document.createElement('div'), { className: 'loading', textContent: 'Loading…' }));
  try {
    if (state.view === 'overview') {
      const holder = document.createElement('div');
      el.append(holder);
      const dispose = await renderOverview(holder, selectSite, stale);
      if (stale()) { dispose(); return; }
      disposeLive = dispose;
      el.querySelector('.loading')?.remove();
    } else if (state.view === 'now') {
      // Attach first: ECharts needs a laid-out container to measure its size.
      const holder = document.createElement('div');
      el.append(holder);
      const dispose = await renderNow(holder, state.site, stale);
      if (stale()) { dispose(); return; }
      disposeLive = dispose;
      el.querySelector('.loading')?.remove();
    } else {
      const data = await loadHistory(state.site, state.date, state.mode);
      if (stale()) return;
      el.replaceChildren();
      HISTORY_VIEWS[state.view].render(el, data);
      startLiveRefresh();
    }
  } catch (err) {
    if (stale()) return;
    if (err.status === 401) return; // login overlay is already visible
    el.replaceChildren(Object.assign(document.createElement('div'), {
      className: 'error', textContent: `Could not load data: ${err.message}` }));
  }
}

function populateSites() {
  const sel = $('site');
  sel.replaceChildren(...state.sites.map((s) => Object.assign(document.createElement('option'), {
    value: s.id, textContent: s.name })));
  if (!state.sites.some((s) => s.id === state.site)) state.site = state.sites[0]?.id ?? null;
  if (state.site) sel.value = state.site;
  updateDot();
}

function updateDot() {
  const s = state.sites.find((x) => x.id === state.site);
  $('site-dot').classList.toggle('online', !!s?.online);
  $('site-dot').title = s?.online ? 'client connected' : 'client offline';
}

async function start() {
  state.sites = await api.sites();
  populateSites();
  route();
  await render();
}

// A card click on the overview: pick the site and open its Summary.
function selectSite(id) {
  state.site = id;
  localStorage.setItem('site', id);
  $('site').value = id;
  updateDot();
  location.hash = '#/summary';   // hashchange routes and renders
}

$('site').addEventListener('change', (ev) => {
  state.site = ev.target.value;
  localStorage.setItem('site', state.site);
  updateDot();
  render();
});
$('mode').addEventListener('change', (ev) => {
  state.mode = ev.target.value;
  localStorage.setItem('mode', state.mode);
  render();
});
$('prev').addEventListener('click', () => { state.date = shiftDate(state.date, state.mode, -1); render(); });
$('next').addEventListener('click', () => { state.date = shiftDate(state.date, state.mode, 1); render(); });
$('now-btn').addEventListener('click', () => { state.date = new Date(); render(); });
window.addEventListener('hashchange', () => { route(); render(); });

// refresh online dots periodically
setInterval(async () => {
  if ($('login').hidden === false) return;
  try { state.sites = await api.sites(); updateDot(); } catch (_) { /* ignore */ }
}, 30000);

(async () => {
  try {
    await api.me();
    await start();
  } catch (err) {
    if (err.status !== 401) {
      $('view').textContent = `Cannot reach the server: ${err.message}`;
    }
  }
})();
