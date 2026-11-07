/**
 * How the Inspector shows a method node whose last run failed.
 *
 * A red box above the Config / Output tabs, so it reads the same on both:
 * "This step failed", the run's message cut to two lines, and a See output
 * pill that opens the Output tab. The Output tab's label carries a ✗, and
 * the tab shows the log once: the traceback is the log's stderr, never a
 * second box under it.
 *
 * The node's run actor is the one boundary faked: `pipelineRunActor`'s
 * snapshot hands the panel a child in its `failed` state, as the real actor
 * tree does after a RUN_FAILED. The persisted log arrives over a faked
 * `EventSource`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import InspectorPanel from '../InspectorPanel.svelte';
import { nodes, edges, selectedNodeId, modules, samples } from '../../builder/stores';
import { pipelineRunActor } from '../../machines/root.js';
import type { NodeRunActor } from '../../machines/nodeRun.machine';
import type { Node } from '@xyflow/svelte';
import type { CanvasNodeData, ModuleDef } from '../../shared/types';

const MESSAGE = "KeyError: 'gene_symbol'";

const CONTRACT: ModuleDef = {
  name: 'mod_a',
  methods: [{
    name: 'm_a', module: 'mod_a',
    inputs: [{ name: 'data', type: 'csv' }],
    outputs: [{ name: 'output', type: 'csv' }],
    params: [{ name: 'alpha', type: 'int', contractType: 'int', required: false, default: 1 }],
  }],
};

function methodNode(id: string): Node<CanvasNodeData> {
  return {
    id, type: 'custom', position: { x: 0, y: 0 },
    data: {
      label: 'm_a', method: 'm_a', module: 'mod_a',
      inputs: CONTRACT.methods[0].inputs, outputs: CONTRACT.methods[0].outputs,
      params: CONTRACT.methods[0].params, paramValues: {},
      runStatus: 'failed', expanded: false, nodeType: 'method',
    } as CanvasNodeData,
  };
}

/**
 * Hand the panel `child` as the node's run actor. The fake carries only
 * what the panel reads from a node run actor (`getSnapshot()`'s value,
 * context, children and `matches`), so it is not a whole actor: the one
 * cast through `unknown` is where the fake stands in for the real one.
 */
function withNodeRef(nodeId: string, child: { getSnapshot: () => object }): void {
  const real = pipelineRunActor.getSnapshot.bind(pipelineRunActor);
  const ref = child as unknown as NodeRunActor;
  vi.spyOn(pipelineRunActor, 'getSnapshot').mockImplementation(() => {
    const snap = real();
    return { ...snap, context: { ...snap.context, nodeRefs: { [nodeId]: ref } } };
  });
}

/** The node's run actor, as the actor tree holds it after a failed run. */
function failNode(nodeId: string, message: string | null = MESSAGE): void {
  withNodeRef(nodeId, {
    getSnapshot: () => ({
      value: 'failed',
      context: { runId: '41', error_message: message ?? undefined },
      children: {},
      matches: (state: unknown) => state === 'failed',
    }),
  });
}

class FakeEventSource {
  static last: FakeEventSource | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeEventSource.last = this; }
  push(frame: object): void { this.onmessage?.({ data: JSON.stringify(frame) }); }
  close(): void {}
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => null })));
  vi.stubGlobal('EventSource', FakeEventSource);
  nodes.set([methodNode('node_m')]);
  edges.set([]);
  samples.set([]);
  modules.set([CONTRACT]);
  selectedNodeId.set('node_m');
  failNode('node_m');
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  FakeEventSource.last = null;
});

function tab(container: HTMLElement, name: string): HTMLButtonElement {
  return [...container.querySelectorAll('.tabs .tab')]
    .find(b => (b.textContent ?? '').startsWith(name)) as HTMLButtonElement;
}

describe('the Inspector on a failed method node', () => {
  it('shows the failure above the tabs with the message and See output, no expand', async () => {
    const view = render(InspectorPanel);
    const box = await view.findByTestId('node-error-box');
    expect(box.textContent).toContain('This step failed');
    expect(box.textContent).toContain(MESSAGE);
    expect(view.queryByText(/Show full error/)).toBeNull();
    expect(tab(view.container, 'Output').textContent).toContain('✗');
  });

  it('See output opens the Output tab and the box stays', async () => {
    const view = render(InspectorPanel);
    await fireEvent.click(await view.findByRole('button', { name: /See output/ }));
    expect(tab(view.container, 'Output').classList.contains('active')).toBe(true);
    expect(view.getByTestId('node-error-box')).toBeTruthy();
  });

  it('the Output tab shows the traceback once, in the log', async () => {
    const view = render(InspectorPanel);
    await fireEvent.click(tab(await waitFor(() => {
      const t = tab(view.container, 'Output');
      expect(t).toBeDefined();
      return view.container;
    }), 'Output'));
    await waitFor(() => expect(FakeEventSource.last).not.toBeNull());
    FakeEventSource.last!.push({ type: 'stderr', data: 'Traceback (most recent call last):' });
    FakeEventSource.last!.push({ type: 'stderr', data: MESSAGE });
    FakeEventSource.last!.push({ type: 'terminal', status: 'failed', error_message: MESSAGE, error_traceback: `Traceback (most recent call last):\n${MESSAGE}` });

    const pane = await view.findByTestId('log-pane');
    expect(pane.textContent).toContain('Traceback (most recent call last)');
    const tracebacks = (view.container.textContent ?? '').split('Traceback (most recent call last)').length - 1;
    expect(tracebacks).toBe(1);
  });

  it('a node that failed with no message still shows the box and the ✗', async () => {
    vi.restoreAllMocks();
    failNode('node_m', null);
    const view = render(InspectorPanel);
    const box = await view.findByTestId('node-error-box');
    expect(box.textContent).toContain('This step failed');
    expect(box.querySelector('.err-box-msg')).toBeNull();
    expect(tab(view.container, 'Output').textContent).toContain('✗');
  });

  it('a run that wrote no output shows the traceback its ending carried', async () => {
    const view = render(InspectorPanel);
    await fireEvent.click(await view.findByRole('button', { name: /See output/ }));
    await waitFor(() => expect(FakeEventSource.last).not.toBeNull());
    FakeEventSource.last!.push({
      type: 'terminal', status: 'failed', error_message: 'could not start the step log',
      error_traceback: 'Traceback (most recent call last):\n  File "dispatch.py", line 646\nOSError: disk full',
    });
    const pane = await view.findByTestId('log-pane');
    expect(pane.textContent).toContain('OSError: disk full');
    expect(view.queryByText('No output captured.')).toBeNull();
  });
});

describe('the Inspector on a running method node', () => {
  it('a live stream that drops says Connection lost in the log bar', async () => {
    vi.restoreAllMocks();
    // The streaming machine ends a lost connection as `failed` with its own
    // terminal error text (streaming.machine.ts).
    const streaming = {
      getSnapshot: () => ({
        value: 'failed',
        context: { lines: [{ kind: 'stdout', line: 'tick 1' }], terminalStatus: 'failed', terminalError: 'Connection lost', terminalTraceback: null },
      }),
      subscribe: () => ({ unsubscribe() {} }),
    };
    withNodeRef('node_m', {
      getSnapshot: () => ({
        value: 'running', context: { runId: '41' }, children: { streaming },
        matches: () => false,
      }),
    });
    const view = render(InspectorPanel);
    await waitFor(() => expect(tab(view.container, 'Output')).toBeDefined());
    await fireEvent.click(tab(view.container, 'Output'));
    expect((await view.findByTestId('log-note')).textContent).toBe('Connection lost');
    expect(view.queryByTestId('node-error-box')).toBeNull();   // running, not failed
  });
});

describe('the Inspector on a node cancelled before it ran', () => {
  it('shows the upstream banner and no log box', async () => {
    vi.restoreAllMocks();
    withNodeRef('node_m', {
      getSnapshot: () => ({
        value: { cancelled: 'becauseUpstream' },
        context: { runId: null, upstreamNodeId: 'node_up', upstreamRunId: '40' },
        children: {},
        matches: (s: unknown) => JSON.stringify(s) === JSON.stringify({ cancelled: 'becauseUpstream' }),
      }),
    });
    const view = render(InspectorPanel);
    await waitFor(() => expect(tab(view.container, 'Output')).toBeDefined());
    await fireEvent.click(tab(view.container, 'Output'));
    expect(await view.findByText(/Cancelled because/)).toBeTruthy();
    expect(view.queryByTestId('log-pane')).toBeNull();
  });
});
