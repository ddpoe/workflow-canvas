/**
 * Vitest suite for the Lineages view's path rows over the one relation.
 *
 * Records carry ``upstreamRunIds`` as the server resolves it: input edges in
 * slot order, then the reuse edge of a cache-hit row.
 *
 *   - views-path-rows: one row per terminal, and a filter that hides a
 *     middle run joins its path instead of splitting it.
 *   - views-rerun-labels: membership is the input edges, so a re-run's row
 *     holds the re-run's own runs and the originals keep their own row. The
 *     reuse edge is kept, and it is what labels a row node cached. A re-run
 *     in which every run was a cache hit still gets a row of its own,
 *     labeled cached from end to end.
 *
 * Note: run ids are unique per test — effectiveSample memoises by run id
 * across store updates (the cache is only cleared by loadRuns()).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { runs, filters, resetFilters, pathRows } from '../historyStore';
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
    dataSource: 's',
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

beforeEach(() => {
  runs.set([]);
  resetFilters();
});

describe('pathRows over the one relation', () => {
  it('views-path-rows: a filter hiding a middle run joins the path into one row', () => {
    runs.set([
      mkRun({ id: 'pa', method: 'load', timestamp: 100 }),
      mkRun({ id: 'pb', method: 'norm', parentRunIds: ['pa'], timestamp: 200 }),
      mkRun({ id: 'pc', method: 'score', parentRunIds: ['pb'], timestamp: 300 }),
      mkRun({ id: 'pe', method: 'load', timestamp: 350 }),
      mkRun({ id: 'pd', method: 'report', parentRunIds: ['pc', 'pe'], timestamp: 400 }),
    ]);
    filters.update(f => ({ ...f, methods: ['load', 'score', 'report'] }));

    const rows = get(pathRows).map(row => row.nodes.map(n => n.id));
    expect(rows).toEqual([['pa', 'pc', 'pe', 'pd']]);
  });

  it('views-rerun-labels: a re-run row is the re-run, its cache-hit runs labeled cached', () => {
    runs.set([
      // First run: A -> B -> C.
      mkRun({ id: 'la', method: 'load', timestamp: 100 }),
      mkRun({ id: 'lb', method: 'norm', parentRunIds: ['la'], timestamp: 200 }),
      mkRun({ id: 'lc', method: 'score', parentRunIds: ['lb'], timestamp: 300 }),
      // Re-run: A' and B' cache-hit A and B; C' executed with new parameters.
      mkRun({ id: 'la2', method: 'load', cacheSourceRunId: 'la', upstreamRunIds: ['la'], timestamp: 400 }),
      mkRun({ id: 'lb2', method: 'norm', parentRunIds: ['la2'], cacheSourceRunId: 'lb',
              upstreamRunIds: ['la2', 'lb'], timestamp: 410 }),
      mkRun({ id: 'lc2', method: 'score', parentRunIds: ['lb2'], timestamp: 420 }),
    ]);

    const byTerminal = new Map(
      get(pathRows).map(row => [row.nodes[row.nodes.length - 1].id, row.nodes]),
    );
    expect([...byTerminal.keys()].sort()).toEqual(['lc', 'lc2']);
    expect(byTerminal.get('lc')!.map(n => n.id)).toEqual(['la', 'lb', 'lc']);

    const rerun = byTerminal.get('lc2')!;
    // Membership is the input edges only, so the re-run's row is the
    // re-run: the originals it reused stay in their own row above and are
    // not repeated here.
    expect(rerun.map(n => n.id)).toEqual(['la2', 'lb2', 'lc2']);
    // The reuse edge is kept, and it is what labels a row node cached.
    expect(rerun.filter(n => n.cacheSourceRunId).map(n => [n.id, n.cacheSourceRunId]))
      .toEqual([['la2', 'la'], ['lb2', 'lb']]);
  });

  it('views-rerun-labels: a fully-cached re-run gets its own row, cached throughout', () => {
    runs.set([
      // First run: A -> B, both executed.
      mkRun({ id: 'fa', method: 'load', timestamp: 100 }),
      mkRun({ id: 'fb', method: 'norm', parentRunIds: ['fa'], timestamp: 200 }),
      // The re-run: every step a cache hit. Its head has no input edge at
      // all — only the reuse edge — so it roots the re-run's own row.
      mkRun({ id: 'fa2', method: 'load', cacheSourceRunId: 'fa', upstreamRunIds: ['fa'], timestamp: 300 }),
      mkRun({ id: 'fb2', method: 'norm', parentRunIds: ['fa2'], cacheSourceRunId: 'fb',
              upstreamRunIds: ['fa2', 'fb'], timestamp: 400 }),
    ]);

    // Two rows, newest first: the re-run, then the runs it reused. A
    // fully-cached re-run is still a re-run the user asked for, so a row
    // missing here would read as a missing run.
    expect(get(pathRows).map(row => row.nodes.map(n => n.id)))
      .toEqual([['fa2', 'fb2'], ['fa', 'fb']]);

    // Every node of that row carries a reuse edge, so it reads cached from
    // end to end rather than looking like a second copy of the first run.
    const rerun = get(pathRows).find(r => r.nodes.some(n => n.id === 'fb2'))!;
    expect(rerun.nodes.every(n => !!n.cacheSourceRunId)).toBe(true);
  });
});
