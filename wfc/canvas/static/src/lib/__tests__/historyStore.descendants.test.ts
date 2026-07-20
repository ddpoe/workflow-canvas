/**
 * Vitest suite for the Descendants tab derivations and the Lineages
 * cached-run summary bucket.
 *
 *   - statusBuckets.cached: overlapping informational count of cache-hit
 *     runs (cacheSourceRunId != null) after the same pre-status filters
 *     as the other buckets.
 *   - descendantForest: per-sample sections of strict-nested trees from
 *     filteredRuns; cache hits excluded; hidden runs' children promote to
 *     the nearest visible ancestor (same rule for filter exclusion).
 *   - collapseAllDescendants / expandAllDescendants / toggle state.
 *
 * Note: run ids are unique per test — effectiveSample memoises by run id
 * across store updates (the cache is only cleared by loadRuns()).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import {
  runs,
  filters,
  resetFilters,
  statusBuckets,
  descendantForest,
  descendantsCollapsed,
  toggleDescendantCollapsed,
  collapseAllDescendants,
  expandAllDescendants,
} from '../historyStore';
import type { DescendantSection, DescTreeNode } from '../historyStore';
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

/** Flatten a section's trees into 'parent>child' shape strings for assertions. */
function shape(section: DescendantSection): string[] {
  const out: string[] = [];
  const walk = (n: DescTreeNode, prefix: string) => {
    const label = prefix ? `${prefix}>${n.run.id}` : n.run.id;
    out.push(label);
    for (const c of n.children) walk(c, label);
  };
  for (const r of section.roots) walk(r, '');
  return out;
}

beforeEach(() => {
  runs.set([]);
  resetFilters();
  descendantsCollapsed.set(new Set());
});

describe('statusBuckets cached bucket', () => {
  it('counts cache hits as an overlapping bucket, after pre-status filters', () => {
    runs.set([
      mkRun({ id: 'cb1', status: 'success' }),
      mkRun({ id: 'cb2', status: 'success', cacheSourceRunId: 'cb1', module: 'm2' }),
      mkRun({ id: 'cb3', status: 'failed' }),
    ]);
    let b = get(statusBuckets);
    // Overlapping: the cached success counts in BOTH success and cached.
    expect(b.success).toBe(2);
    expect(b.cached).toBe(1);
    expect(b.failed).toBe(1);

    // Same pre-status filters as the other buckets: module filter drops
    // the cached run (module m2) from both its buckets.
    filters.update(f => ({ ...f, module: 'm' }));
    b = get(statusBuckets);
    expect(b.success).toBe(1);
    expect(b.cached).toBe(0);
  });
});

describe('descendantForest derivation', () => {
  it('groups by root sample with strict nesting; filter exclusion promotes children', () => {
    runs.set([
      mkRun({ id: 'fa1', dataSource: 's1', timestamp: 100 }),
      mkRun({ id: 'fa2', dataSource: 's1', parentRunIds: ['fa1'], status: 'failed', timestamp: 200 }),
      mkRun({ id: 'fa3', dataSource: 's1', parentRunIds: ['fa2'], timestamp: 300 }),
      mkRun({ id: 'fb1', dataSource: 's2', timestamp: 400 }),
    ]);
    let sections = get(descendantForest);
    expect(sections).toHaveLength(2);
    // Sections newest-first: s2 (ts 400) before s1.
    expect(sections[0].sample).toBe('s2');
    expect(sections[1].sample).toBe('s1');
    expect(shape(sections[1])).toEqual(['fa1', 'fa1>fa2', 'fa1>fa2>fa3']);

    // Status chip drops the failed fa2 — fa3 promotes under fa1.
    filters.update(f => ({ ...f, statuses: ['success'] }));
    sections = get(descendantForest);
    const s1 = sections.find(s => s.sample === 's1')!;
    expect(shape(s1)).toEqual(['fa1', 'fa1>fa3']);
  });

  it('excludes cache hits: children attach to nearest non-cached ancestor or section top level', () => {
    runs.set([
      // Family 1: mid-chain cache hit — grandchild promotes under the root.
      mkRun({ id: 'pa1', dataSource: 's1', timestamp: 100 }),
      mkRun({ id: 'pa2', dataSource: 's1', parentRunIds: ['pa1'], cacheSourceRunId: 'old', timestamp: 200 }),
      mkRun({ id: 'pa3', dataSource: 's1', parentRunIds: ['pa2'], timestamp: 300 }),
      // Family 2: fully-cached trunk — both branches land at section top level.
      mkRun({ id: 'qa1', dataSource: 's2', cacheSourceRunId: 'old', timestamp: 400 }),
      mkRun({ id: 'qa2', dataSource: 's2', parentRunIds: ['qa1'], cacheSourceRunId: 'old', timestamp: 500 }),
      mkRun({ id: 'qa3', dataSource: 's2', parentRunIds: ['qa2'], timestamp: 600 }),
      mkRun({ id: 'qa4', dataSource: 's2', parentRunIds: ['qa2'], timestamp: 700 }),
    ]);
    const sections = get(descendantForest);
    const s1 = sections.find(s => s.sample === 's1')!;
    expect(shape(s1)).toEqual(['pa1', 'pa1>pa3']);

    const s2 = sections.find(s => s.sample === 's2')!;
    // Two top-level roots (execution order), no invented structure, no
    // cached runs anywhere in the tree.
    expect(shape(s2)).toEqual(['qa3', 'qa4']);
  });
});

describe('collapse all / expand all', () => {
  it('collapse-all marks every expandable node; expand-all clears; toggle flips one', () => {
    runs.set([
      mkRun({ id: 'ca1', dataSource: 's1', timestamp: 100 }),
      mkRun({ id: 'ca2', dataSource: 's1', parentRunIds: ['ca1'], timestamp: 200 }),
      mkRun({ id: 'ca3', dataSource: 's1', parentRunIds: ['ca2'], timestamp: 300 }),
      mkRun({ id: 'cb9', dataSource: 's2', timestamp: 400 }), // leaf root — not expandable
    ]);
    collapseAllDescendants();
    expect(get(descendantsCollapsed)).toEqual(new Set(['ca1', 'ca2']));

    expandAllDescendants();
    expect(get(descendantsCollapsed).size).toBe(0);

    toggleDescendantCollapsed('ca2');
    expect(get(descendantsCollapsed)).toEqual(new Set(['ca2']));
    toggleDescendantCollapsed('ca2');
    expect(get(descendantsCollapsed).size).toBe(0);
  });
});
