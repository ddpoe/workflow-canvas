/**
 * Draft nodes and draft-mode nodes on the canvas (SOP canvas-colours,
 * Drafts).
 *
 * `CustomNode.svelte` mounted over node data built by the one writer,
 * `nodeData.ts::methodNode`, with the canvas stores holding a draft node
 * wired to a downstream node. Both draw the mask-drawn dash in their status
 * colour (neutral when not run); the draft's DRAFT MODE flag is solid, the
 * downstream one an outline; an invalid draft says why on hover; a run in
 * draft mode says only its latest results are kept.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import CustomNode from '../CustomNode.svelte';
import { methodNode } from '../nodeData';
import { nodes, edges } from '../stores';
import type { CanvasNodeData, DraftDef, MethodDef, RunStatus } from '../../shared/types';
import type { Node } from '@xyflow/svelte';

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

let canvas: Node<CanvasNodeData>[];

function seed(draft: MethodDef = LABEL): void {
  canvas = [
    methodNode('d', { x: 0, y: 0 }, draft, { version: 'draft' }),
    methodNode('p', { x: 200, y: 0 }, PLOT, {}),
    methodNode('x', { x: 0, y: 200 }, PLOT, {}),
  ];
  nodes.set(canvas);
  edges.set([{ id: 'e1', source: 'd', target: 'p', sourceHandle: 'output', targetHandle: 'data' }]);
}

function mount(id: string, status: RunStatus = 'idle'): HTMLElement {
  const data = { ...canvas.find(n => n.id === id)!.data, runStatus: status };
  const { container } = render(CustomNode, { props: { data, id } });
  return container.querySelector('.custom-node') as HTMLElement;
}

beforeEach(() => { seed(); });
afterEach(() => { cleanup(); nodes.set([]); edges.set([]); });

describe('draft nodes draw the draft dash and flag', () => {
  it('the draft and its downstream node are dashed in the neutral colour until run; an unrelated node is not', () => {
    for (const id of ['d', 'p']) {
      const el = mount(id);
      expect(el.classList.contains('dash-2')).toBe(true);
      expect(el.style.getPropertyValue('--dash-color')).toBe('var(--dash-neutral)');
    }
    const plain = mount('x');
    expect(plain.classList.contains('dash-2')).toBe(false);
    expect(plain.querySelector('[data-testid="draft-flag"]')).toBeNull();
  });

  it('a run in draft mode dashes in its status colour and says only the latest is kept', () => {
    const ok = mount('d', 'completed');
    expect(ok.style.getPropertyValue('--dash-color')).toBe('var(--status-ok)');
    expect(ok.querySelector('.latest-kept')!.textContent).toBe('· latest kept');
    const bad = mount('p', 'failed');
    expect(bad.style.getPropertyValue('--dash-color')).toBe('var(--status-failed)');
    // A draft-mode step is never a cache hit: no CACHED pill.
    expect(mount('p', 'cached').querySelector('.cached-pill')).toBeNull();
  });

  it('the flag is solid on the draft and an outline downstream, each with its help text', () => {
    const draftFlag = mount('d').querySelector('[data-testid="draft-flag"]')!;
    expect(draftFlag.classList.contains('strong')).toBe(true);
    expect(draftFlag.textContent).toContain('DRAFT MODE');
    expect(draftFlag.querySelector('.qm')).not.toBeNull();
    const down = mount('p');
    const downFlag = down.querySelector('[data-testid="draft-flag"]')!;
    expect(downFlag.classList.contains('strong')).toBe(false);
    expect(down.querySelector('.draft-tip')!.textContent).toContain('this step uses results from the label draft');
    expect(down.querySelector('.param-count')!.textContent).toBe('draft input · not run');
  });

  it('the draft node shows ● DRAFT and the draft env; an invalid draft says why on hover', () => {
    const el = mount('d');
    expect(el.querySelector('.node-version-draft')!.textContent).toContain('● DRAFT');
    expect((el.querySelector('.node-env') as HTMLElement).title).toBe('container:draft-env');
    expect(el.querySelector('[data-testid="draft-invalid"]')).toBeNull();
    cleanup();

    seed({ ...LABEL, draft: { ...DRAFT, valid: false, reasons: ['env wfc-qc-env is not registered'] } });
    const bad = mount('d').querySelector('[data-testid="draft-invalid"]') as HTMLElement;
    expect(bad.textContent).toBe('⚠ invalid');
    expect(bad.title).toBe('label: env wfc-qc-env is not registered');
  });
});
