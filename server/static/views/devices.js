import { deviceInstanceCard, h } from '../cards.js';
import { genericInstances } from '../transform.js';

export function render(el, d) {
  const instances = genericInstances(d.instances);
  if (!instances.length) {
    el.append(h('div', { class: 'empty' },
      'No devices to show here. Devices without a dedicated dashboard, such as a heat pump or a water meter, ' +
      'appear on this tab as soon as the site sends them.'));
    return;
  }
  const grid = h('div', { class: 'grid devices' });
  el.append(grid);
  for (const inst of instances) deviceInstanceCard(grid, d, inst);
}
