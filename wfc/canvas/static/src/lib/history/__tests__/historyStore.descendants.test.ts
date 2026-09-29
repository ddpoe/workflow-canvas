/**
 * Vitest suite for the Descendants tab derivations and the Lineages
 * cached-run summary bucket.
 *
 *   - statusBuckets.cached: overlapping informational count of cache-hit
 *     runs (cacheSourceRunId != null) after the same pre-status filters
 *     as the other buckets.
 *   - descendantForest: per-sample sections of trees from filteredRuns;
 *     cache hits excluded; hidden runs' children promote to the nearest
 *     visible ancestors (same rule for filter exclusion), reduced so no
 *     grandparent counts as a parent. A run with several parents has a
 *     full entry under each, keyed by position; nothing depends on start
 *     times.
 *   - descendantRows / descendantHighlight: folding goes by entry,
 *     highlighting by run.
 *   - scopedDescendantForest and the scope actions: "→ Descendants"
 *     scopes this view to one run's subtree. The scoped view shows no
 *     cache-hit copies because it is this same forest, pruned.
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
  historyView,
  descendantScope,
  scopeDescendantsTo,
  clearDescendantScope,
  scopedDescendantForest,
  descendantRows,
  descendantHighlight,
  selectRun,
  selectedRunIds,
  toggleRunSelection,
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
    // Without a reuse edge the resolved upstreams are the input edges; a
    // cache-hit fixture states its upstreams literally.
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
    // Sections follow their sample label, not when their runs started:
    // s1 before s2 although s2's run is the newer.
    expect(sections[0].sample).toBe('s1');
    expect(sections[1].sample).toBe('s2');
    expect(shape(sections[0])).toEqual(['fa1', 'fa1>fa2', 'fa1>fa2>fa3']);

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
      mkRun({ id: 'pa2', dataSource: 's1', parentRunIds: ['pa1'], cacheSourceRunId: 'old', upstreamRunIds: ['pa1', 'old'], timestamp: 200 }),
      mkRun({ id: 'pa3', dataSource: 's1', parentRunIds: ['pa2'], timestamp: 300 }),
      // Family 2: fully-cached trunk — both branches land at section top level.
      mkRun({ id: 'qa1', dataSource: 's2', cacheSourceRunId: 'old', upstreamRunIds: ['old'], timestamp: 400 }),
      mkRun({ id: 'qa2', dataSource: 's2', parentRunIds: ['qa1'], cacheSourceRunId: 'old', upstreamRunIds: ['qa1', 'old'], timestamp: 500 }),
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

  it('re-anchors a cache-fed executed run under its cache point', () => {
    // A pipeline re-run where the head steps cache-hit and a later step
    // re-executes. The cache clones (lif2, basic2) are hidden; their real
    // upstream is the executed run they reused, the reuse edge that ends
    // their resolved upstreamRunIds — their parentRunIds point at sibling clones
    // that dead-end at a headless clone. The executed ash2 must nest under
    // its cache point (the lif1 → basic1 chain), not float as a phantom
    // root directly under the sample. Mirrors ashlar_stitch v3 in the field.
    runs.set([
      // Pipeline 1 — fully executed.
      mkRun({ id: 'lif1', method: 'lif', dataSource: 's', timestamp: 100 }),
      mkRun({ id: 'basic1', method: 'basic', dataSource: 's', parentRunIds: ['lif1'], timestamp: 110 }),
      // Pipeline 2 — heads cache-hit, tail re-executes.
      mkRun({ id: 'lif2', method: 'lif', dataSource: 's', cacheSourceRunId: 'lif1', parentRunIds: [], upstreamRunIds: ['lif1'], timestamp: 200 }),
      mkRun({ id: 'basic2', method: 'basic', dataSource: 's', cacheSourceRunId: 'basic1', parentRunIds: ['lif2'], upstreamRunIds: ['lif2', 'basic1'], timestamp: 210 }),
      mkRun({ id: 'ash2', method: 'ashlar', dataSource: 's', parentRunIds: ['lif2', 'basic2'], timestamp: 220 }),
    ]);
    const section = get(descendantForest).find(s => s.sample === 's')!;
    expect(shape(section)).toEqual(['lif1', 'lif1>basic1', 'lif1>basic1>ash2']);
  });
});

/**
 * Every entry of a forest as 'sample | key | parent count', in drawing
 * order: the whole forest, its structure and its order, in one comparable
 * value.
 */
function forestView(sections: DescendantSection[]): string[] {
  const out: string[] = [];
  const walk = (n: DescTreeNode, sample: string | null) => {
    out.push(`${sample} | ${n.key} | ${n.parentCount}`);
    n.children.forEach(c => walk(c, sample));
  };
  for (const s of sections) s.roots.forEach(r => walk(r, s.sample));
  return out;
}

/** Every entry of a forest, flattened. */
function entries(sections: DescendantSection[]): DescTreeNode[] {
  const out: DescTreeNode[] = [];
  const walk = (n: DescTreeNode) => { out.push(n); n.children.forEach(walk); };
  for (const s of sections) s.roots.forEach(walk);
  return out;
}

/**
 * The demo's shape: two segmentations, a quantification of each, then a
 * re-run in which every segmentation and quantification was a cache hit and
 * two comparisons executed. The cache hits are hidden; each carries its
 * input edge to the earlier cache hit and its reuse edge to the run it
 * reused, as the server's resolved upstreams do.
 */
function demoRuns(p: string, times: number[]): WfcRun[] {
  const t = (i: number) => times[i];
  return [
    mkRun({ id: `${p}s1`, method: 'segment', nid: 'cyto2', dataSource: 'demo', timestamp: t(0) }),
    mkRun({ id: `${p}s2`, method: 'segment', nid: 'tissuenet', dataSource: 'demo', timestamp: t(1) }),
    mkRun({ id: `${p}q1`, method: 'quantify', nid: 'cyto2', dataSource: 'demo', parentRunIds: [`${p}s1`], timestamp: t(2) }),
    mkRun({ id: `${p}q2`, method: 'quantify', nid: 'tissuenet', dataSource: 'demo', parentRunIds: [`${p}s2`], timestamp: t(3) }),
    mkRun({ id: `${p}s1c`, method: 'segment', nid: 'cyto2', dataSource: 'demo', parentRunIds: [],
            cacheSourceRunId: `${p}s1`, upstreamRunIds: [`${p}s1`], timestamp: t(4) }),
    mkRun({ id: `${p}s2c`, method: 'segment', nid: 'tissuenet', dataSource: 'demo', parentRunIds: [],
            cacheSourceRunId: `${p}s2`, upstreamRunIds: [`${p}s2`], timestamp: t(5) }),
    mkRun({ id: `${p}q1c`, method: 'quantify', nid: 'cyto2', dataSource: 'demo', parentRunIds: [`${p}s1c`],
            cacheSourceRunId: `${p}q1`, upstreamRunIds: [`${p}s1c`, `${p}q1`], timestamp: t(6) }),
    mkRun({ id: `${p}q2c`, method: 'quantify', nid: 'tissuenet', dataSource: 'demo', parentRunIds: [`${p}s2c`],
            cacheSourceRunId: `${p}q2`, upstreamRunIds: [`${p}s2c`, `${p}q2`], timestamp: t(7) }),
    mkRun({ id: `${p}cmp`, method: 'compare_segmentations', dataSource: 'demo',
            parentRunIds: [`${p}s1c`, `${p}s2c`], timestamp: t(8) }),
    mkRun({ id: `${p}kde`, method: 'plot_dna_content_comparison', dataSource: 'demo',
            parentRunIds: [`${p}q1c`, `${p}q2c`], timestamp: t(9) }),
  ];
}

describe('a run with several parents', () => {
  beforeEach(() => {
    selectRun(null);
    selectedRunIds.set(new Set());
  });

  it('a diamond: the child and its whole subtree appear under both parents, each marked "2 parents"', () => {
    runs.set([
      mkRun({ id: 'dm1', method: 'load', dataSource: 's8', timestamp: 100 }),
      mkRun({ id: 'dm2', method: 'seg_a', dataSource: 's8', parentRunIds: ['dm1'], timestamp: 200 }),
      mkRun({ id: 'dm3', method: 'seg_b', dataSource: 's8', parentRunIds: ['dm1'], timestamp: 210 }),
      mkRun({ id: 'dm4', method: 'compare', dataSource: 's8', parentRunIds: ['dm2', 'dm3'], timestamp: 300 }),
      mkRun({ id: 'dm5', method: 'plot', dataSource: 's8', parentRunIds: ['dm4'], timestamp: 400 }),
    ]);
    const [section] = get(descendantForest);
    expect(shape(section)).toEqual([
      'dm1',
      'dm1>dm2', 'dm1>dm2>dm4', 'dm1>dm2>dm4>dm5',
      'dm1>dm3', 'dm1>dm3>dm4', 'dm1>dm3>dm4>dm5',
    ]);
    const copies = entries([section]).filter(n => n.run.id === 'dm4');
    expect(copies.map(n => n.key)).toEqual(['dm1>dm2>dm4', 'dm1>dm3>dm4']);
    expect(copies.map(n => n.parentCount)).toEqual([2, 2]);
    // One parent below the child: no pill there.
    expect(entries([section]).filter(n => n.run.id === 'dm5').map(n => n.parentCount)).toEqual([1, 1]);
  });

  it('the demo: the comparison sits under both quantifications and never under a segmentation', () => {
    runs.set(demoRuns('dx', [100, 110, 200, 210, 300, 301, 310, 311, 400, 410]));
    const [section] = get(descendantForest);
    expect(section.sample).toBe('demo');
    expect(shape(section)).toEqual([
      'dxs1', 'dxs1>dxcmp', 'dxs1>dxq1', 'dxs1>dxq1>dxkde',
      'dxs2', 'dxs2>dxcmp', 'dxs2>dxq2', 'dxs2>dxq2>dxkde',
    ]);
    const kde = entries([section]).filter(n => n.run.id === 'dxkde');
    expect(kde.map(n => n.parentCount)).toEqual([2, 2]);
    // The walk through the hidden cache hits also reaches each
    // segmentation; the reduced parent set drops them.
    expect(kde.map(n => n.key.split('>').at(-2))).toEqual(['dxq1', 'dxq2']);
  });

  it('permuting the runs\' start times leaves the forest unchanged', () => {
    const base = [100, 110, 200, 210, 300, 301, 310, 311, 400, 410];
    const orders = [
      base,
      [...base].reverse(),
      [311, 100, 410, 200, 110, 400, 210, 300, 301, 310],
    ];
    const views = orders.map(times => {
      runs.set([
        ...demoRuns('pm', times),
        // A second sample whose run is newer or older than the demo's
        // depending on the permutation.
        mkRun({ id: 'pmz', method: 'load', dataSource: 'another', timestamp: times[9] + 1 }),
        mkRun({ id: 'pmy', method: 'load', dataSource: 'another', timestamp: times[0] - 1 }),
      ]);
      return forestView(get(descendantForest));
    });
    expect(views[0].length).toBeGreaterThan(0);
    expect(views[1]).toEqual(views[0]);
    expect(views[2]).toEqual(views[0]);
  });

  it('selecting one copy highlights every copy; folding one copy leaves the others open', () => {
    runs.set([
      mkRun({ id: 'hl1', method: 'load', dataSource: 's9', timestamp: 100 }),
      mkRun({ id: 'hl2', method: 'seg_a', dataSource: 's9', parentRunIds: ['hl1'], timestamp: 200 }),
      mkRun({ id: 'hl3', method: 'seg_b', dataSource: 's9', parentRunIds: ['hl1'], timestamp: 210 }),
      mkRun({ id: 'hl4', method: 'compare', dataSource: 's9', parentRunIds: ['hl2', 'hl3'], timestamp: 300 }),
      mkRun({ id: 'hl5', method: 'plot', dataSource: 's9', parentRunIds: ['hl4'], timestamp: 400 }),
    ]);
    const [section] = get(descendantForest);
    const highlighted = () =>
      entries([section]).filter(n => get(descendantHighlight).has(n.run.id)).map(n => n.key);

    // Selecting one entry selects the run, and the highlight goes by run.
    selectRun('hl4');
    expect(highlighted()).toEqual(['hl1>hl2>hl4', 'hl1>hl3>hl4']);
    // Select mode checks runs rather than opening one; the same holds.
    selectRun(null);
    filters.update(f => ({ ...f, selectMode: true }));
    toggleRunSelection('hl4');
    expect(highlighted()).toEqual(['hl1>hl2>hl4', 'hl1>hl3>hl4']);

    // Fold the first copy only.
    toggleDescendantCollapsed('hl1>hl2>hl4');
    const rows = descendantRows(section.roots, get(descendantsCollapsed));
    expect(rows.map(r => `${r.node.key}${r.collapsed ? ' (folded)' : ''}`)).toEqual([
      'hl1',
      'hl1>hl2', 'hl1>hl2>hl4 (folded)',
      'hl1>hl3', 'hl1>hl3>hl4', 'hl1>hl3>hl4>hl5',
    ]);
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
    // Fold state is keyed by entry position, not run id.
    collapseAllDescendants();
    expect(get(descendantsCollapsed)).toEqual(new Set(['ca1', 'ca1>ca2']));

    expandAllDescendants();
    expect(get(descendantsCollapsed).size).toBe(0);

    toggleDescendantCollapsed('ca1>ca2');
    expect(get(descendantsCollapsed)).toEqual(new Set(['ca1>ca2']));
    toggleDescendantCollapsed('ca1>ca2');
    expect(get(descendantsCollapsed).size).toBe(0);
  });
});

describe('the Descendants view scopes to one run', () => {
  // The scope and the view are module-scoped state, so this block resets
  // both before each of its own rows.
  beforeEach(() => {
    clearDescendantScope();
    historyView.set('descendants');
  });

  it('scoping shows the run and its descendants, from whichever view asked', () => {
    runs.set([
      mkRun({ id: 'sc1', dataSource: 's1', timestamp: 100 }),
      mkRun({ id: 'sc2', dataSource: 's1', parentRunIds: ['sc1'], timestamp: 200 }),
      mkRun({ id: 'sc3', dataSource: 's1', parentRunIds: ['sc2'], timestamp: 300 }),
      mkRun({ id: 'sc4', dataSource: 's1', parentRunIds: ['sc1'], timestamp: 250 }),
      mkRun({ id: 'sc9', dataSource: 's2', timestamp: 400 }),
    ]);
    // The button lives in the run detail panel, which is open beside any
    // view, so scoping switches the view as well as setting the scope.
    historyView.set('lineages');
    scopeDescendantsTo('sc2');

    expect(get(historyView)).toBe('descendants');
    expect(get(descendantScope)).toBe('sc2');
    const sections = get(scopedDescendantForest);
    // One section — the scoped run's own — holding the run and what ran
    // below it. Its parent, its sibling and the other sample are gone.
    expect(sections).toHaveLength(1);
    expect(sections[0].sample).toBe('s1');
    expect(shape(sections[0])).toEqual(['sc2', 'sc2>sc3']);
  });

  it('the scoped subtree holds no cache-hit copies; an executed run below one promotes', () => {
    runs.set([
      mkRun({ id: 'cs1', dataSource: 's3', timestamp: 100 }),
      mkRun({ id: 'cs2', dataSource: 's3', parentRunIds: ['cs1'], timestamp: 200 }),
      // A cache-hit child, and an executed grandchild fed by it.
      mkRun({ id: 'cs3', dataSource: 's3', parentRunIds: ['cs1'], cacheSourceRunId: 'old',
              upstreamRunIds: ['cs1', 'old'], timestamp: 300 }),
      mkRun({ id: 'cs4', dataSource: 's3', parentRunIds: ['cs3'], timestamp: 400 }),
    ]);
    scopeDescendantsTo('cs1');

    const sections = get(scopedDescendantForest);
    expect(sections).toHaveLength(1);
    // cs3 is a cache hit, so the scoped view never draws it; cs4 hangs off
    // the nearest run that actually executed.
    expect(shape(sections[0])).toEqual(['cs1', 'cs1>cs2', 'cs1>cs4']);
  });

  it('scoped to one parent, a multi-parent child shows its full subtree', () => {
    runs.set([
      mkRun({ id: 'sp1', method: 'load', dataSource: 's7', timestamp: 100 }),
      mkRun({ id: 'sp2', method: 'seg_a', dataSource: 's7', parentRunIds: ['sp1'], timestamp: 200 }),
      mkRun({ id: 'sp3', method: 'seg_b', dataSource: 's7', parentRunIds: ['sp1'], timestamp: 210 }),
      mkRun({ id: 'sp4', method: 'compare', dataSource: 's7', parentRunIds: ['sp2', 'sp3'], timestamp: 300 }),
      mkRun({ id: 'sp5', method: 'plot', dataSource: 's7', parentRunIds: ['sp4'], timestamp: 400 }),
    ]);
    // Scoped to either parent, the child is there with everything below it,
    // still marked as having two parents.
    for (const parent of ['sp2', 'sp3']) {
      scopeDescendantsTo(parent);
      const sections = get(scopedDescendantForest);
      expect(sections).toHaveLength(1);
      expect(shape(sections[0])).toEqual([parent, `${parent}>sp4`, `${parent}>sp4>sp5`]);
      expect(sections[0].roots[0].children[0].parentCount).toBe(2);
    }
  });

  it('clearing the scope brings back every section', () => {
    runs.set([
      mkRun({ id: 'sd1', dataSource: 's4', timestamp: 100 }),
      mkRun({ id: 'sd2', dataSource: 's4', parentRunIds: ['sd1'], timestamp: 200 }),
      mkRun({ id: 'sd9', dataSource: 's5', timestamp: 300 }),
    ]);
    scopeDescendantsTo('sd2');
    expect(get(scopedDescendantForest)).toHaveLength(1);

    clearDescendantScope();
    expect(get(descendantScope)).toBeNull();
    expect(get(scopedDescendantForest)).toEqual(get(descendantForest));
    expect(get(scopedDescendantForest)).toHaveLength(2);
  });

  it('scoping to a run this view never draws leaves the scoped view empty', () => {
    runs.set([
      mkRun({ id: 'se1', dataSource: 's6', timestamp: 100 }),
      mkRun({ id: 'se2', dataSource: 's6', parentRunIds: ['se1'], cacheSourceRunId: 'old',
              upstreamRunIds: ['se1', 'old'], timestamp: 200 }),
    ]);
    // se2 is a cache hit. The run detail panel offers "→ Descendants" for
    // it like any other run, and the view answers with its own empty state
    // rather than drawing the copy this view excludes by design.
    scopeDescendantsTo('se2');
    expect(get(scopedDescendantForest)).toEqual([]);
  });
});
