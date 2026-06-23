/**
 * Vitest suite for the Pipelines view derived stores added by the
 * load-in-canvas cycle (Task 2 + 5).
 *
 *   - pipelineRuns: groups runs by pipelineId with rolled-up status,
 *     progress N/M, sample count, started timestamp.
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
    version: '1',
    timestamp: 1,
    duration: 0,
    status: 'success',
    inputs: {},
    outputs: {},
    metrics: {},
    dataSource: 'sample_a',
    parentRunIds: [],
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
  historyView.set('pipelines');
  expandedPipelineIds.set(new Set());
  highlightedRunId.set(null);
});

describe('pipelineRuns derived store', () => {
  it('groups runs by pipelineId with correct rollups', () => {
    runs.set([
      mkRun({ id: 'r1', pipelineId: 'p1', status: 'success', dataSource: 's1', timestamp: 100 }),
      mkRun({ id: 'r2', pipelineId: 'p1', status: 'failed',  dataSource: 's2', timestamp: 200 }),
      mkRun({ id: 'r3', pipelineId: 'p2', status: 'running', dataSource: 's1', timestamp: 50 }),
      mkRun({ id: 'r4', pipelineId: null, status: 'success' }), // ignored — no pipelineId
    ]);
    const rows = get(pipelineRuns);
    expect(rows).toHaveLength(2);
    const p1 = rows.find(r => r.pipelineId === 'p1')!;
    expect(p1.total).toBe(2);
    expect(p1.done).toBe(2);                // success + failed both counted as "done"
    expect(p1.sampleCount).toBe(2);         // s1 + s2 distinct
    expect(p1.status).toBe('failed');       // running > pending > failed > cancelled > success
    expect(p1.started).toBe(100);           // earliest timestamp
    const p2 = rows.find(r => r.pipelineId === 'p2')!;
    expect(p2.status).toBe('running');
    expect(p2.done).toBe(0);                // running doesn't count as done
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
