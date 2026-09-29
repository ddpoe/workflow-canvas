/**
 * A node draws one run status at a time
 * (canvas-ui case `catalog.builder-node-run-status`).
 *
 * `CustomNode.svelte` mounted once per `RunStatus`, with the status in the
 * node's data as the run bridge in `machines/root.ts` writes it. Each
 * status draws its own label and its own icon; the mixed state adds the
 * tally badge; nothing else about the node changes with the status.
 *
 * Border colors are deliberately not asserted — jsdom lays nothing out,
 * and screenshot parity covers them.
 *
 * No other suite covers this: the Playwright specs reach a node only through
 * `[data-id]` and prove nothing about what it draws.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import CustomNode from '../CustomNode.svelte';
import { samples } from '../stores';
import type { CanvasNodeData, RunStatus, RunTally } from '../../shared/types';

// `Handle` only works inside a mounted SvelteFlow — it reads the node id,
// the connectable flag and the flow store out of context. Stub it so the
// node component can be mounted on its own; what it draws for a run status
// is its own markup, and edge connecting is the Playwright suite's.
vi.mock('@xyflow/svelte', async () => ({
  Handle: (await import('./HandleStub.svelte')).default,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
}));

afterEach(() => {
  cleanup();
  samples.set([]);
});

function methodData(runStatus: RunStatus, runTally?: RunTally): CanvasNodeData {
  return {
    label: 'my_method',
    method: 'my_method',
    module: 'mod_a',
    color: '#2ecc71',
    inputs: [{ name: 'data', type: 'csv' }],
    outputs: [{ name: 'output', type: 'csv' }],
    params: [],
    paramValues: {},
    // The chip strip is drawn from variants — held constant across the
    // table so a status change is the only variable.
    variants: { thresh: { v1: 0.3 } },
    runStatus,
    runTally,
    expanded: false,
    nodeType: 'method',
  } as CanvasNodeData;
}

/**
 * One row per `RunStatus`. `label` is the footer's status text; `icon` is
 * the glyph drawn beside it, `null` where the status draws none.
 */
const ROWS: Array<{ status: RunStatus; label: string | null; icon: string | null }> = [
  // Idle on an unselected node draws no status at all — the footer shows
  // the param count instead.
  { status: 'idle', label: null, icon: null },
  { status: 'pending', label: 'pending...', icon: '●' },
  { status: 'running', label: 'running...', icon: '↻' },
  { status: 'completed', label: 'completed', icon: '✓' },
  // A cache hit is a success, so it keeps the tick; its label says the run
  // was reused rather than run.
  { status: 'cached', label: 'cached', icon: '✓' },
  { status: 'failed', label: 'failed', icon: '✗' },
  // Cancelled draws its label with no glyph.
  { status: 'cancelled', label: 'cancelled', icon: null },
  { status: 'mixed', label: 'mixed', icon: '⚠' },
];

describe('CustomNode draws one run status at a time', () => {
  it.each(ROWS)('$status draws its own label and icon', ({ status, label, icon }) => {
    const { container } = render(CustomNode, {
      props: { data: methodData(status), id: 'node_1', selected: false },
    });

    const statusLabel = container.querySelector('.status-label');
    if (label === null) {
      expect(statusLabel).toBeNull();
      expect(container.querySelector('.param-count')?.textContent).toContain('0 params');
    } else {
      expect(statusLabel).not.toBeNull();
      expect(statusLabel!.textContent).toContain(label);
      if (icon === null) {
        // Only the label text — no glyph in front of it.
        expect(statusLabel!.textContent!.trim()).toBe(label);
      } else {
        expect(statusLabel!.textContent).toContain(icon);
      }
    }

    // Nothing else about the node changes with the status.
    expect(container.querySelector('.node-title')!.textContent).toBe('my_method');
    expect(container.querySelector('.slot-name')!.textContent).toBe('data');
    expect(container.querySelector('.node-chip')!.textContent).toBe('thresh:v1');
  });

  it('no two statuses draw the same label', () => {
    // Read off the mounted component, one status at a time — comparing the
    // table above against itself would prove nothing about the node.
    const drawn: string[] = [];
    for (const { status } of ROWS) {
      const { container } = render(CustomNode, {
        props: { data: methodData(status), id: 'node_1', selected: false },
      });
      const label = container.querySelector('.status-label');
      if (label) drawn.push(label.textContent!.trim());
      cleanup();
    }

    // Idle is the one status that draws no status label at all; the rest draw
    // one each, and no two of them coincide.
    expect(drawn).toHaveLength(ROWS.length - 1);
    expect(new Set(drawn).size).toBe(drawn.length);
  });

  it('the mixed state adds the tally badge of how its runs ended', () => {
    const { container } = render(CustomNode, {
      props: {
        data: methodData('mixed', { completed: 3, failed: 1 } as RunTally),
        id: 'node_1',
        selected: false,
      },
    });
    // completed / (completed + failed) — 3 of the 4 sample runs are good.
    expect(container.querySelector('.status-label')!.textContent).toContain('3/4 ✓');
  });

  it('a mixed node with no tally yet draws the bare label, not a badge', () => {
    const { container } = render(CustomNode, {
      props: { data: methodData('mixed'), id: 'node_1', selected: false },
    });
    expect(container.querySelector('.status-label')!.textContent!.trim()).toBe('⚠ mixed');
  });
});
