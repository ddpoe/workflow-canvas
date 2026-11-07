/**
 * The options picker's rules, without mounting it: which sections a row's
 * lookup answers become and in what order, which start open, the node chip,
 * the count, the ticks, the filter, and the chevron's state and hover.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { Edge } from '@xyflow/svelte';
import {
  chevronState, filterSection, fitsPattern, noMatch, openSectionKeys,
  optionSections, patternFixedPart, sectionsFold, tickedOptions,
  type OutputOptions, type ParamOptions, type UpstreamOptions,
} from '../options';
import { optionsLookupPlan, resolveOptionsLookup, type CanvasNode } from '../nodeEdits';
import { fetchOutputOptions } from '../api';
import { forgetModuleColors, rememberModuleColors } from '../../shared/moduleColor.svelte';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  fetchOutputOptions: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(fetchOutputOptions).mockReset();
  forgetModuleColors();
  rememberModuleColors([{ name: 'measure', color: '#06809C' }, { name: 'annot', color: '#6467B4' }]);
});

const CELLS: OutputOptions = {
  fixed: ['cell_id', 'area', 'perimeter'],
  built: [{ params: ['channels'], names: ['dapi_mean', 'gfp_mean', 'cell_id'] }],
  patterns: ['intensity_*', 'intensity_*'],
  all: ['cell_id', 'area', 'perimeter', 'dapi_mean', 'gfp_mean'],
};
const ANNOTATIONS: OutputOptions = {
  fixed: ['cell_id', 'cell_type', 'confidence'], built: [], patterns: [],
  all: ['cell_id', 'cell_type', 'confidence'],
};
const NONE: OutputOptions = { fixed: [], built: [], patterns: [], all: [] };

function upstream(
  nodeId: string, input: string, options: OutputOptions | null,
  extra: Partial<UpstreamOptions> = {},
): UpstreamOptions {
  return {
    input, nodeId, slot: 'table', nodeLabel: nodeId, kind: 'method',
    module: 'measure', method: nodeId, options, ...extra,
  };
}

const MEASURE = upstream('measure_cells', 'cells', CELLS);
const ANNOTATE = upstream('annotate', 'annotations', ANNOTATIONS, { module: 'annot' });

describe('sections follow the wired upstreams', () => {
  it('one section per upstream with options, in the order the lookup lists them', () => {
    const fanned = upstream('annotate_b', 'annotations', ANNOTATIONS, { module: 'annot' });
    const p: ParamOptions = {
      inputs: ['cells', 'annotations'],
      upstreams: [MEASURE, ANNOTATE, upstream('selector', 'cells', null, { kind: 'system' }), fanned,
        upstream('bare', 'cells', NONE)],
    };
    const sections = optionSections(p);
    expect(sections.map(s => [s.nodeId, s.input])).toEqual([
      ['measure_cells', 'cells'], ['annotate', 'annotations'], ['annotate_b', 'annotations'],
    ]);
    expect(sectionsFold(sections)).toBe(true);
  });

  it('a section names its node in its module colour and counts each option once', () => {
    const [s] = optionSections({ inputs: ['cells'], upstreams: [MEASURE] });
    expect(s.chip).toEqual({ text: 'measure_cells', color: '#06809C' });
    expect(s.built).toEqual([{ params: ['channels'], names: ['dapi_mean', 'gfp_mean'] }]);
    expect(s.patterns).toEqual(['intensity_*']);
    expect(s.count).toBe(6);
  });

  it('a Run Reference chip reads "<method> · run <id>" in the run method\'s module colour', () => {
    const run = upstream('ref', 'cells', { ...CELLS, module: 'annot', method: 'annotate' },
      { kind: 'run_reference', module: '', method: '', runId: '412' });
    const [s] = optionSections({ inputs: ['cells'], upstreams: [run] });
    expect(s.chip).toEqual({ text: 'annotate · run 412', color: '#6467B4' });
  });

  it('a single section has no fold chevron and is open', () => {
    const sections = optionSections({ inputs: ['cells'], upstreams: [MEASURE] });
    expect(sectionsFold(sections)).toBe(false);
    expect(openSectionKeys(sections, 'area')).toEqual([sections[0].key]);
  });
});

describe('which sections start open', () => {
  const sections = optionSections({ inputs: ['cells', 'annotations'], upstreams: [MEASURE, ANNOTATE] });
  const [m, a] = sections.map(s => s.key);

  it('the section holding the value opens and the rest are folded', () => {
    expect(openSectionKeys(sections, 'dapi_mean')).toEqual([m]);
    expect(openSectionKeys(sections, 'cell_type')).toEqual([a]);
    expect(openSectionKeys(sections, 'intensity_dapi')).toEqual([m]);
  });

  it('a value in both sections opens both; no value opens all', () => {
    expect(openSectionKeys(sections, 'cell_id')).toEqual([m, a]);
    expect(openSectionKeys(sections, '')).toEqual([m, a]);
  });
});

describe('ticks and the filter', () => {
  const [s] = optionSections({ inputs: ['cells'], upstreams: [MEASURE] });

  it('a glob matches any run of characters at a star, every other character literally, whole string', () => {
    expect(fitsPattern('intensity_dapi', 'intensity_*')).toBe(true);
    expect(fitsPattern('intensity_', 'intensity_*')).toBe(true);
    expect(fitsPattern('xintensity_dapi', 'intensity_*')).toBe(false);
    expect(fitsPattern('a.b', 'a.b')).toBe(true);
    expect(fitsPattern('axb', 'a.b')).toBe(false);
    expect(patternFixedPart('intensity_*')).toBe('intensity_');
  });

  it('a value equal to an option ticks it, one fitting a pattern ticks the pattern, any other ticks nothing', () => {
    expect(tickedOptions(s, 'area')).toEqual({ names: ['area'], patterns: [] });
    expect(tickedOptions(s, 'intensity_gfp')).toEqual({ names: [], patterns: ['intensity_*'] });
    expect(tickedOptions(s, 'volume')).toEqual({ names: [], patterns: [] });
  });

  it('typing filters names by substring and keeps the count; matching nothing is No match', () => {
    const f = filterSection(s, 'MEAN');
    expect(f.fixed).toEqual([]);
    expect(f.built).toEqual([{ params: ['channels'], names: ['dapi_mean', 'gfp_mean'] }]);
    expect(f.count).toBe(6);
    expect(filterSection(s, 'inten').patterns).toEqual(['intensity_*']);
    expect(noMatch([filterSection(s, 'mean')])).toBe(false);
    expect(noMatch([filterSection(s, 'zzz')])).toBe(true);
  });
});

describe('the chevron', () => {
  const ready = (p: ParamOptions, locked = false) => chevronState(p, optionSections(p), locked);

  it('opens the list when an upstream offers options', () => {
    expect(ready({ inputs: ['cells'], upstreams: [MEASURE] })).toEqual(
      { enabled: true, dimmed: false, hover: 'Show the options' });
  });

  it('is dimmed with nothing wired to any listed input', () => {
    expect(ready({ inputs: ['cells', 'annotations'], upstreams: [] })).toEqual(
      { enabled: false, dimmed: true, hover: 'Nothing wired to cells, annotations' });
  });

  it('is dimmed when nothing wired offers options, naming the nodes', () => {
    const p = { inputs: ['cells'], upstreams: [
      upstream('filter_cells', 'cells', NONE), upstream('Input Selector', 'cells', null, { kind: 'system' })] };
    expect(ready(p)).toEqual(
      { enabled: false, dimmed: true, hover: 'No options on filter_cells, Input Selector' });
    expect(ready(p, true).hover).toBe('No options on filter_cells, Input Selector');
  });

  it('is dimmed and disabled on a locked row', () => {
    expect(ready({ inputs: ['cells'], upstreams: [MEASURE] }, true)).toEqual(
      { enabled: false, dimmed: true, hover: 'Unlock the row to change it' });
  });

  it('is dimmed when the wired upstream is a draft, saying its options appear once it is registered', () => {
    const p = { inputs: ['cells'], upstreams: [
      upstream('measure_cells', 'cells', null, { draft: true }), upstream('filter_cells', 'cells', NONE)] };
    expect(ready(p)).toEqual(
      { enabled: false, dimmed: true, hover: 'Options appear once measure_cells is registered' });
  });
});

describe('a draft upstream is not asked for options', () => {
  function method(id: string, data: Record<string, unknown>): CanvasNode {
    return {
      id, position: { x: 0, y: 0 }, type: 'custom',
      data: { nodeType: 'method', label: id, module: 'measure', method: id, params: [], ...data },
    } as unknown as CanvasNode;
  }
  const consumer = method('plot', {
    method: 'plot', params: [{ name: 'column', type: 'string', options_from: 'cells' }] });
  const edge = (source: string): Edge =>
    ({ id: `${source}-plot`, source, target: 'plot', sourceHandle: 'table', targetHandle: 'cells' }) as Edge;
  const VERSIONS = [{ identity: 'v1', newest: true }];
  const pipeline = { name: '', nodes: [], links: [], samples: [] } as never;

  it.each([
    ['pinned to its draft', { version: 'draft', versions: VERSIONS, draft: { outputs: {} } }],
    ['a draft-only method', { versions: [], draft: { outputs: {} } }],
  ])('%s: no request, and the chevron names it', async (_case, data) => {
    const fetch = vi.mocked(fetchOutputOptions).mockResolvedValue(CELLS);
    const upstreamNode = method('measure_cells', data);
    const plan = optionsLookupPlan(consumer, consumer.data, [edge('measure_cells')],
      [consumer, upstreamNode], pipeline);
    expect(plan.column.upstreams[0]).toMatchObject({ draft: true, request: null });

    const answer = await resolveOptionsLookup(plan);
    expect(fetch).not.toHaveBeenCalled();
    expect(answer.column.upstreams[0].options).toBeNull();
    expect(chevronState(answer.column, optionSections(answer.column), false).hover)
      .toBe('Options appear once measure_cells is registered');
  });

  it('a registered upstream is asked with its pin', async () => {
    const fetch = vi.mocked(fetchOutputOptions).mockResolvedValue(CELLS);
    const upstreamNode = method('measure_cells', { version: 'v1', versions: VERSIONS });
    const plan = optionsLookupPlan(consumer, consumer.data, [edge('measure_cells')],
      [consumer, upstreamNode], pipeline);
    const answer = await resolveOptionsLookup(plan);
    expect(fetch).toHaveBeenCalledWith('measure.measure_cells',
      expect.objectContaining({ slot: 'table', version: 'v1' }));
    expect(answer.column.upstreams[0].options).toEqual(CELLS);
  });
});
