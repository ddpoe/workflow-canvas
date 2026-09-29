/**
 * What each Inspector panel shows and writes
 * (canvas-ui cases `catalog.inspector-input-selector-panel`,
 *  `catalog.inspector-run-reference-panel`, `catalog.inspector-method-panel`).
 *
 * Each panel is mounted over the real stores with `fetch` stubbed, and the
 * node's data is read after every interaction.
 *
 * The age rows use the one relative-time function, History's, so a
 * three-hour-old record reads "3h ago" and a date nothing can parse reads
 * "--".
 *
 * No other test reaches `InspectorPanel.svelte`. A row's own editing (bind picker, numeric classification) is covered by
 * `valueList.bindUi.test.ts` and `valueList.numericTypes.test.ts`; this
 * module only asserts that a commit lands in the node's data.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { tick } from 'svelte';
import { get } from 'svelte/store';
import InspectorPanel from '../InspectorPanel.svelte';
import { nodes, edges, selectedNodeId, modules, samples } from '../../builder/stores';
import type { Node } from '@xyflow/svelte';
import type { CanvasNodeData, ModuleDef, SampleInfo, CompletedRun } from '../../shared/types';

// ── the stubbed routes ────────────────────────────────────────────────────

/**
 * The ages are dated from `Date.now()` rather than frozen: the panels read
 * the wall clock, and fake timers fight the `waitFor`/`tick` these mounted
 * rows need. Three hours *and five minutes* keeps the row off the 3h
 * boundary, where a few milliseconds of drift would read "2h ago".
 */
const THREE_HOURS_AGO = new Date(Date.now() - (3 * 60 + 5) * 60_000).toISOString();

const SAMPLES: SampleInfo[] = [
  { name: 'sample_a', file_type: 'csv', registered_path: '/data/sample_a.csv', file_size: 2048, registered_at: THREE_HOURS_AGO, description: null, file_count: null },
  // A date nothing can parse.
  { name: 'sample_b', file_type: 'tsv', registered_path: '/data/sample_b.tsv', file_size: 4096, registered_at: 'not a date', description: null, file_count: null },
];

/** A directory sample with a description, and a one-file directory. */
const DIRECTORY_SAMPLES: SampleInfo[] = [
  { name: 'reads', file_type: 'directory', registered_path: 'data/samples/reads/reads_dir', file_size: 3072, registered_at: THREE_HOURS_AGO, description: 'Paired-end reads, lane 1', file_count: 3 },
  { name: 'single', file_type: 'directory', registered_path: 'data/samples/single/one', file_size: 10, registered_at: THREE_HOURS_AGO, description: null, file_count: 1 },
];

/** What the samples route answers; a test may swap in another list. */
let samplesBody: SampleInfo[];

const COMPLETED_RUNS: CompletedRun[] = [
  {
    id: 'r_1', method: 'm_a', module: 'mod_a', sample: 'sample_a',
    params: { alpha: 1 }, output_slots: ['out_a', 'out_b'],
    pipeline_id: 'p_1', finished_at: THREE_HOURS_AGO,
  },
];

/** Per-route call counts, so a test can count refreshes per selection. */
let calls: Record<string, number>;

function stubFetch(): void {
  calls = {};
  vi.stubGlobal('fetch', vi.fn(async (input: unknown) => {
    const url = String(input);
    const route = url.split('?')[0];
    calls[route] = (calls[route] ?? 0) + 1;
    const body =
      route === '/api/wfc/samples' ? samplesBody :
      route === '/api/wfc/completed-runs' ? COMPLETED_RUNS :
      null;
    if (body === null) return { ok: false, json: async () => null };
    return { ok: true, json: async () => body };
  }));
}

// ── node fixtures ─────────────────────────────────────────────────────────

const CONTRACT: ModuleDef = {
  name: 'mod_a',
  color: '#2ecc71',
  methods: [{
    name: 'm_a',
    module: 'mod_a',
    inputs: [{ name: 'data', type: 'csv' }],
    outputs: [{ name: 'output', type: 'csv' }],
    params: [{ name: 'alpha', type: 'number', contractType: 'int', required: false, default: 1, constraints: { min: 0, max: 10 } }],
  }],
};

function mkNode(id: string, data: Partial<CanvasNodeData>): Node<CanvasNodeData> {
  return {
    id,
    type: 'custom',
    position: { x: 0, y: 0 },
    data: {
      label: id, method: '', module: '', color: '#1ABC9C',
      inputs: [], outputs: [], params: [], paramValues: {},
      runStatus: 'idle', expanded: false, nodeType: 'method',
      ...data,
    } as CanvasNodeData,
  };
}

function inputSelector(id: string): Node<CanvasNodeData> {
  return mkNode(id, {
    label: 'Input Selector', nodeType: 'input_selector',
    outputs: [{ name: 'output', type: 'csv' }],
    selectedSamples: [], fanMode: 'out',
  });
}

function runReference(id: string): Node<CanvasNodeData> {
  return mkNode(id, {
    label: 'Run Reference', nodeType: 'run_reference',
    outputs: [{ name: 'output', type: 'csv' }],
  });
}

function methodNode(id: string): Node<CanvasNodeData> {
  return mkNode(id, {
    label: 'm_a', method: 'm_a', module: 'mod_a', color: '#2ecc71',
    inputs: CONTRACT.methods[0].inputs,
    outputs: CONTRACT.methods[0].outputs,
    params: CONTRACT.methods[0].params,
  });
}

function dataOf(id: string): CanvasNodeData {
  return get(nodes).find(n => n.id === id)!.data;
}

function clickTab(container: HTMLElement, selector: string, text: string): Promise<unknown> {
  const tab = [...container.querySelectorAll(selector)]
    .find(b => (b.textContent ?? '').trim() === text) as HTMLButtonElement;
  expect(tab, `no ${selector} labelled ${text}`).toBeDefined();
  return fireEvent.click(tab);
}

beforeEach(() => {
  samplesBody = SAMPLES;
  stubFetch();
  nodes.set([]);
  edges.set([]);
  samples.set([]);
  modules.set([CONTRACT]);
  selectedNodeId.set(null);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// ── the input selector panel ──────────────────────────────────────────────

describe('the input selector panel lists samples and writes the selection', () => {
  it('lists every registered sample the route returned, with its type and file', async () => {
    nodes.set([inputSelector('node_is')]);
    selectedNodeId.set('node_is');
    const { container } = render(InspectorPanel);

    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(2));
    expect(container.querySelector('.card-count')!.textContent).toContain('2 registered inputs');
    expect([...container.querySelectorAll('.card-name')].map(e => e.textContent))
      .toEqual(['sample_a', 'sample_b']);
    expect([...container.querySelectorAll('.type-badge')].map(e => e.textContent))
      .toEqual(['csv', 'tsv']);
    expect(container.querySelector('.card-file')!.textContent).toBe('sample_a.csv');
  });

  it('accepting a selection writes selectedSamples into the node data', async () => {
    nodes.set([inputSelector('node_is')]);
    selectedNodeId.set('node_is');
    const { container } = render(InspectorPanel);
    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(2));

    await fireEvent.click(container.querySelectorAll('.card-wrap')[0]);
    await tick();
    expect(container.querySelector('.selected-summary')!.textContent).toContain('1 input selected');

    await fireEvent.click(container.querySelector('.accept-btn')!);
    await tick();
    expect(dataOf('node_is').selectedSamples).toEqual(['sample_a']);
  });

  it('a sample\'s age reads in History\'s format, unparseable dates included', async () => {
    nodes.set([inputSelector('node_is')]);
    selectedNodeId.set('node_is');
    const { container } = render(InspectorPanel);
    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(2));

    const metas = [...container.querySelectorAll('.card-meta')]
      .map(e => (e.textContent ?? '').trim());
    // Three hours old, in the one format the History tab uses.
    expect(metas[0]).toContain('3h ago');
    // And a date nothing can parse reads that function's own fallback.
    expect(metas[1]).toMatch(/^--/);
  });

  it('a sample shows its description, and a directory sample its file count', async () => {
    samplesBody = DIRECTORY_SAMPLES;
    nodes.set([inputSelector('node_is')]);
    selectedNodeId.set('node_is');
    const { container } = render(InspectorPanel);
    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(2));

    const cards = [...container.querySelectorAll('.card-wrap')];
    expect(cards[0].querySelector('.card-desc')!.textContent).toBe('Paired-end reads, lane 1');
    expect(cards[0].querySelector('.card-file')!.textContent).toBe('reads_dir');
    expect(cards[0].querySelector('.card-meta')!.textContent).toContain('3 files');
    expect(cards[1].querySelector('.card-desc')).toBeNull();
    expect(cards[1].querySelector('.card-meta')!.textContent).toMatch(/\b1 file\b(?!s)/);
  });

  it('a file sample shows no file count', async () => {
    nodes.set([inputSelector('node_is')]);
    selectedNodeId.set('node_is');
    const { container } = render(InspectorPanel);
    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(2));

    for (const meta of container.querySelectorAll('.card-meta')) {
      expect(meta.textContent).not.toMatch(/\bfiles?\b/);
    }
    expect(container.querySelector('.card-desc')).toBeNull();
  });

  it('the fan switch writes fanMode through the one writer', async () => {
    nodes.set([inputSelector('node_is')]);
    selectedNodeId.set('node_is');
    const { container } = render(InspectorPanel);
    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(2));

    await clickTab(container, '.sys-tab', 'Settings');
    await tick();
    const fanToggle = container.querySelector('.fanout-toggle .toggle input') as HTMLInputElement;
    await fireEvent.click(fanToggle);
    await tick();

    expect(dataOf('node_is').fanMode).toBe('in');
  });

  it('selecting the node refreshes the samples route exactly once; selecting a method node does not', async () => {
    nodes.set([inputSelector('node_is'), methodNode('node_m')]);
    selectedNodeId.set('node_is');
    render(InspectorPanel);

    await waitFor(() => expect(calls['/api/wfc/samples'] ?? 0).toBeGreaterThan(0));
    await tick();
    await tick();
    // One selection of an input selector is one refresh. The panel reads the
    // store it refreshes instead of a second copy it writes, so the effect
    // does not re-fire on its own write.
    expect(calls['/api/wfc/samples']).toBe(1);

    selectedNodeId.set('node_m');
    await tick();
    await tick();
    expect(calls['/api/wfc/samples']).toBe(1);

    // And the method node asks the completed-runs route nothing either.
    expect(calls['/api/wfc/completed-runs'] ?? 0).toBe(0);
  });
});

// ── the run reference panel ───────────────────────────────────────────────

describe('the run reference panel picks a finished run', () => {
  it('lists the completed runs the route returned', async () => {
    nodes.set([runReference('node_rr')]);
    selectedNodeId.set('node_rr');
    const { container } = render(InspectorPanel);

    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(1));
    expect(container.querySelector('.card-count')!.textContent).toContain('1 completed run');
    expect(container.querySelector('.card-name')!.textContent).toBe('m_a');
    expect(container.querySelector('.card-sample')!.textContent).toBe('sample_a');
    expect(container.querySelector('.card-meta')!.textContent).toContain('Run #r_1');
  });

  it('the run\'s age reads in History\'s format beside its id', async () => {
    nodes.set([runReference('node_rr')]);
    selectedNodeId.set('node_rr');
    const { container } = render(InspectorPanel);
    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(1));

    const meta = container.querySelector('.card-meta')!.textContent ?? '';
    expect(meta).toContain('Run #r_1');
    expect(meta).toContain('3h ago');
  });

  it('accepting a run writes selectedRunId and the run\'s output slots, and no input-selector fields', async () => {
    nodes.set([runReference('node_rr')]);
    selectedNodeId.set('node_rr');
    const { container } = render(InspectorPanel);
    await waitFor(() => expect(container.querySelectorAll('.card-wrap')).toHaveLength(1));

    // The accept button is disabled until a run is staged.
    expect(container.querySelector('.accept-btn')).toBeDisabled();
    await fireEvent.click(container.querySelector('.card-wrap')!);
    await tick();
    await fireEvent.click(container.querySelector('.accept-btn')!);
    await tick();

    const data = dataOf('node_rr');
    expect(data.selectedRunId).toBe('r_1');
    // Every output slot the run produced becomes a handle on the node.
    expect(data.outputs.map(o => o.name)).toEqual(['out_a', 'out_b']);
    // A run reference carries none of the input selector's fields.
    expect(data.selectedSamples).toBeUndefined();
    expect(data.fanMode).toBeUndefined();
    expect(data.inputCollapsed).toBeUndefined();
  });
});

// ── the method panel ──────────────────────────────────────────────────────

describe('the method panel shows the contract and writes param values', () => {
  it('lists the declared params with type and constraint hint, beside the node\'s wiring', async () => {
    nodes.set([methodNode('node_m'), mkNode('node_up', { label: 'upstream', method: 'm_up', module: 'mod_a', outputs: [{ name: 'output', type: 'csv' }] })]);
    edges.set([{ id: 'e_1', source: 'node_up', target: 'node_m', sourceHandle: 'output', targetHandle: 'data' }]);
    selectedNodeId.set('node_m');
    const { container } = render(InspectorPanel);
    await tick();

    // The declared parameter, with its type and its constraint hint.
    expect(container.querySelector('.pname')!.textContent).toBe('alpha');
    expect(container.querySelector('.ptype')!.textContent).toBe('number');
    expect(container.querySelector('.constraint-hint')).not.toBeNull();

    // Wiring: the input slot names its upstream, the output has no consumer.
    const ioRows = [...container.querySelectorAll('.io-row')];
    expect(ioRows[0].querySelector('.io-name')!.textContent).toBe('data');
    expect(ioRows[0].querySelector('.io-chip-label')!.textContent).toBe('upstream');
    expect(ioRows[1].querySelector('.io-name')!.textContent).toBe('output');
    expect(ioRows[1].querySelector('.io-unwired')!.textContent).toContain('no consumers');
  });

  it('committing a row writes paramValues into the node data', async () => {
    nodes.set([methodNode('node_m')]);
    selectedNodeId.set('node_m');
    const { container } = render(InspectorPanel);
    await tick();

    // Drive the row exactly as a user does — through its editor actor, not
    // by writing the store (the ValueList suites cover the row itself).
    const editBtn = container.querySelector('.act.edit') as HTMLButtonElement | null;
    if (editBtn) { await fireEvent.click(editBtn); await tick(); }
    const input = container.querySelector('input.text') as HTMLInputElement;
    await fireEvent.input(input, { target: { value: '7' } });
    await fireEvent.click(container.querySelector('.act.commit')!);

    await waitFor(() => expect(dataOf('node_m').paramValues.alpha).toBe(7));
  });

  it('the output tab stays idle until the node has a run', async () => {
    nodes.set([methodNode('node_m')]);
    selectedNodeId.set('node_m');
    const { container } = render(InspectorPanel);
    await tick();

    await clickTab(container, '.tab', 'Output');
    await tick();
    expect(container.querySelector('.output-empty')!.textContent)
      .toContain('Run this pipeline to see output for this node.');
    // No stream was opened: nothing asked for a log.
    expect(Object.keys(calls).some(r => r.includes('log'))).toBe(false);
  });
});
