/**
 * A refused node draws a red border (SOP canvas-colours: `--status-failed`
 * is the border of a blocked or invalid step).
 *
 * `CustomNode.svelte` mounted over the node data the canvas holds after a
 * Validate or Run the server refused: `nodeRefusals.ts::showRefusals` puts
 * each refusal on the node it names, `clearRefusals` takes them off at the
 * next Validate or Run. While a node carries a refusal its border is the
 * failed colour, over whatever colour an earlier run left; a draft-mode node
 * dashes in it; the selection glow takes it.
 *
 * The card shows one status, in its footer: ⚠ refused (with the count and
 * each refusal on hover) over everything else, then an invalid draft's
 * ⚠ invalid, then how the last run ended. The title row holds the name.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { render, cleanup } from '@testing-library/svelte';
import CustomNode from '../CustomNode.svelte';
import { methodNode } from '../nodeData';
import { nodes, edges } from '../stores';
import { clearRefusals, refusalsByNode, showRefusals } from '../nodeRefusals';
import type { CanvasNodeData, DraftDef, MethodDef, RunStatus } from '../../shared/types';

// `Handle` needs a mounted SvelteFlow; see customNode.runStatus.test.ts.
vi.mock('@xyflow/svelte', async () => ({
  Handle: (await import('./HandleStub.svelte')).default,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
}));

const SLOTS = {
  inputs: [{ name: 'data', type: 'csv' }],
  outputs: [{ name: 'output', type: 'csv' }],
  params: [],
};
const DRAFT: DraftDef = {
  module: 'mod_a', method: 'label', kind: 'version', folder: 'methods/label',
  valid: true, reasons: [], env: 'container:draft-env', ...SLOTS,
};
const LABEL: MethodDef = {
  name: 'label', module: 'mod_a', ...SLOTS,
  versions: [{ label: 1, identity: 'id-v1', newest: true, env: 'container:env', ...SLOTS }],
  draft: DRAFT,
};
const PLOT: MethodDef = {
  name: 'plot', module: 'mod_a', ...SLOTS,
  versions: [{ label: 1, identity: 'id-p1', newest: true, env: 'container:env', ...SLOTS }],
};
const REFUSAL = "Node 'plain' (mod_a.plot): parameter 'label_column' is not declared by v1; remove it";
const DRAFT_REFUSAL = "Node 'd' (mod_a.label draft): its draft run is still running";

beforeEach(() => {
  nodes.set([
    methodNode('d', { x: 0, y: 0 }, LABEL, { version: 'draft' }),
    methodNode('plain', { x: 0, y: 200 }, PLOT, {}),
  ]);
  edges.set([]);
});
afterEach(() => { cleanup(); nodes.set([]); edges.set([]); });

/** Mount node `id` from the data the canvas holds now, with a run status. */
function mount(id: string, status: RunStatus = 'idle', selected = false): HTMLElement {
  const data: CanvasNodeData = { ...get(nodes).find(n => n.id === id)!.data, runStatus: status };
  const { container } = render(CustomNode, { props: { data, id, selected } });
  return container.querySelector('.custom-node') as HTMLElement;
}

describe('a refused node draws the failed border until the next Validate or Run', () => {
  it('the refusal overrides an earlier run\'s border and clears with the refusals', () => {
    showRefusals(refusalsByNode([REFUSAL]));
    const refused = mount('plain', 'completed');
    expect(refused.getAttribute('style')).toContain('border: 2px solid var(--status-failed)');
    cleanup();

    clearRefusals();
    const cleared = mount('plain', 'completed');
    expect(cleared.getAttribute('style')).toContain('border: 2px solid var(--status-ok)');
    expect(cleared.querySelector('[data-testid="node-refused"]')).toBeNull();
  });

  it('a refused node that never ran takes the failed border, not the idle one', () => {
    showRefusals(refusalsByNode([REFUSAL]));
    expect(mount('plain').getAttribute('style')).toContain('border: 2px solid var(--status-failed)');
  });

  it('a refused draft node dashes red; cleared, it dashes neutral again', () => {
    showRefusals(refusalsByNode([], [{ nodeId: 'd', message: DRAFT_REFUSAL }]));
    const refused = mount('d');
    expect(refused.classList.contains('dash-2')).toBe(true);
    expect(refused.style.getPropertyValue('--dash-color')).toBe('var(--status-failed)');
    cleanup();

    clearRefusals();
    expect(mount('d').style.getPropertyValue('--dash-color')).toBe('var(--dash-neutral)');
  });

  it('selecting a refused node glows in the failed colour, never the neutral glow', () => {
    showRefusals(refusalsByNode([REFUSAL], [{ nodeId: 'd', message: DRAFT_REFUSAL }]));
    for (const id of ['plain', 'd']) {
      const el = mount(id, 'idle', true);
      expect(el.classList.contains('sel-glow')).toBe(true);
      expect(el.classList.contains('glow-neutral')).toBe(false);
      expect(el.style.getPropertyValue('--glow-c')).toBe('var(--status-failed)');
      cleanup();
    }
  });
});

const SECOND = "Node 'plain' (mod_a.plot): declares input 'mask', which nothing is connected to";
const INVALID_LABEL: MethodDef = {
  ...LABEL, draft: { ...DRAFT, valid: false, reasons: ['env wfc-qc-env is not registered'] },
};

/** The footer of node `id` as mounted now. */
function footer(el: HTMLElement): HTMLElement {
  return el.querySelector('.node-footer') as HTMLElement;
}

describe('a node card shows one status, in its footer: refused, then invalid, then the run outcome', () => {
  it('refused replaces an earlier completed run, with each refusal on hover', () => {
    showRefusals(refusalsByNode([REFUSAL, SECOND]));
    const foot = footer(mount('plain', 'completed'));
    const status = foot.querySelector('[data-testid="node-refused"]') as HTMLElement;
    expect(status.textContent!.trim()).toBe('⚠ refused · 2 issues');
    expect(status.title).toBe(`${REFUSAL}\n${SECOND}`);
    expect(status.getAttribute('style')).toContain('var(--status-failed-ink)');
    expect(foot.textContent).not.toContain('completed');
    expect(foot.querySelector('.param-count')).toBeNull();
  });

  it('refused on a draft-mode node replaces the run outcome and the latest-kept text, and wins over invalid', () => {
    nodes.set([methodNode('d', { x: 0, y: 0 }, INVALID_LABEL, { version: 'draft' })]);
    showRefusals(refusalsByNode([], [{ nodeId: 'd', message: DRAFT_REFUSAL }]));
    const foot = footer(mount('d', 'completed'));
    expect(foot.querySelector('[data-testid="node-refused"]')!.textContent!.trim()).toBe('⚠ refused · 1 issue');
    expect(foot.querySelector('.latest-kept')).toBeNull();
    expect(foot.querySelector('[data-testid="draft-invalid"]')).toBeNull();
    expect(foot.textContent).not.toContain('completed');
  });

  it('an invalid draft with no refusal shows ⚠ invalid in place of the run outcome, its reason on hover', () => {
    nodes.set([methodNode('d', { x: 0, y: 0 }, INVALID_LABEL, { version: 'draft' })]);
    const foot = footer(mount('d', 'completed'));
    const status = foot.querySelector('[data-testid="draft-invalid"]') as HTMLElement;
    expect(status.textContent!.trim()).toBe('⚠ invalid');
    expect(status.title).toBe('label: env wfc-qc-env is not registered');
    expect(foot.querySelector('[data-testid="node-refused"]')).toBeNull();
    expect(foot.textContent).not.toContain('completed');
  });

  it('once the refusals clear, the footer shows the run outcome again', () => {
    showRefusals(refusalsByNode([REFUSAL]));
    clearRefusals();
    const foot = footer(mount('plain', 'completed'));
    expect(foot.querySelector('[data-testid="node-refused"]')).toBeNull();
    expect(foot.querySelector('.status-label')!.textContent).toContain('✓');
    expect(foot.querySelector('.status-label')!.textContent).toContain('completed');
  });

  it('the title row holds the node name only, refused or invalid', () => {
    nodes.set([
      methodNode('d', { x: 0, y: 0 }, INVALID_LABEL, { version: 'draft' }),
      methodNode('plain', { x: 0, y: 200 }, PLOT, {}),
    ]);
    showRefusals(refusalsByNode([REFUSAL]));
    for (const id of ['plain', 'd']) {
      const el = mount(id, 'completed');
      const header = el.querySelector('.node-header') as HTMLElement;
      expect(header.querySelector('[data-testid="node-refused"]')).toBeNull();
      expect(header.querySelector('[data-testid="draft-invalid"]')).toBeNull();
      expect(header.querySelector('.node-title')!.textContent).toBe(get(nodes).find(n => n.id === id)!.data.label);
      cleanup();
    }
  });
});
