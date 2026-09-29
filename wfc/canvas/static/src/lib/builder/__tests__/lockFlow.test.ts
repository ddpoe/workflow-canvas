/**
 * The lock flow behind the Runs Preview, Lock All and Run.
 *
 * Statuses belong to one locked state: before a lock, and after any edit,
 * the preview shows run counts only; Lock All posts the pipeline once.
 * Lock All, and Run with a row still unlocked, open one summary; a row that
 * cannot be committed is named there, and the wait never hangs. These tests
 * drive the real stores, the real preview component and real parameter
 * editor actors registered through the runtime bridge; only `fetch` is
 * stubbed, answering in the cache-status route's shape.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, waitFor, cleanup } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { createActor } from 'xstate';
import RunsPreview from '../RunsPreview.svelte';
import { loadPipeline } from '../pipeline';
import { pipelineName, nodes } from '../stores';
import {
  lockNow, clearLock, lockState, lockSummary, openLockSummary, confirmLockSummary,
  requestRun, defaultsInUse,
} from '../lockFlow';
import {
  registerEditorChild, unregisterEditorChild, editorsDirty, pipelineRunActor, dispatchReset,
} from '../../machines/root';
import { makeParamEditorMachine, type ParamEditorActor } from '../../machines/paramEditor.machine';
import { makeVariantMachine, type VariantActor } from '../../machines/variant.machine';
import type { ChildActor } from '../../machines/paramEditorAggregator.machine';
import type { CanvasNodeData, PipelineJSON } from '../../shared/types';
import type { CacheStatusResponse } from '../runsPreviewStatus';

const DOC: PipelineJSON = {
  name: 'lock',
  nodes: [
    { id: 's', type: 'input_selector', method: '', module: '', params: {}, samples: ['s1', 's2'] },
    { id: 'm1', type: 'method', method: 'filter', module: 'mod', params: {} },
  ],
  links: [{ source: 's', target: 'm1', targetHandle: 'data' }],
  samples: ['s1', 's2'],
};

function answer(): CacheStatusResponse {
  return {
    blocked_reason: null,
    rows: ['s1', 's2'].map(s => ({
      key: `m1::${s}::default`, node_id: 'm1', sample: s, variant: 'default',
      status: 'new_step_changed', reason: null, cache_key: null,
      source_run_id: null, source_nid: null, outputs: [],
    })),
  } as CacheStatusResponse;
}

let fetchMock: ReturnType<typeof vi.fn>;
const cacheStatusCalls = () =>
  fetchMock.mock.calls.filter(c => c[0] === '/api/wfc/cache-status').length;
let registered: Array<[string, ChildActor]> = [];

function register(id: string, actor: ChildActor) {
  registerEditorChild(id, actor);
  registered.push([id, actor]);
}

/** A base-row editor that is open with a draft. */
function openEditor(id: string, paramType: 'int' | 'string', draft: string): ParamEditorActor {
  const a = createActor(makeParamEditorMachine(), {
    input: { nodeId: 'm1', paramName: id.split('::')[1], paramType, required: false, currentValue: '' },
  }) as unknown as ParamEditorActor;
  a.start();
  register(id, a);
  a.send({ type: 'EDIT' });
  a.send({ type: 'CHANGE_VALUE', value: draft });
  return a;
}

beforeEach(() => {
  fetchMock = vi.fn(async () => ({
    ok: true, status: 200, json: async () => answer(), text: async () => '',
  } as unknown as Response));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  loadPipeline(DOC);
});

afterEach(() => {
  for (const [id, a] of registered) { unregisterEditorChild(id, a); a.stop(); }
  registered = [];
  lockSummary.set(null);
  clearLock();
  cleanup();
  vi.restoreAllMocks();
});

const statusCells = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('tr.summary-row td')).slice(2, 7).map(td => td.textContent!.trim());

describe('lock flow', () => {
  it('shows counts only until Lock All, posts once, and any later edit clears the statuses', async () => {
    const { container } = render(RunsPreview);
    await waitFor(() => expect(container.querySelector('tr.summary-row')).not.toBeNull());
    expect(statusCells(container)).toEqual(['—', '—', '—', '—', '—']);
    expect(container.querySelector('.tally-unlocked')!.textContent).toContain('2 runs');
    expect(cacheStatusCalls()).toBe(0);

    await lockNow();
    await waitFor(() => expect(statusCells(container)).toEqual(['0', '0', '0', '2', '0']));
    expect(cacheStatusCalls()).toBe(1);

    // An edit clears the statuses; nothing is re-posted per keystroke.
    pipelineName.set('lock-renamed');
    pipelineName.set('lock-renamed-again');
    await waitFor(() => expect(statusCells(container)).toEqual(['—', '—', '—', '—', '—']));
    expect(get(lockState).status).toBe('unlocked');
    expect(cacheStatusCalls()).toBe(1);

    // Moving a node changes no run: a new lock survives it.
    await lockNow();
    nodes.update(ns => { ns[1].position = { x: 999, y: 999 }; return [...ns]; });
    expect(get(lockState).status).toBe('locked');

    // Opening a parameter editor clears the statuses too.
    openEditor('m1::min_quality::base', 'string', 'x');
    expect(get(editorsDirty)).toBe(true);
    await waitFor(() => expect(statusCells(container)).toEqual(['—', '—', '—', '—', '—']));
  });

  it('names a row that cannot be committed and never hangs, even on a row that ignores COMMIT', async () => {
    openEditor('m1::bins::base', 'int', 'not-a-number');
    const adding = createActor(makeVariantMachine(), {
      input: { paramName: 'bins', variantId: 'v1', paramType: 'int', required: false, currentValue: '', siblingValues: [] },
    }) as unknown as VariantActor;
    adding.start();
    register('m1::label::variant::v1', adding);
    adding.send({ type: 'ADD_VARIANT' });
    expect(adding.getSnapshot().value).toBe('addingVariant');
    const good = openEditor('m1::threshold::base', 'string', 'high');

    openLockSummary('lock');
    expect(get(lockSummary)!.unlocked).toEqual(['filter · bins', 'filter · label', 'filter · threshold']);

    await confirmLockSummary();
    const s = get(lockSummary)!;
    expect(s.failed).toEqual(['filter · bins', 'filter · label']);
    expect(s.busy).toBe(false);
    expect(good.getSnapshot().value).toBe('committed');
    // Nothing proceeded: no lock and no request.
    expect(get(lockState).status).toBe('unlocked');
    expect(cacheStatusCalls()).toBe(0);
  });

  it('Run with an unlocked row opens the summary and runs only after confirm; Lock All confirm locks', async () => {
    openEditor('m1::threshold::base', 'string', 'high');
    requestRun();
    expect(get(lockSummary)!.purpose).toBe('run');
    expect(pipelineRunActor.getSnapshot().value).toBe('idle');

    await confirmLockSummary();
    expect(get(lockSummary)).toBeNull();
    // The run was dispatched: the run actor left idle. Run posts no cache status.
    expect(pipelineRunActor.getSnapshot().value).not.toBe('idle');
    expect(cacheStatusCalls()).toBe(0);
    dispatchReset();

    openLockSummary('lock');
    await confirmLockSummary();
    expect(get(lockState).status).toBe('locked');
    expect(cacheStatusCalls()).toBe(1);
  });

  it('lists every parameter that runs its declared default, by node and parameter, with its value', () => {
    const data = {
      label: 'filter', nodeType: 'method',
      params: [
        { name: 'set', type: 'int', default: 1 },
        { name: 'untouched', type: 'int', default: 5 },
        { name: 'blanked', type: 'str', default: 'x' },
        { name: 'swept', type: 'int', default: 2 },
        { name: 'none', type: 'str' },
      ],
    } as unknown as CanvasNodeData;
    const doc = {
      name: 'd', links: [], samples: ['s1'],
      nodes: [{ id: 'm1', type: 'method', method: 'filter', module: 'mod', params: { set: 3 } }],
      param_sets: { m1: { v1: { set: 3, swept: 4 } } },
    } as PipelineJSON;
    expect(defaultsInUse(doc, [{ id: 'm1', data }])).toEqual([
      { node: 'filter', param: 'untouched', value: '5' },
      { node: 'filter', param: 'blanked', value: 'x' },
      { node: 'filter', param: 'none', value: 'no declared default' },
    ]);
  });
});
