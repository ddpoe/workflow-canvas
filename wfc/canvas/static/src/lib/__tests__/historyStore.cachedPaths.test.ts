/**
 * Vitest suite for the Lineages fully-cached-path suppression.
 *
 *   - visiblePathRows: pathRows minus rows whose every node is a cache
 *     hit (cacheSourceRunId != null), unless showFullyCachedPaths is on.
 *   - hiddenCachedPathCount: number of fully-cached rows, independent of
 *     the toggle (the count line shows it in both Show and Hide states).
 *
 * Note: run ids are unique per test — effectiveSample memoises by run id
 * across store updates (the cache is only cleared by loadRuns()).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import {
  runs,
  resetFilters,
  visiblePathRows,
  hiddenCachedPathCount,
  showFullyCachedPaths,
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
  resetFilters();
  showFullyCachedPaths.set(false);
});

describe('visiblePathRows fully-cached suppression', () => {
  it('hides a path whose every node is a cache hit and counts it', () => {
    runs.set([
      // Executed path: ca1 -> ca2
      mkRun({ id: 'ca1', timestamp: 100 }),
      mkRun({ id: 'ca2', parentRunIds: ['ca1'], timestamp: 200 }),
      // Fully-cached duplicate path: ca3 -> ca4
      mkRun({ id: 'ca3', cacheSourceRunId: 'ca1', timestamp: 300 }),
      mkRun({ id: 'ca4', parentRunIds: ['ca3'], cacheSourceRunId: 'ca2', timestamp: 400 }),
    ]);
    const visible = get(visiblePathRows);
    expect(visible).toHaveLength(1);
    expect(visible[0].nodes.map(n => n.id)).toEqual(['ca1', 'ca2']);
    expect(get(hiddenCachedPathCount)).toBe(1);
  });

  it('keeps a partially cached path fully visible with all its nodes', () => {
    runs.set([
      // Cached base feeding a fresh executed terminal.
      mkRun({ id: 'cb1', cacheSourceRunId: 'orig1', timestamp: 100 }),
      mkRun({ id: 'cb2', parentRunIds: ['cb1'], cacheSourceRunId: 'orig2', timestamp: 200 }),
      mkRun({ id: 'cb3', parentRunIds: ['cb2'], timestamp: 300 }),
    ]);
    const visible = get(visiblePathRows);
    expect(visible).toHaveLength(1);
    expect(visible[0].nodes.map(n => n.id)).toEqual(['cb1', 'cb2', 'cb3']);
    expect(get(hiddenCachedPathCount)).toBe(0);
  });

  it('reveals hidden paths when the toggle is on, keeping the count', () => {
    runs.set([
      mkRun({ id: 'cc1', timestamp: 100 }),
      mkRun({ id: 'cc2', cacheSourceRunId: 'cc1', timestamp: 200 }),
    ]);
    expect(get(visiblePathRows)).toHaveLength(1);

    showFullyCachedPaths.set(true);
    const visible = get(visiblePathRows);
    expect(visible).toHaveLength(2);
    expect(get(hiddenCachedPathCount)).toBe(1);
  });
});
