import test from 'node:test';
import assert from 'node:assert/strict';
import {
  computeRange, shiftDate, isCurrent, formatNumber, formatPower, formatValue, formatCost, formatPct, prettyUnit,
} from '../../static/format.js';
import {
  makeBuckets, bucketIndex, assign, counterArrays, sumRole, homeSeries, devicesDetail,
  sankeyData, totalsRows, netGridPower, unitClass, genericInstances, sensorSummary,
} from '../../static/transform.js';

test('day range has 24 hourly buckets and spans one day', () => {
  const r = computeRange(new Date(2026, 10, 27, 15, 30), 'day');
  assert.equal(new Date(r.start).getHours(), 0);
  const b = makeBuckets(r);
  assert.ok(b.length === 24 || b.length === 23 || b.length === 25); // DST days
  assert.equal(b[0], r.start);
});

test('week range is 7 daily buckets ending on the date', () => {
  const r = computeRange(new Date(2026, 10, 27), 'week');
  const b = makeBuckets(r);
  assert.equal(b.length, 7);
  assert.equal(new Date(b[6]).getDate(), 27);
});

test('month range covers the calendar month', () => {
  const r = computeRange(new Date(2026, 1, 10), 'month');
  assert.equal(makeBuckets(r).length, 28);
});

test('shiftDate steps by mode', () => {
  const d = new Date(2026, 10, 27);
  assert.equal(shiftDate(d, 'day', -1).getDate(), 26);
  assert.equal(shiftDate(d, 'week', 1).getDate(), 4);
  assert.equal(shiftDate(d, 'month', 1).getMonth(), 11);
});

test('isCurrent', () => {
  const now = new Date(2026, 10, 27, 12);
  assert.ok(isCurrent(new Date(2026, 10, 27), 'day', now));
  assert.ok(!isCurrent(new Date(2026, 10, 26), 'day', now));
  assert.ok(isCurrent(new Date(2026, 10, 27), 'week', now));
  assert.ok(!isCurrent(new Date(2026, 10, 20), 'week', now));
});

test('bucketIndex edges', () => {
  const r = computeRange(new Date(2026, 10, 27), 'day');
  const b = makeBuckets(r);
  assert.equal(bucketIndex(b, r.start - 1, r.end), -1);
  assert.equal(bucketIndex(b, r.end, r.end), -1);
  assert.equal(bucketIndex(b, r.start + 3600_000 * 5 + 1000, r.end), 5);
});

test('assign sums counters, averages gauges, leaves gaps null', () => {
  const r = computeRange(new Date(2026, 10, 27), 'day');
  const b = makeBuckets(r);
  const t = b[2];
  const counter = assign([[t, 1], [t + 1000, 2]], b, r.end, 'counter');
  assert.equal(counter[2], 3);
  assert.equal(counter[3], null);
  const gauge = assign([[t, 10], [t + 1000, 20]], b, r.end, 'gauge');
  assert.equal(gauge[2], 15);
});

const ents = [
  { key: 'imp', role: 'grid_import', kind: 'counter', name: 'Import' },
  { key: 'exp', role: 'grid_export', kind: 'counter', name: 'Export' },
  { key: 'sol', role: 'solar_energy', kind: 'counter', name: 'Solar' },
  { key: 'car', role: 'device_energy', kind: 'counter', name: 'Car' },
  { key: 't', role: null, kind: 'gauge', name: 'T' },
];

test('counterArrays skips gauges; homeSeries and untracked', () => {
  const r = computeRange(new Date(2026, 10, 27), 'day');
  const b = makeBuckets(r);
  const series = {
    imp: { points: [[b[0], 5]] }, exp: { points: [[b[0], 1]] },
    sol: { points: [[b[0], 2]] }, car: { points: [[b[0], 4]] }, t: { points: [[b[0], 20]] },
  };
  const arrays = counterArrays(ents, series, b, r.end);
  assert.ok(!('t' in arrays));
  assert.equal(homeSeries(ents, arrays, b.length)[0], 5 + 2 - 1);
  assert.equal(homeSeries(ents, arrays, b.length)[1], null);
  const d = devicesDetail(ents, arrays, b.length);
  assert.equal(d.untracked[0], 2);            // 6 home - 4 car
  assert.equal(d.untrackedTotal, 2);
  const over = devicesDetail(ents, { ...arrays, car: [10, ...arrays.car.slice(1)] }, b.length);
  assert.equal(over.over[0], -4);
  assert.equal(over.untracked[0], 0);
});

test('sumRole returns a null array when the role is absent and sums several instances', () => {
  assert.deepEqual(sumRole(ents, {}, 'gas', 3), [null, null, null]);
  const two = [
    { key: 'a', role: 'solar_energy', kind: 'counter' }, { key: 'b', role: 'solar_energy', kind: 'counter' },
  ];
  assert.deepEqual(sumRole(two, { a: [1, null], b: [2, null] }, 'solar_energy', 2), [3, null]);
});

test('sankeyData drops zero links and keeps names unique', () => {
  const summary = { flows: {
    solar_to_home: 3, solar_to_battery: 0, solar_to_grid: 1, grid_to_home: 10,
    grid_to_battery: 0, battery_to_home: 2, battery_to_grid: 0 } };
  const s = sankeyData(summary, [{ name: 'Car', total: 8 }], 2);
  const names = s.nodes.map((n) => n.name);
  assert.equal(new Set(names).size, names.length);
  assert.ok(!names.includes('Battery (in)'));
  assert.ok(s.links.some((l) => l.source === 'Home' && l.target === 'Untracked consumption'));
  assert.ok(s.links.every((l) => l.value > 0));
});

test('totalsRows order and device exclusion', () => {
  const totals = {
    sources: [
      { key: 'a', group: 'grid' }, { key: 'b', group: 'solar' }, { key: 'c', group: 'device' },
    ],
    groups: { grid: { energy: 5, unit: 'kWh', cost: 1 }, solar: { energy: 2, unit: 'kWh', cost: null } },
  };
  const rows = totalsRows(totals);
  assert.deepEqual(rows.map((r) => r.group), ['solar', 'grid']);
});

test('formatters', () => {
  assert.equal(formatPower(500), '500 W');
  assert.match(formatPower(1500), /1.5.*kW/);
  assert.equal(formatValue(null, 'kWh'), '–');
  assert.match(formatValue(6.654, 'kWh'), /6.65 kWh/);
  assert.equal(formatCost(null, 'EUR'), '');
  assert.match(formatCost(1.5, 'EUR'), /1.50/);
  assert.equal(formatPct(null), '–');
  assert.match(formatPct(42.4), /42%/);
});

test('netGridPower is import minus export, summed over meters, sorted by time', () => {
  const e = [
    { key: 'i1', role: 'grid_power_import' }, { key: 'e1', role: 'grid_power_export' },
    { key: 'i2', role: 'grid_power_import' }, { key: 'x', role: 'solar_power' },
  ];
  const g = {
    i1: { points: [[2000, 500, 0, 0], [1000, 100, 0, 0]] },
    e1: { points: [[1000, 30, 0, 0], [3000, 200, 0, 0]] },
    i2: { points: [[1000, 10, 0, 0], [2000, null, 0, 0]] },
    x: { points: [[1000, 9999, 0, 0]] },
  };
  assert.deepEqual(netGridPower(e, g), [[1000, 80], [2000, 500], [3000, -200]]);
  assert.deepEqual(netGridPower([], {}), []);
  assert.deepEqual(netGridPower([{ key: 'i', role: 'grid_power_import' }], {}), []);
});

test('unitClass groups gauges for the Now view', () => {
  assert.equal(unitClass({ unit: 'W' }), 'power');
  assert.equal(unitClass({ unit: 'kW' }), 'power');
  assert.equal(unitClass({ unit: '°C' }), 'temperature');
  assert.equal(unitClass({ unit: '%' }), 'level');
  assert.equal(unitClass({ unit: 'L/min' }), 'L/min');
  assert.equal(unitClass({ unit: '' }), 'other');
  assert.equal(unitClass({}), 'other');
});

test('genericInstances keeps only the instances the server flags', () => {
  const list = [{ id: 'hp', generic: true }, { id: 'bat', generic: false }, { id: 'w', generic: true }];
  assert.deepEqual(genericInstances(list).map((i) => i.id), ['hp', 'w']);
  assert.deepEqual(genericInstances(undefined), []);
});

test('sensorSummary: total for counters, latest reading for gauges', () => {
  const arrays = { 'w.consumption': [null, 0.5, 0.25, null] };
  const gauges = { 'hp.temp': { points: [[1, 30, 0, 0], [2, 31, 0, 0], [3, null, 0, 0]] }, 'hp.empty': { points: [] } };
  assert.deepEqual(sensorSummary({ key: 'w.consumption', kind: 'counter' }, arrays, gauges), { label: 'total', value: 0.75 });
  assert.deepEqual(sensorSummary({ key: 'hp.temp', kind: 'gauge' }, arrays, gauges), { label: 'latest', value: 31 });
  assert.equal(sensorSummary({ key: 'hp.empty', kind: 'gauge' }, arrays, gauges).value, null);
  assert.equal(sensorSummary({ key: 'missing', kind: 'counter' }, arrays, gauges).value, null);
  assert.equal(sensorSummary({ key: 'missing', kind: 'gauge' }, arrays, gauges).value, null);
});

test('prettyUnit', () => {
  assert.equal(prettyUnit('m3'), 'm³');
  assert.equal(prettyUnit('/m3'), '/m³');
  assert.equal(prettyUnit('kWh'), 'kWh');
  assert.equal(prettyUnit(undefined), '');
});

test('numbers that round to zero never show a minus sign', () => {
  assert.equal(formatNumber(-0), '0');
  assert.equal(formatNumber(-0.0001), '0');
  assert.equal(formatNumber(-0.004, 2), '0');
  assert.equal(formatNumber(-0.006, 2), '-0.01');
  assert.equal(formatNumber(0.004, 2), '0');
  assert.equal(formatNumber(-12.5), '-12.5');
  assert.equal(formatPower(-0), '0 W');
  assert.equal(formatValue(-0.001, 'kWh'), '0 kWh');
});
