/**
 * The Builder palette's colours (SOP Canvas Colours, The Governing Rule).
 *
 * A method's slot badges carry no run state, so each takes its slot type's
 * palette colour from `slotColor`, never a colour near a protected one. The
 * system-node icons draw in the system ink token.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import Sidebar from '../Sidebar.svelte';
import { modules } from '../stores.js';
import { slotColor } from '../../shared/slotColor.js';
import { PALETTE } from '../../shared/palette.js';

vi.mock('../api.js', () => ({ refreshPalette: async () => {} }));

afterEach(() => { cleanup(); modules.set([]); });

describe('Builder palette colours', () => {
  it('a slot badge takes its slot type’s palette colour, and the system icons the system ink', async () => {
    modules.set([{
      name: 'demo', description: '',
      methods: [{
        name: 'label', module: 'demo', params: [],
        inputs: [{ name: 'data', type: 'csv' }], outputs: [{ name: 'plot', type: 'png' }],
      }],
    }] as never);
    const view = render(Sidebar);
    await fireEvent.click(view.getByText('label'));
    const badges = view.getAllByTestId('palette-slot');
    expect(badges.map(b => b.textContent)).toEqual(['data', 'plot']);
    expect(badges.map(b => b.style.getPropertyValue('--chip'))).toEqual([slotColor('csv'), slotColor('png')]);
    const palette = new Set(PALETTE.map(p => p.hex.toLowerCase()));
    for (const b of badges) expect(palette.has(b.style.getPropertyValue('--chip').toLowerCase())).toBe(true);
    for (const svg of view.container.querySelectorAll('.system-icon-small svg')) {
      expect(svg.getAttribute('stroke')).toBe('currentColor');
    }
  });
});
