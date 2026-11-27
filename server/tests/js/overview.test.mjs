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
  assert.deepEqual(batteryLabel(null), { sub: '', w: null, soc: null, stored: null });
  assert.deepEqual(batteryLabel({ power_w: 300, soc: 55, stored: null }), { sub: 'discharging', w: 300, soc: 55, stored: null });
  assert.deepEqual(batteryLabel({ power_w: -1200, soc: 20 }), { sub: 'charging', w: 1200, soc: 20, stored: null });
  assert.equal(batteryLabel({ power_w: 0, soc: 90 }).sub, 'idle');
  assert.deepEqual(batteryLabel({ power_w: null, soc: 80, stored: null }), { sub: '', w: null, soc: 80, stored: null });
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
    { grid: { import_w: 0, export_w: 900, net_w: -900 }, solar: null, battery: { power_w: null, soc: 80, stored: null } },
    { grid: null, solar: { power_w: 50 }, battery: null },
  ];
  assert.deepEqual(sumSites(sites), {
    grid: { import_w: 500, export_w: 900, net_w: -400 },
    solar: { power_w: 150 },
    battery: { power_w: -200, soc: 60, soc_n: 2, stored_kwh: null },
  });
});

test('sumSites with no data', () => {
  assert.deepEqual(sumSites([]), { grid: null, solar: null, battery: null });
  assert.deepEqual(sumSites([{ grid: null, solar: null, battery: null }]), { grid: null, solar: null, battery: null });
});

test('batteryLabel returns stored energy with and without power', () => {
  assert.deepEqual(batteryLabel({ power_w: 300, soc: 55, stored_kwh: 12.4 }),
    { sub: 'discharging', w: 300, soc: 55, stored: 12.4 });
  assert.deepEqual(batteryLabel({ power_w: null, soc: 55, stored_kwh: 5.5 }),
    { sub: '', w: null, soc: 55, stored: 5.5 });
  assert.equal(batteryLabel({ power_w: 0, soc: 55, stored_kwh: null }).stored, null);
  assert.equal(batteryLabel({ power_w: 10, soc: 55 }).stored, null);
});

test('sumSites weights soc by soc_n and sums stored_kwh', () => {
  const sites = [
    { grid: null, solar: null, battery: { power_w: 1, soc: 40, soc_n: 1, stored_kwh: 4 } },
    { grid: null, solar: null, battery: { power_w: 2, soc: 80, soc_n: 3, stored_kwh: 20 } },
    { grid: null, solar: null, battery: { power_w: null, soc: 10, soc_n: 1, stored_kwh: null } },
    { grid: null, solar: null, battery: null },
  ];
  const b = sumSites(sites).battery;
  assert.equal(b.soc, (40 + 240 + 10) / 5);
  assert.equal(b.soc_n, 5);
  assert.equal(b.stored_kwh, 24);
  assert.equal(b.power_w, 3);
});

test('sumSites: stored_kwh null when no site has it; soc_n defaults to 1 per site', () => {
  const b = sumSites([{ battery: { power_w: 1, soc: 30 } }, { battery: { power_w: 1, soc: 50, soc_n: null } }]).battery;
  assert.equal(b.stored_kwh, null);
  assert.equal(b.soc, 40);
  assert.equal(b.soc_n, 2);
});
