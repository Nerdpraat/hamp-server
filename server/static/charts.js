// ECharts helpers: shared theme, lifecycle tracking and common bar chart option.
import { bucketLabel, formatValue } from './format.js';

export const TEXT = '#9b9b9b';
export const GRID_LINE = '#2a2a2a';
export const PALETTE = ['#3f51b5', '#7e57c2', '#c9a227', '#26a69a', '#ef5350', '#78909c', '#ec407a', '#8d6e63'];

const live = new Set();
const observers = new Map();

export function createChart(el) {
  const chart = window.echarts.init(el, null, { renderer: 'canvas' });
  live.add(chart);
  // Cards are added to the grid one by one, so a chart is created before the final column widths
  // are known. Follow the container so it always matches its card.
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(() => { if (!chart.isDisposed()) chart.resize(); });
    ro.observe(el);
    observers.set(chart, ro);
  }
  return chart;
}

export function disposeAll() {
  for (const ro of observers.values()) ro.disconnect();
  observers.clear();
  for (const c of live) c.dispose();
  live.clear();
}

/** Legend page and hidden series of every live chart, in creation order (null = no legend). */
export function snapshotLegends() {
  return [...live].map((chart) => {
    const legend = chart.getOption()?.legend?.[0];
    if (!legend) return null;
    const hidden = Object.entries(legend.selected || {}).filter(([, on]) => on === false).map(([n]) => n);
    return { hidden, scroll: legend.scrollDataIndex || 0 };
  });
}

/** Re-apply a snapshot to freshly created charts (same view, so the same creation order). */
export function restoreLegends(snapshot) {
  [...live].forEach((chart, i) => {
    const saved = snapshot[i];
    if (!saved) return;
    for (const name of saved.hidden) chart.dispatchAction({ type: 'legendUnSelect', name });
    if (saved.scroll) chart.dispatchAction({ type: 'legendScroll', scrollDataIndex: saved.scroll });
  });
}

window.addEventListener('resize', () => { for (const c of live) c.resize(); });

export function colorFor(entity, index = 0) {
  return entity.color || PALETTE[index % PALETTE.length];
}

const axisCommon = {
  axisLabel: { color: TEXT },
  axisLine: { lineStyle: { color: '#444' } },
  axisTick: { show: false },
};

/** seriesList: [{name, color, data, stack?}] aligned with `buckets`. */
export function barOption(buckets, mode, seriesList, unit) {
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 8, right: 12, top: 30, bottom: 44, containLabel: true },
    legend: { bottom: 0, type: 'scroll', textStyle: { color: TEXT }, icon: 'roundRect' },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      valueFormatter: (v) => formatValue(v, unit),
    },
    xAxis: {
      type: 'category', data: buckets.map((b) => bucketLabel(b, mode)), ...axisCommon,
      axisLabel: { color: TEXT, interval: mode === 'day' ? 3 : 'auto' },
    },
    yAxis: {
      type: 'value', name: unit, nameTextStyle: { color: TEXT, align: 'left' },
      axisLabel: { color: TEXT }, splitLine: { lineStyle: { color: GRID_LINE } },
    },
    series: seriesList.map((s) => ({
      type: 'bar', name: s.name, stack: s.stack, data: s.data,
      itemStyle: { color: s.color, opacity: 0.85, borderRadius: 2 }, barMaxWidth: 28,
    })),
  };
}

export function lineOption(seriesList, unit, formatter, window) {
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 8, right: 12, top: 30, bottom: 44, containLabel: true },
    legend: { bottom: 0, type: 'scroll', textStyle: { color: TEXT }, icon: 'roundRect' },
    tooltip: { trigger: 'axis', valueFormatter: formatter || ((v) => formatValue(v, unit)) },
    xAxis: { type: 'time', ...axisCommon, ...(window ? { min: window.min, max: window.max } : {}) },
    yAxis: {
      type: 'value', name: unit, nameTextStyle: { color: TEXT, align: 'left' },
      axisLabel: { color: TEXT }, splitLine: { lineStyle: { color: GRID_LINE } },
    },
    series: seriesList.map((s) => ({
      type: 'line', name: s.name, data: s.data, showSymbol: false, smooth: false,
      lineStyle: { width: 1.5, color: s.color }, itemStyle: { color: s.color },
      areaStyle: s.area ? { color: s.color, opacity: 0.25 } : undefined,
    })),
  };
}
