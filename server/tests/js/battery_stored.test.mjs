import test from 'node:test';
import assert from 'node:assert/strict';
import { batteriesWithCapacity, storedEnergy, storedSeries } from '../../static/transform.js';

const bat = (id, name, key, fixed) => ({
  id, name, device: 'battery',
  sensors: [
    { id: `${id}-soc`, key, role: 'battery_soc' },
    ...(fixed === undefined ? [] : [{ id: `${id}-cap`, key: null, role: 'battery_capacity', fixed }]),
  ],
});

test('storedEnergy', () => {
  assert.equal(storedEnergy(50, 10000), 5);
  assert.equal(storedEnergy(null, 10000), null);
  assert.equal(storedEnergy(undefined, 10000), null);
  assert.equal(storedEnergy(NaN, 10000), null);
  assert.equal(storedEnergy(50, null), null);
  assert.equal(storedEnergy(50, 0), null);
  assert.equal(storedEnergy(50, -5), null);
  assert.equal(storedEnergy(0, 10000), 0);
});

test('batteriesWithCapacity reads capacity from fixed and skips incomplete instances', () => {
  const noKey = { id: 'c', name: 'C', sensors: [
    { role: 'battery_soc', key: null }, { role: 'battery_capacity', key: null, fixed: 5 }] };
  const zero = bat('d', 'D', 'd.soc', 0);
  const list = batteriesWithCapacity([bat('a', 'A', 'a.soc', 10), bat('b', 'B', 'b.soc'), noKey, zero]);
  assert.deepEqual(list, [{ id: 'a', name: 'A', socKey: 'a.soc', capacity: 10 }]);
  assert.deepEqual(batteriesWithCapacity(undefined), []);
});

test('storedSeries per battery, keeps null points, omits batteries without capacity or SOC series', () => {
  const instances = [bat('a', 'Battery 1', 'a.soc', 10000), bat('b', 'Battery 2', 'b.soc', 20000),
    bat('c', 'Battery 3', 'c.soc'), bat('d', 'Battery 4', 'd.soc', 5)];
  const gauges = {
    'a.soc': { points: [[1, 50, 40, 60], [2, null, null, null], [3, 100, 100, 100]] },
    'b.soc': { points: [[1, 25, 25, 25]] },
    'c.soc': { points: [[1, 50, 50, 50]] },
  };
  assert.deepEqual(storedSeries(instances, gauges), [
    { name: 'Battery 1', data: [[1, 5], [2, null], [3, 10]] },
    { name: 'Battery 2', data: [[1, 5]] },
  ]);
  assert.deepEqual(storedSeries([], {}), []);
});
