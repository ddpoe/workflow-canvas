/**
 * Vitest suite for the FilterBar dropdown cascade:
 *
 *   - availableMethods narrows to the selected module; setModuleFilter
 *     prunes selected methods that don't belong to the new module.
 *   - availableSamples narrows (lineage-aware) to samples with runs
 *     matching the module/methods filters; module/method changes prune
 *     selected samples the dropdown no longer offers.
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
  methodInfos,
  availableMethods,
  availableSamples,
  setModuleFilter,
  toggleMethodFilter,
} from '../historyStore';
import type { MethodInfo, WfcRun } from '../historyApi';

function mkMethod(name: string, module: string): MethodInfo {
  return { name, module, script_path: null, env: 'x' };
}

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
  methodInfos.set([]);
  resetFilters();
});

describe('methods dropdown cascade', () => {
  it('narrows to the selected module and prunes invalid method selections', () => {
    methodInfos.set([
      mkMethod('stitch', 'imaging'),
      mkMethod('segment', 'imaging'),
      mkMethod('aggregate', 'stats'),
    ]);
    expect(get(availableMethods)).toEqual(['aggregate', 'segment', 'stitch']);

    filters.update(f => ({ ...f, methods: ['aggregate', 'segment'] }));
    setModuleFilter('imaging');
    expect(get(availableMethods)).toEqual(['segment', 'stitch']);
    // 'aggregate' belongs to stats — pruned; 'segment' survives.
    expect(get(filters).methods).toEqual(['segment']);

    // Clearing the module restores the full list and keeps selections.
    setModuleFilter('');
    expect(get(availableMethods)).toEqual(['aggregate', 'segment', 'stitch']);
    expect(get(filters).methods).toEqual(['segment']);
  });
});

describe('samples dropdown cascade', () => {
  it('narrows lineage-aware to matching runs and prunes stale sample selections', () => {
    runs.set([
      // s1 lineage: imaging root, stats child.
      mkRun({ id: 'sc1', module: 'imaging', method: 'stitch', dataSource: 's1', timestamp: 100 }),
      mkRun({ id: 'sc2', module: 'stats', method: 'aggregate', dataSource: 's1', parentRunIds: ['sc1'], timestamp: 200 }),
      // s2 lineage: stats root only.
      mkRun({ id: 'sc3', module: 'stats', method: 'aggregate', dataSource: 's2', timestamp: 300 }),
    ]);
    expect(get(availableSamples)).toEqual(['s1', 's2']);

    // stats runs exist in both lineages — both samples stay offered.
    setModuleFilter('stats');
    expect(get(availableSamples)).toEqual(['s1', 's2']);

    // imaging runs exist only in the s1 lineage — s2 disappears and a
    // pre-selected s2 is pruned so no invisible filter remains.
    filters.update(f => ({ ...f, sample: ['s1', 's2'] }));
    setModuleFilter('imaging');
    expect(get(availableSamples)).toEqual(['s1']);
    expect(get(filters).sample).toEqual(['s1']);

    // Method toggles cascade the same way.
    setModuleFilter('');
    toggleMethodFilter('stitch');
    expect(get(availableSamples)).toEqual(['s1']);
  });
});
