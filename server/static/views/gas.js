import { h, gasCard } from '../cards.js';

export const needsDate = true;

export function render(el, d) {
  const grid = h('div', { class: 'grid gas' });
  el.append(grid);
  gasCard(grid, d);
}
