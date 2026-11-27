import test from 'node:test';
import assert from 'node:assert/strict';
import { gridLabel, batteryLabel, connectionTitle, sumSites } from '../../static/transform.js';

test('gridLabel shows direction and magnitude', () => {
  assert.deepEqual(gridLabel(null), { sub: '', w: null });
  assert.deepEqual(gridLabel({ net_w: 400 }), { sub: 'importing', w: 400 });
  assert.deepEqual(gridLabel({ net_w: -900 }), { sub: 'exporting', w: 900 });
  assert.equal(gridLabel({ net_w: 2 }).sub, 'idle');
});

test('batteryLabel: positive power discharges, negative charges', () => {
  assert.deepEqual(batteryLabel(null), { sub: '', w: null, soc: null });
  assert.deepEqual(batteryLabel({ power_w: 300, soc: 55 }), { sub: 'discharging', w: 300, soc: 55 });
  assert.deepEqual(batteryLabel({ power_w: -1200, soc: 20 }), { sub: 'charging', w: 1200, soc: 20 });
  assert.equal(batteryLabel({ power_w: 0, soc: 90 }).sub, 'idle');
  assert.deepEqual(batteryLabel({ power_w: null, soc: 80 }), { sub: '', w: null, soc: 80 });
});

test('connectionTitle', () => {
  assert.equal(connectionTitle(true, 5), 'connected');
  assert.equal(connectionTitle(false, null), 'disconnected, never connected');
  assert.equal(connectionTitle(false, undefined), 'disconnected, never connected');
  assert.equal(connectionTitle(false, 1000), `disconnected, last connected ${new Date(1_000_000).toLocaleString()}`);
});

test('sumSites adds groups and skips missing ones', () => {
  const sites = [
    { grid: { import_w: 500, export_w: 0, net_w: 500 }, solar: { power_w: 100 }, battery: { power_w: -200, soc: 40 } },
    { grid: { import_w: 0, export_w: 900, net_w: -900 }, solar: null, battery: { power_w: null, soc: 80 } },
    { grid: null, solar: { power_w: 50 }, battery: null },
  ];
  assert.deepEqual(sumSites(sites), {
    grid: { import_w: 500, export_w: 900, net_w: -400 },
    solar: { power_w: 150 },
    battery: { power_w: -200, soc: 60 },
  });
});

test('sumSites with no data', () => {
  assert.deepEqual(sumSites([]), { grid: null, solar: null, battery: null });
  assert.deepEqual(sumSites([{ grid: null, solar: null, battery: null }]), { grid: null, solar: null, battery: null });
});
