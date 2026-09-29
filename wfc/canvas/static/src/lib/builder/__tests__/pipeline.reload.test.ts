/**
 * Vitest suite for the load-in-canvas reload gates and graft helper.
 * Behavior-first per project test policy.
 *
 *   - confirmReplaceIfDirty: empty canvas → resolves without prompting.
 *   - checkRunningBlock: pipelineId with running/pending → blocked; only
 *     done/failed → unblocked; mismatch → unblocked.
 *   - graftRunReference: appends a run_reference node to `nodes` with the
 *     run-reference shape, leaves existing nodes intact, does NOT
 *     replace the canvas (no clearRunState side-effect).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { nodes, pipelineName } from '../stores';
import { runs } from '../../history/historyStore';
import { confirmDialogState, centerOnNodeRequest, graftToastState } from '../../shared/uiState';
import {
  confirmReplaceIfDirty,
  checkRunningBlock,
  graftRunReference,
  canvasPipelineId,
  loadPipeline,
} from '../pipeline';
import { layoutByDepth } from '../layout';
import type { PipelineJSON } from '../../shared/types';
import type { WfcRun } from '../../history/historyApi';
import type { Node } from '@xyflow/svelte';
import type { CanvasNodeData } from '../../shared/types';

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: partial.id ?? 'r1',
    module: 'm',
    method: 'method',
    version: '1',
    timestamp: 1,
    duration: 0,
    status: 'success',
    inputs: {},
    outputs: {},
    metrics: {},
    dataSource: 'sample_a',
    parentRunIds: [],
    upstreamRunIds: [],
    parents: [],
    experimentId: 'e',
    runName: 'pip/run',
    nid: 'v1',
    user: 'u',
    favorite: false,
    pipelineId: null,
    scriptPath: null,
    ...partial,
  };
}

function mkNode(id: string): Node<CanvasNodeData> {
  return {
    id,
    type: 'custom',
    position: { x: 0, y: 0 },
    data: {
      label: id,
      method: 'm',
      module: 'mod',
      color: '#ccc',
      inputs: [],
      outputs: [{ name: 'out', type: 'csv' }],
      params: [],
      paramValues: {},
      runStatus: 'idle',
      expanded: false,
      nodeType: 'method',
    },
  };
}

beforeEach(() => {
  nodes.set([]);
  runs.set([]);
  pipelineName.set('My Pipeline');
  canvasPipelineId.set(null);
});

describe('confirmReplaceIfDirty', () => {
  it('resolves true immediately when canvas is empty (no dialog opened)', async () => {
    nodes.set([]);
    confirmDialogState.set(null);
    const result = await confirmReplaceIfDirty('target_pipeline');
    expect(result).toBe(true);
    expect(get(confirmDialogState)).toBeNull(); // dialog never opened
  });

  it('opens the dirty-confirm dialog when canvas has nodes; resolves to user choice', async () => {
    nodes.set([mkNode('node_1')]);
    confirmDialogState.set(null);

    // Resolve the dialog with `true` (Discard-and-load).
    const proceedPromise = confirmReplaceIfDirty('target_pipeline');
    const req = get(confirmDialogState);
    expect(req).not.toBeNull();
    expect(req!.variant).toBe('dirty-confirm');
    expect(req!.targetName).toBe('target_pipeline');
    req!.resolve(true);
    expect(await proceedPromise).toBe(true);
    expect(get(confirmDialogState)).toBeNull(); // cleared after resolve

    // And again with Cancel.
    const cancelPromise = confirmReplaceIfDirty('target_pipeline');
    get(confirmDialogState)!.resolve(false);
    expect(await cancelPromise).toBe(false);
  });
});

describe('checkRunningBlock', () => {
  it('resolves false (not blocked) when fresh canvas has no in-flight pipelineId', async () => {
    // Semantic: a fresh canvas with no submitted-pipeline identity has
    // nothing to block on, even if some unrelated pipeline is running.
    // Callers (Toolbar, CanvasActions) pass `canvasPipelineId`, which is
    // null only when the canvas has no identity.
    runs.set([mkRun({ id: 'r1', pipelineId: 'p1', status: 'running' })]);
    expect(await checkRunningBlock(null)).toBe(false);
  });

  it('resolves true (BLOCKED) when matching pipeline has a running run', async () => {
    runs.set([mkRun({ id: 'r1', pipelineId: 'p1', status: 'running' })]);
    confirmDialogState.set(null);
    const promise = checkRunningBlock('p1');
    const req = get(confirmDialogState);
    expect(req).not.toBeNull();
    expect(req!.variant).toBe('running-block');
    req!.resolve(false); // user clicks OK
    expect(await promise).toBe(true);
  });

  it('resolves true (BLOCKED) when matching pipeline has a pending run', async () => {
    runs.set([mkRun({ id: 'r1', pipelineId: 'p1', status: 'pending' })]);
    const promise = checkRunningBlock('p1');
    get(confirmDialogState)!.resolve(false);
    expect(await promise).toBe(true);
  });

  it('resolves false (unblocked) when matching pipeline only has done/failed runs', async () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', status: 'success' }),
      mkRun({ id: 'r2', pipelineId: 'p1', status: 'failed' }),
    ]);
    expect(await checkRunningBlock('p1')).toBe(false);
  });

  it('resolves false (unblocked) when no run matches the given pipelineId', async () => {
    runs.set([mkRun({ id: 'r1', pipelineId: 'p_other', status: 'running' })]);
    expect(await checkRunningBlock('p_target')).toBe(false);
  });
});

describe('graftRunReference', () => {
  it('appends a run_reference node carrying the runId, leaving existing nodes untouched', () => {
    nodes.set([mkNode('existing_node')]);
    const newId = graftRunReference('r_42');
    const $nodes = get(nodes);
    expect($nodes).toHaveLength(2);
    expect($nodes[0].id).toBe('existing_node');
    const grafted = $nodes.find(n => n.id === newId);
    expect(grafted).toBeDefined();
    expect(grafted!.data.nodeType).toBe('run_reference');
    expect(grafted!.data.selectedRunId).toBe('r_42');
    // A run reference runs no method, so the graft leaves `method` blank.
    expect(grafted!.data.method).toBe('');
  });

  it('does NOT call clearRunState (graft is additive, not replace)', () => {
    nodes.set([mkNode('existing')]);
    // Set a marker on the existing node to verify it survives.
    nodes.update(ns => {
      ns[0].data.runStatus = 'completed';
      return [...ns];
    });
    graftRunReference('r_99');
    const $nodes = get(nodes);
    const existing = $nodes.find(n => n.id === 'existing');
    expect(existing!.data.runStatus).toBe('completed');
  });
});

describe('canvasPipelineId store', () => {
  // The store is the single source of truth for "what pipeline did the
  // user just submit/load into THIS canvas?". Four call sites read it
  // (PipelineRow, CanvasActions ×2 handlers, Toolbar) and the running-
  // block gate is scoped to its value. Behavior-first tests:

  it('starts null on a fresh canvas', () => {
    expect(get(canvasPipelineId)).toBeNull();
  });

  it('checkRunningBlock(canvasPipelineId) blocks when set to a running pipeline', async () => {
    // With the store set to a matching running pid, the gate must BLOCK.
    runs.set([mkRun({ id: 'r1', pipelineId: 'p_running', status: 'running' })]);
    canvasPipelineId.set('p_running');
    confirmDialogState.set(null);
    const promise = checkRunningBlock(get(canvasPipelineId));
    const req = get(confirmDialogState);
    expect(req).not.toBeNull();
    expect(req!.variant).toBe('running-block');
    req!.resolve(false);
    expect(await promise).toBe(true);
  });

  it('checkRunningBlock(canvasPipelineId) does NOT block when canvas pid mismatches the running pid', async () => {
    // The gate only fires when the *user's canvas*
    // shares identity with the running pipeline — not for any unrelated
    // running pipeline.
    runs.set([mkRun({ id: 'r1', pipelineId: 'p_other', status: 'running' })]);
    canvasPipelineId.set('p_canvas');
    expect(await checkRunningBlock(get(canvasPipelineId))).toBe(false);
  });

  it('checkRunningBlock(canvasPipelineId) does NOT block when canvas has no identity (null)', async () => {
    // Fresh canvas → null → not blocked, even if unrelated runs are
    // in flight. This is the same semantic as the null-pipelineId test
    // above, expressed in terms of the store.
    runs.set([mkRun({ id: 'r1', pipelineId: 'p_running', status: 'running' })]);
    canvasPipelineId.set(null);
    expect(await checkRunningBlock(get(canvasPipelineId))).toBe(false);
  });
});

describe('loadPipeline positions', () => {
  it('keeps a placed node where the document put it and lays out an unplaced one by depth', () => {
    const doc = {
      name: 'lineage_1',
      nodes: [
        { id: 'node_sel', type: 'input_selector', method: '', params: {}, samples: ['s1'], position: { x: 5, y: 7 } },
        { id: 'node_seg', type: 'method', method: 'segment', module: 'seg', params: {} },
      ],
      links: [{ source: 'node_sel', target: 'node_seg', sourceHandle: 'output', targetHandle: null }],
      samples: ['s1'],
    } as unknown as PipelineJSON;

    loadPipeline(doc);

    const byId = Object.fromEntries(get(nodes).map(n => [n.id, n.position]));
    expect(byId.node_sel).toEqual({ x: 5, y: 7 });
    expect(byId.node_seg).toEqual(layoutByDepth(doc.nodes, doc.links).node_seg);
  });
});

describe('GraftToast [Jump to node] bridge', () => {
  // The toast's onJump callback is constructed in CanvasActions.svelte; the
  // logic under test is "selecting + center-request happen together". We
  // re-create the same callback shape here to lock the contract: selection
  // mutates the matching node, and centerOnNodeRequest receives the new id.
  it('selects the grafted node AND publishes a center-on-node request', () => {
    nodes.set([mkNode('existing'), mkNode('to_jump')]);
    centerOnNodeRequest.set(null);
    graftToastState.set(null);

    const newNodeId = 'to_jump';
    // Same shape as CanvasActions.handleReferenceInCanvas constructs.
    const onJump = () => {
      nodes.update(ns => ns.map(n => ({ ...n, selected: n.id === newNodeId })));
      centerOnNodeRequest.set(newNodeId);
      graftToastState.set(null);
    };
    onJump();

    const $nodes = get(nodes);
    expect($nodes.find(n => n.id === 'to_jump')!.selected).toBe(true);
    expect($nodes.find(n => n.id === 'existing')!.selected).toBe(false);
    expect(get(centerOnNodeRequest)).toBe('to_jump');
  });
});
