import {
  h, balanceCard, devicesBarCard, devicesDetailCard, distributionCard, electricityCard,
  gaugeTiles, sankeyCard, solarCard, totalsCard,
} from '../cards.js';

export const needsDate = true;

export function render(el, d) {
  const grid = h('div', { class: 'grid electricity' });
  const left = h('div', { class: 'col' });
  const right = h('div', { class: 'col' });
  grid.append(left, right);
  el.append(grid);

  electricityCard(left, d);
  solarCard(left, d);
  totalsCard(left, d);
  devicesDetailCard(left, d);
  devicesBarCard(left, d);
  sankeyCard(left, d);

  distributionCard(right, d);
  balanceCard(right, d);
  gaugeTiles(right, d);
}
