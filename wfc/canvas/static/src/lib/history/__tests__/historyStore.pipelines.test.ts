/**
 * Vitest suite for the Pipelines view derived stores.
 *
 *   - pipelineRuns: groups runs by pipelineId with the server summary's
 *     status, the run buckets (pending from the summary's expected count),
 *     sample count, started timestamp, and the child runs in step order.
 *   - runningPipelineId(pid): returns the pid only when matching pipeline
 *     has running/pending runs; null otherwise (and on mismatch).
 *   - jumpToPipelineRun(pid, runId): cross-nav helper sets historyView,
 *     adds pid to expanded set, sets highlightedRunId.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import {
  runs,
  pipelineRuns,
  pipelineSummaries,
  runningPipelineId,
  jumpToPipelineRun,
  historyView,
  expandedPipelineIds,
  highlightedRunId,
} from '../historyStore';
import type { WfcRun } from '../historyApi';

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: partial.id ?? 'r1',
    module: 'm',
    method: 'method',
    version: 1,
    timestamp: 1,
    duration: 0,
    status: 'success',
    inputs: {},
    outputs: {},
    outputAvailability: {},
    metrics: {},
    dataSource: 'sample_a',
    parentRunIds: [],
    upstreamRunIds: partial.parentRunIds ?? [],
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

beforeEach(() => {
  runs.set([]);
  pipelineSummaries.set([]);
  historyView.set('pipelines');
  expandedPipelineIds.set(new Set());
  highlightedRunId.set(null);
});

describe('pipelineRuns derived store', () => {
  it('groups runs by pipelineId, takes the status from the server summary', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', status: 'success', dataSource: 's1', timestamp: 100 }),
      mkRun({ id: 'r2', pipelineId: 'p1', status: 'failed',  dataSource: 's2', timestamp: 200 }),
      mkRun({ id: 'r3', pipelineId: 'p2', status: 'running', dataSource: 's1', timestamp: 50 }),
      mkRun({ id: 'r4', pipelineId: null, status: 'success' }), // ignored — no pipelineId
    ]);
    pipelineSummaries.set([
      { pipelineId: 'p1', status: 'mixed', expected: 2, nodeOrder: null },
    ]);
    const rows = get(pipelineRuns);
    expect(rows).toHaveLength(2);
    const p1 = rows.find(r => r.pipelineId === 'p1')!;
    expect(p1.sampleCount).toBe(2);         // s1 + s2 distinct
    expect(p1.status).toBe('mixed');        // the server's word, never derived here
    expect(p1.started).toBe(100);           // earliest timestamp
    // No summary for p2: the browser decides no status of its own.
    expect(rows.find(r => r.pipelineId === 'p2')!.status).toBeNull();
  });

  it('counts ran, cached, failed, cancelled and running from the runs, pending from the summary', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', status: 'success' }),
      mkRun({ id: 'r2', pipelineId: 'p1', status: 'success', cacheSourceRunId: 'orig1' }),
      mkRun({ id: 'r3', pipelineId: 'p1', status: 'success', cacheSourceRunId: 'orig2' }),
      mkRun({ id: 'r4', pipelineId: 'p1', status: 'failed' }),
      mkRun({ id: 'r5', pipelineId: 'p1', status: 'cancelled', cancelReason: 'not_run' }),
      mkRun({ id: 'r6', pipelineId: 'p1', status: 'running' }),
      mkRun({ id: 'r7', pipelineId: 'p2', status: 'success' }),
      mkRun({ id: 'r8', pipelineId: 'p3', status: 'success' }),
    ]);
    pipelineSummaries.set([
      { pipelineId: 'p1', status: 'running', expected: 9, nodeOrder: null },
      { pipelineId: 'p2', status: 'succeeded', expected: null, nodeOrder: null },
      // More runs than expected: pending never goes below zero.
      { pipelineId: 'p3', status: 'succeeded', expected: 0, nodeOrder: null },
    ]);
    const rows = get(pipelineRuns);
    expect(rows.find(r => r.pipelineId === 'p1')!.buckets).toEqual({
      ran: 1, cached: 2, failed: 1, cancelled: 1, running: 1, pending: 3,
    });
    expect(rows.find(r => r.pipelineId === 'p2')!.buckets.pending).toBe(0);
    expect(rows.find(r => r.pipelineId === 'p3')!.buckets.pending).toBe(0);
  });

  it('sorts child runs by the document node order, then sample', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', nodeId: 'n_seg2', method: 'segment', dataSource: 'b', timestamp: 1 }),
      mkRun({ id: 'r2', pipelineId: 'p1', nodeId: 'n_load', method: 'load', dataSource: 'b', timestamp: 2 }),
      mkRun({ id: 'r3', pipelineId: 'p1', nodeId: 'n_seg1', method: 'segment', dataSource: 'a', timestamp: 3 }),
      mkRun({ id: 'r4', pipelineId: 'p1', nodeId: 'n_load', method: 'load', dataSource: 'a', timestamp: 4 }),
      mkRun({ id: 'r5', pipelineId: 'p1', nodeId: 'n_seg2', method: 'segment', dataSource: 'a', timestamp: 5 }),
      mkRun({ id: 'r6', pipelineId: 'p1', nodeId: 'n_seg1', method: 'segment', dataSource: 'b', timestamp: 6 }),
    ]);
    pipelineSummaries.set([
      { pipelineId: 'p1', status: 'succeeded', expected: 6, nodeOrder: ['n_load', 'n_seg1', 'n_seg2'] },
    ]);
    const p1 = get(pipelineRuns)[0];
    expect(p1.runs.map(r => r.id)).toEqual(['r4', 'r2', 'r3', 'r6', 'r5', 'r1']);
  });

  it('without a node order, orders the nodes by their first run, then sample', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', nodeId: 'n_b', dataSource: 'y', timestamp: 30 }),
      mkRun({ id: 'r2', pipelineId: 'p1', nodeId: 'n_a', dataSource: 'y', timestamp: 10 }),
      mkRun({ id: 'r3', pipelineId: 'p1', nodeId: 'n_b', dataSource: 'x', timestamp: 40 }),
      mkRun({ id: 'r4', pipelineId: 'p1', nodeId: 'n_a', dataSource: 'x', timestamp: 20 }),
    ]);
    const p1 = get(pipelineRuns)[0];
    expect(p1.runs.map(r => r.id)).toEqual(['r4', 'r2', 'r3', 'r1']);
  });

  it('titles rows with the first pipelineName in the group, else the short pipeline id', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', pipelineName: null, timestamp: 100 }),
      mkRun({ id: 'r2', pipelineId: 'p1', pipelineName: 'demo', timestamp: 200 }),
      mkRun({ id: 'r3', pipelineId: 'abcdef12-3456-7890', timestamp: 50 }),
    ]);
    const rows = get(pipelineRuns);
    expect(rows.find(r => r.pipelineId === 'p1')!.name).toBe('demo');
    // No pipelineName anywhere in the group → short id, never a child
    // run's method prefix (runName stays 'pip/run' in mkRun).
    expect(rows.find(r => r.pipelineId === 'abcdef12-3456-7890')!.name).toBe('abcdef12');
  });

  it('orders rows newest-first by started timestamp', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p_old', status: 'success', timestamp: 100 }),
      mkRun({ id: 'r2', pipelineId: 'p_new', status: 'success', timestamp: 500 }),
    ]);
    const rows = get(pipelineRuns);
    expect(rows.map(r => r.pipelineId)).toEqual(['p_new', 'p_old']);
  });
});

describe('runningPipelineId', () => {
  it('returns the pid when matching pipeline has running/pending runs', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', status: 'running' }),
      mkRun({ id: 'r2', pipelineId: 'p2', status: 'success' }),
    ]);
    expect(runningPipelineId('p1')).toBe('p1');
  });

  it('returns null for mismatched pid', () => {
    runs.set([mkRun({ id: 'r1', pipelineId: 'p1', status: 'running' })]);
    expect(runningPipelineId('p_other')).toBeNull();
  });

  it('returns null when matching pipeline is fully terminal', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', status: 'success' }),
      mkRun({ id: 'r2', pipelineId: 'p1', status: 'failed' }),
    ]);
    expect(runningPipelineId('p1')).toBeNull();
  });
});

describe('jumpToPipelineRun cross-nav', () => {
  it('switches view to pipelines, expands the pipeline row, and highlights the run', () => {
    historyView.set('lineages');
    expandedPipelineIds.set(new Set());
    highlightedRunId.set(null);

    jumpToPipelineRun('p_target', 'r_target');

    expect(get(historyView)).toBe('pipelines');
    expect(get(expandedPipelineIds).has('p_target')).toBe(true);
    expect(get(highlightedRunId)).toBe('r_target');
  });
});
