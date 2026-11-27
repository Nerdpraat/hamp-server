// Loads everything a historical view needs for one site and range.
import { api } from './api.js';
import { computeRange } from './format.js';
import { counterArrays, makeBuckets } from './transform.js';

const EMPTY = { series: {} };

export async function loadHistory(site, date, mode) {
  const range = computeRange(date, mode);
  const { entities, instances } = await api.devices(site);
  const counterKeys = entities.filter((e) => e.kind === 'counter').map((e) => e.key);
  const gaugeKeys = entities.filter((e) => e.kind === 'gauge').map((e) => e.key);
  const gaugeRes = mode === 'day' ? '5m' : '1h';
  const [totals, summary, counters, gauges] = await Promise.all([
    api.totals(site, range.start, range.end),
    api.summary(site, range.start, range.end),
    counterKeys.length ? api.series(site, range.start, range.end, '1h', counterKeys) : EMPTY,
    gaugeKeys.length ? api.series(site, range.start, range.end, gaugeRes, gaugeKeys) : EMPTY,
  ]);
  const buckets = makeBuckets(range);
  return {
    site, range, mode, entities, instances, totals, summary, buckets,
    arrays: counterArrays(entities, counters.series, buckets, range.end),
    gauges: gauges.series,
    currency: totals.currency || '',
  };
}
