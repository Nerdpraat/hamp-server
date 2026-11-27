// Pure formatting and date-range helpers (no DOM access, unit tested with node).

export const MODES = ['day', 'week', 'month'];

export function formatNumber(v, maxDigits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  const abs = Math.abs(v);
  const digits = abs >= 100 ? Math.min(maxDigits, 1) : maxDigits;
  if (Math.round(abs * 10 ** digits) === 0) v = 0;     // never show "-0"
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(v);
}

export function formatValue(v, unit = '', maxDigits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  return `${formatNumber(v, maxDigits)}${unit ? ' ' + unit : ''}`;
}

/** Units as shown to people: m3 becomes m³. */
export function prettyUnit(unit) {
  return unit === 'm3' ? 'm³' : unit === '/m3' ? '/m³' : unit || '';
}

export function formatPower(watts) {
  if (watts === null || watts === undefined || Number.isNaN(watts)) return '–';
  return Math.abs(watts) >= 1000 ? `${formatNumber(watts / 1000, 2)} kW` : `${formatNumber(watts, 0)} W`;
}

export function formatCost(v, currency = '') {
  if (v === null || v === undefined || Number.isNaN(v)) return '';
  if (/^[A-Z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(v);
    } catch (_) { /* fall through */ }
  }
  return `${formatNumber(v, 2)}${currency ? ' ' + currency : ''}`;
}

export function formatPct(v) {
  return v === null || v === undefined ? '–' : `${formatNumber(v, 0)}%`;
}

function startOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

/** Range for `date` in `mode`. day: that day; week: 7 days ending on date; month: calendar month. */
export function computeRange(date, mode) {
  const day = startOfDay(date);
  let start;
  let end;
  if (mode === 'week') {
    start = new Date(day);
    start.setDate(start.getDate() - 6);
    end = new Date(day);
    end.setDate(end.getDate() + 1);
  } else if (mode === 'month') {
    start = new Date(day.getFullYear(), day.getMonth(), 1);
    end = new Date(day.getFullYear(), day.getMonth() + 1, 1);
  } else {
    start = day;
    end = new Date(day);
    end.setDate(end.getDate() + 1);
  }
  return { mode, start: start.getTime(), end: end.getTime() };
}

export function shiftDate(date, mode, dir) {
  const d = new Date(date);
  if (mode === 'month') d.setMonth(d.getMonth() + dir);
  else d.setDate(d.getDate() + dir * (mode === 'week' ? 7 : 1));
  return d;
}

export function rangeLabel(date, mode, locale) {
  const r = computeRange(date, mode);
  const fmt = (ms, o) => new Date(ms).toLocaleDateString(locale, o);
  if (mode === 'month') return fmt(r.start, { month: 'long', year: 'numeric' });
  if (mode === 'week') {
    return `${fmt(r.start, { month: 'short', day: 'numeric' })} – ${fmt(r.end - 1, { month: 'short', day: 'numeric' })}`;
  }
  return fmt(r.start, { month: 'short', day: 'numeric', year: 'numeric' });
}

/** True when the range contains `now` (the "Now" button is then disabled). */
export function isCurrent(date, mode, now = new Date()) {
  const r = computeRange(date, mode);
  return now.getTime() >= r.start && now.getTime() < r.end;
}

export function bucketLabel(ts, mode, locale) {
  const d = new Date(ts);
  if (mode === 'day') return d.toLocaleTimeString(locale, { hour: 'numeric' });
  return d.toLocaleDateString(locale, { month: 'short', day: 'numeric' });
}
