// Pure data transforms for the charts (no DOM access, unit tested with node).

/** Bucket start timestamps (ms) for a range: hourly for a day, daily otherwise. */
export function makeBuckets(range) {
  const out = [];
  const d = new Date(range.start);
  while (d.getTime() < range.end) {
    out.push(d.getTime());
    if (range.mode === 'day') d.setHours(d.getHours() + 1);
    else d.setDate(d.getDate() + 1);
  }
  return out;
}

/** Index of the bucket containing ts, or -1 when outside the range. */
export function bucketIndex(buckets, ts, end) {
  if (!buckets.length || ts < buckets[0] || ts >= end) return -1;
  let lo = 0;
  let hi = buckets.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (buckets[mid] <= ts) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/**
 * Put server points into buckets.
 * counters: points [ts, delta] are summed; gauges: points [ts, avg, min, max] are averaged.
 * Empty buckets are null so charts show a gap rather than zero.
 */
export function assign(points, buckets, end, kind = 'counter') {
  const sums = new Array(buckets.length).fill(null);
  const counts = new Array(buckets.length).fill(0);
  for (const p of points || []) {
    const i = bucketIndex(buckets, p[0], end);
    if (i < 0 || p[1] === null || p[1] === undefined) continue;
    sums[i] = (sums[i] ?? 0) + p[1];
    counts[i] += 1;
  }
  if (kind === 'gauge') return sums.map((s, i) => (s === null ? null : s / counts[i]));
  return sums;
}

/** key -> bucket array for every counter entity present in `series`. */
export function counterArrays(entities, series, buckets, end) {
  const out = {};
  for (const e of entities) {
    if (e.kind !== 'counter') continue;
    out[e.key] = assign(series[e.key]?.points, buckets, end, 'counter');
  }
  return out;
}

const add = (a, b) => (a === null && b === null ? null : (a ?? 0) + (b ?? 0));

/** Element-wise sum over all entities with one role. Returns a null-filled array when none exist. */
export function sumRole(entities, arrays, role, length) {
  let acc = new Array(length).fill(null);
  for (const e of entities) {
    if (e.role !== role || !arrays[e.key]) continue;
    acc = acc.map((v, i) => add(v, arrays[e.key][i]));
  }
  return acc;
}

/** home = import + solar + battery discharge - export - battery charge, per bucket (null if no data). */
export function homeSeries(entities, arrays, length) {
  const t = (role) => sumRole(entities, arrays, role, length);
  const imp = t('grid_import');
  const sol = t('solar_energy');
  const bout = t('battery_discharge');
  const exp = t('grid_export');
  const bin = t('battery_charge');
  return imp.map((_, i) => {
    const parts = [imp[i], sol[i], bout[i], exp[i], bin[i]];
    if (parts.every((p) => p === null)) return null;
    return (imp[i] ?? 0) + (sol[i] ?? 0) + (bout[i] ?? 0) - (exp[i] ?? 0) - (bin[i] ?? 0);
  });
}

/** Device series plus untracked (positive remainder) and over-reported (negative remainder). */
export function devicesDetail(entities, arrays, length) {
  const devices = entities
    .filter((e) => e.role === 'device_energy' && arrays[e.key])
    .map((e) => ({ entity: e, values: arrays[e.key] }));
  const home = homeSeries(entities, arrays, length);
  const untracked = new Array(length).fill(null);
  const over = new Array(length).fill(null);
  for (let i = 0; i < length; i++) {
    if (home[i] === null) continue;
    const tracked = devices.reduce((s, d) => s + (d.values[i] ?? 0), 0);
    const diff = home[i] - tracked;
    untracked[i] = Math.max(diff, 0);
    over[i] = Math.min(diff, 0);
  }
  const sum = (a) => a.reduce((s, v) => s + (v ?? 0), 0);
  return {
    devices, untracked, over,
    untrackedTotal: sum(untracked), overTotal: sum(over),
  };
}

/**
 * Sankey nodes/links like the HA energy flow: sources -> home -> sinks.
 * `flows` and `values` come from the server summary, `devices` are
 * [{name, total}] and `untracked` the unaccounted remainder (>= 0).
 */
export function sankeyData(summary, devices, untracked, homeName = 'Home') {
  const f = summary.flows;
  const nodes = new Set([homeName]);
  const links = [];
  const link = (source, target, value) => {
    if (!(value > 0.0005)) return;
    nodes.add(source);
    nodes.add(target);
    links.push({ source, target, value: Number(value.toFixed(4)) });
  };
  link('Solar', homeName, f.solar_to_home);
  link('Solar', 'Battery (in)', f.solar_to_battery);
  link('Solar', 'Grid (out)', f.solar_to_grid);
  link('Grid (in)', homeName, f.grid_to_home);
  link('Grid (in)', 'Battery (in)', f.grid_to_battery);
  link('Battery (out)', homeName, f.battery_to_home);
  link('Battery (out)', 'Grid (out)', f.battery_to_grid);
  for (const d of devices) link(homeName, d.name, d.total);
  link(homeName, 'Untracked consumption', untracked);
  return { nodes: [...nodes].map((name) => ({ name })), links };
}

/** Group totals sources into the HA "Totals" table order. */
export function totalsRows(totals, order = ['solar', 'battery', 'grid', 'gas']) {
  const groups = {};
  for (const s of totals.sources) {
    if (s.group === 'device') continue;
    (groups[s.group] ||= []).push(s);
  }
  const names = [...order.filter((g) => groups[g]), ...Object.keys(groups).filter((g) => !order.includes(g))];
  return names.map((g) => ({
    group: g,
    sources: groups[g],
    energy: totals.groups[g]?.energy ?? 0,
    unit: totals.groups[g]?.unit ?? '',
    cost: totals.groups[g]?.cost ?? null,
  }));
}

/**
 * Net grid power over time in W (import positive, export negative), summed over all electricity
 * meters. `gauges` is the series payload of the gauge sensors: key -> {points: [[ts, avg, min, max]]}.
 */
export function netGridPower(entities, gauges) {
  const net = new Map();
  const add = (role, sign) => {
    for (const e of entities) {
      if (e.role !== role) continue;
      for (const p of gauges[e.key]?.points || []) {
        if (p[1] === null || p[1] === undefined) continue;
        net.set(p[0], (net.get(p[0]) ?? 0) + sign * p[1]);
      }
    }
  };
  add('grid_power_import', 1);
  add('grid_power_export', -1);
  return [...net.entries()].sort((a, b) => a[0] - b[0]);
}

/** Group gauges by what they measure, for the Now view: power, temperature, level, or the unit. */
export function unitClass(entity) {
  const u = entity.unit || '';
  if (u === 'W' || u === 'kW') return 'power';
  if (u === '°C' || u === '°F') return 'temperature';
  if (u === '%') return 'level';
  return u || 'other';
}

/** Instances the generic Devices tab shows: those with a sensor that has no dedicated dashboard. */
export function genericInstances(instances) {
  return (instances || []).filter((i) => i.generic);
}

/**
 * One headline number for a sensor over the selected range.
 * Counters: the total consumed in the range. Gauges: the latest reading.
 * `arrays` are the bucketed counter series (key -> array), `gauges` the gauge series (key -> {points}).
 */
export function sensorSummary(sensor, arrays, gauges) {
  if (sensor.kind === 'counter') {
    const values = (arrays[sensor.key] || []).filter((v) => v !== null && v !== undefined);
    return { label: 'total', value: values.length ? values.reduce((a, b) => a + b, 0) : null };
  }
  const points = (gauges[sensor.key]?.points || []).filter((p) => p[1] !== null && p[1] !== undefined);
  return { label: 'latest', value: points.length ? points.at(-1)[1] : null };
}

// ---------------------------------------------------------------- overview
const NEAR_ZERO_W = 5;   // below this a flow is shown as idle

/** Label for the net grid power (import minus export): { sub, w }. `w` is null without data. */
export function gridLabel(grid) {
  if (!grid) return { sub: '', w: null };
  const w = grid.net_w;
  if (Math.abs(w) < NEAR_ZERO_W) return { sub: 'idle', w: Math.abs(w) };
  return { sub: w > 0 ? 'importing' : 'exporting', w: Math.abs(w) };
}

/** Label for the battery (power is positive while discharging): { sub, w, soc, stored }. */
export function batteryLabel(battery) {
  if (!battery) return { sub: '', w: null, soc: null, stored: null };
  const p = battery.power_w;
  const soc = battery.soc ?? null;
  const stored = battery.stored_kwh ?? null;
  if (p === null || p === undefined) return { sub: '', w: null, soc, stored };
  if (Math.abs(p) < NEAR_ZERO_W) return { sub: 'idle', w: Math.abs(p), soc, stored };
  return { sub: p > 0 ? 'discharging' : 'charging', w: Math.abs(p), soc, stored };
}

/** Hover text of the connection dot. `lastSeen` is a unix timestamp in seconds or null. */
export function connectionTitle(online, lastSeen) {
  if (online) return 'connected';
  if (lastSeen === null || lastSeen === undefined) return 'disconnected, never connected';
  return `disconnected, last connected ${new Date(lastSeen * 1000).toLocaleString()}`;
}

/** Sum of all sites in the shape of one site entry. A group is null when no site has data for it. */
export function sumSites(sites) {
  const sum = (xs) => xs.reduce((a, b) => a + b, 0);
  const grids = sites.map((s) => s.grid).filter(Boolean);
  const solars = sites.map((s) => s.solar?.power_w).filter((v) => v !== null && v !== undefined);
  const bats = sites.map((s) => s.battery).filter(Boolean);
  const powers = bats.map((b) => b.power_w).filter((v) => v !== null && v !== undefined);
  const socs = bats.filter((b) => b.soc !== null && b.soc !== undefined)
    .map((b) => ({ soc: b.soc, n: b.soc_n ?? 1 }));
  const socN = sum(socs.map((x) => x.n));
  const stored = bats.map((b) => b.stored_kwh).filter((v) => v !== null && v !== undefined);
  return {
    grid: grids.length ? {
      import_w: sum(grids.map((g) => g.import_w)), export_w: sum(grids.map((g) => g.export_w)),
      net_w: sum(grids.map((g) => g.net_w)),
    } : null,
    solar: solars.length ? { power_w: sum(solars) } : null,
    battery: bats.length ? {
      power_w: powers.length ? sum(powers) : null,
      soc: socN > 0 ? sum(socs.map((x) => x.soc * x.n)) / socN : null,
      soc_n: socN,
      stored_kwh: stored.length ? sum(stored) : null,
    } : null,
  };
}

/** Stored energy in kWh from a state of charge in % and a capacity in Wh; null when unknown or invalid. */
export function storedEnergy(soc, capacityWh) {
  const ok = (v) => v !== null && v !== undefined && !Number.isNaN(v);
  if (!ok(soc) || !ok(capacityWh) || capacityWh <= 0) return null;
  return ((soc / 100) * capacityWh) / 1000;
}

/**
 * Batteries that have both a state-of-charge entity and a fixed capacity (in Wh) > 0:
 * [{id, name, socKey, capacity}]. Instances lacking either are omitted.
 */
export function batteriesWithCapacity(instances) {
  const out = [];
  for (const inst of instances || []) {
    const sensors = inst.sensors || [];
    const soc = sensors.find((s) => s.role === 'battery_soc' && s.key);
    const cap = sensors.find((s) => s.role === 'battery_capacity' && Number(s.fixed) > 0);
    if (soc && cap) out.push({ id: inst.id, name: inst.name, socKey: soc.key, capacity: Number(cap.fixed) });
  }
  return out;
}

/**
 * Stored energy over time per battery: [{name, data: [[ts, kWh|null], ...]}].
 * `gauges` is key -> {points: [[ts, avg, min, max]]}. Null points stay null (gaps in the chart).
 * A battery whose SOC series is absent from `gauges` is omitted.
 */
export function storedSeries(instances, gauges) {
  const out = [];
  for (const b of batteriesWithCapacity(instances)) {
    const points = gauges?.[b.socKey]?.points;
    if (!points) continue;
    out.push({ name: b.name, data: points.map((p) => [p[0], storedEnergy(p[1], b.capacity)]) });
  }
  return out;
}
