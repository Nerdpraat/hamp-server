import test from 'node:test';
import assert from 'node:assert/strict';
import { gridLabel, batteryLabel, lastSeenText } from '../../static/transform.js';

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

test('lastSeenText', () => {
  assert.equal(lastSeenText(null, 1000), 'never seen');
  assert.equal(lastSeenText(990, 1000), 'last seen just now');
  assert.equal(lastSeenText(1000 - 300, 1000), 'last seen 5 min ago');
  assert.equal(lastSeenText(1000 - 7200, 1000), 'last seen 2 h ago');
  assert.equal(lastSeenText(1000 - 3 * 86400, 1000), 'last seen 3 d ago');
});
