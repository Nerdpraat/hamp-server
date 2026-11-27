import { h, distributionCard, electricityCard, gasCard, powerSourcesCard, totalsCard } from '../cards.js';

export const needsDate = true;

export function render(el, d) {
  const grid = h('div', { class: 'grid summary' });
  el.append(grid);
  distributionCard(grid, d);
  totalsCard(grid, d);
  powerSourcesCard(grid, d);
  electricityCard(grid, d);
  if (d.entities.some((e) => e.role === 'gas')) gasCard(grid, d);
}
