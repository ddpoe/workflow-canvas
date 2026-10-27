/**
 * The History Pipelines view as drawn: column headers, a status pill per
 * pipeline from the server's summary, the runs bar and its counts in words,
 * the row's actions, and an expanded pipeline's child rows.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import PipelinesView from '../PipelinesView.svelte';
import PipelineRow from '../PipelineRow.svelte';
import {
  runs,
  pipelineSummaries,
  pipelineRuns,
  expandedPipelineIds,
  selectedRunId,
} from '../historyStore';
import { modules } from '../../builder/stores';
import type { WfcRun } from '../historyApi';
import { get } from 'svelte/store';

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: 'r1',
    module: 'm',
    method: 'method',
    version: 1,
    versionIdentity: null,
    timestamp: 1,
    duration: 12,
    status: 'success',
    inputs: {},
    outputs: {},
    outputAvailability: {},
    metrics: {},
    dataSource: 'S1',
    parentRunIds: [],
    upstreamRunIds: [],
    parents: [],
    codeFingerprint: null,
    envName: null,
    envFingerprint: null,
    gitCommit: null,
    wfcVersion: null,
    pipelineDocHash: null,
    keyedUnderEarlierRule: false,
    paramsHash: null,
    inputsHash: null,
    paramTypes: {},
    experimentId: 'e',
    runName: 'run',
    nid: 'v1',
    user: 'u',
    favorite: false,
    pipelineId: 'p1',
    pipelineName: 'stitch-and-segment',
    scriptPath: null,
    ...partial,
  } as WfcRun;
}

beforeEach(() => {
  runs.set([]);
  pipelineSummaries.set([]);
  expandedPipelineIds.set(new Set());
  selectedRunId.set(null);
  modules.set([]);
});
afterEach(() => cleanup());

const texts = (els: NodeListOf<Element> | Element[]) =>
  Array.from(els).map(e => e.textContent?.trim());

describe('the Pipelines view list', () => {
  beforeEach(() => {
    runs.set([
      ...Array.from({ length: 4 }, (_, i) => mkRun({ id: `ran${i}`, status: 'success' })),
      ...Array.from({ length: 3 }, (_, i) =>
        mkRun({ id: `hit${i}`, status: 'success', cacheSourceRunId: 'orig' })),
      mkRun({ id: 'f1', status: 'failed' }),
      mkRun({ id: 'c1', status: 'cancelled', cancelReason: 'user' }),
      mkRun({ id: 'run1', status: 'running' }),
      mkRun({ id: 'q1', pipelineId: 'p2', pipelineName: 'qc-only', status: 'success', timestamp: 0 }),
    ]);
    pipelineSummaries.set([
      { pipelineId: 'p1', status: 'running', expected: 12, nodeOrder: null },
      { pipelineId: 'p2', status: 'succeeded', expected: 1, nodeOrder: null },
    ]);
  });

  it('names every column in a header row', () => {
    const { container } = render(PipelinesView);
    const header = container.querySelector('[data-testid="pipelines-header"]')!;
    expect(texts(header.children)).toEqual(
      ['Status', 'Pipeline', 'Runs', 'Samples', 'Started', 'Actions']);
  });

  it('gives each pipeline the status pill of its summary', () => {
    const { container } = render(PipelinesView);
    const pills = container.querySelectorAll('.pipeline-row .row-head .hist-pill');
    expect(texts(pills)).toEqual(['Running', '● Succeeded']);
    expect(pills[0].classList.contains('running')).toBe(true);
    expect(pills[1].classList.contains('succeeded')).toBe(true);
  });

  it('counts the runs in words, only the parts above zero, pending included', () => {
    const { container } = render(PipelinesView);
    const [first, second] = container.querySelectorAll('[data-testid="runs-text"]');
    expect(first.textContent?.trim()).toBe(
      '4 ran · 3 cached · 1 failed · 1 cancelled · 1 running · 2 pending');
    expect(second.textContent?.trim()).toBe('1 ran');
  });

  it('draws the runs bar segments in the fixed order', () => {
    const { container } = render(PipelinesView);
    const segments = container.querySelectorAll('.pipeline-row [data-testid="runs-bar"] [data-bucket]');
    expect(Array.from(segments).slice(0, 6).map(s => s.getAttribute('data-bucket')))
      .toEqual(['ran', 'cached', 'failed', 'cancelled', 'running', 'pending']);
  });

  it('keeps Open in Canvas on an ended row and puts the pipeline actions in a ⋯ menu', async () => {
    const { container, getByTestId } = render(PipelinesView);
    // A running row offers Watch in Canvas instead (pipelineRow.watch.test.ts).
    const head = [...container.querySelectorAll('.pipeline-row .row-head')]
      .find(h => !h.querySelector('.hist-pill.running'))!;
    expect(head.querySelector('button.open-btn')?.textContent?.trim()).toBe('Open in Canvas ↗');
    expect(head.querySelector('a.download-btn')).toBeNull();

    await fireEvent.click(head.querySelector('button.kebab')!);
    const menu = getByTestId('pipeline-menu');
    expect(texts(menu.querySelectorAll('[role="menuitem"]'))).toEqual(
      ['Open in Canvas ↗', 'Download pipeline JSON', 'Copy pipeline ID']);
  });
});

describe('an expanded pipeline', () => {
  beforeEach(() => {
    // Two nodes run the one method `segment`; the document puts load first.
    runs.set([
      mkRun({ id: '11', nodeId: 'n_seg2', method: 'segment', dataSource: 'S1', status: 'cancelled',
              cancelReason: 'upstream_failed', cancelledDueToRunId: '5',
              cancelledDueToMethod: 'load', cancelledDueToSample: 'S1', duration: 0 }),
      mkRun({ id: '5', nodeId: 'n_load', method: 'load', dataSource: 'S1', status: 'failed' }),
      mkRun({ id: '6', nodeId: 'n_load', method: 'load', dataSource: 'S2', status: 'success',
              cacheSourceRunId: '1', duration: 0 }),
      mkRun({ id: '7', nodeId: 'n_seg1', method: 'segment', dataSource: 'S2', status: 'success',
              version: 2, versionIdentity: 'seg-v2', duration: 41 }),
      mkRun({ id: '8', nodeId: 'n_seg1', method: 'segment', dataSource: 'S1', status: 'cancelled',
              cancelReason: 'not_run', version: 1, versionIdentity: 'seg-v1' }),
      mkRun({ id: '12', nodeId: 'n_seg2', method: 'segment', dataSource: 'S2', status: 'cancelled',
              cancelReason: 'user' }),
      mkRun({ id: '13', nodeId: 'n_seg2', method: 'segment', dataSource: 'S3', status: 'running' }),
    ]);
    pipelineSummaries.set([
      { pipelineId: 'p1', status: 'mixed', expected: 7, nodeOrder: ['n_load', 'n_seg1', 'n_seg2'] },
    ]);
    modules.set([{
      name: 'm', color: '#000', methods: [{
        name: 'segment', inputs: [], outputs: [], params: [],
        versions: [
          { label: 1, identity: 'seg-v1', newest: false },
          { label: 2, identity: 'seg-v2', newest: true },
        ],
      }],
    }] as never);
    expandedPipelineIds.set(new Set(['p1']));
  });

  function renderRow() {
    const row = get(pipelineRuns)[0];
    return render(PipelineRow, { props: { row, currentCanvasPipelineId: null } });
  }

  it('lists its runs under their own column headers, by step then sample', () => {
    const { container } = renderRow();
    const header = container.querySelector('[data-testid="child-header"]')!;
    expect(texts(header.children)).toEqual(
      ['Status', 'Step', 'Version', 'Sample', 'NID', 'Duration', 'Run']);
    const rows = container.querySelectorAll('.child-row');
    expect(Array.from(rows).map(r => r.getAttribute('data-run-id')))
      .toEqual(['5', '6', '8', '7', '11', '12', '13']);
    // The step is the method's name alone: two nodes of one method keep
    // their own rows, with no "#2".
    expect(texts(Array.from(rows).map(r => r.querySelector('.step')!)))
      .toEqual(['load', 'load', 'segment', 'segment', 'segment', 'segment', 'segment']);
  });

  it('shows each run pill, version tag with the newest dot, and a dash for cached and cancelled durations', () => {
    const { container } = renderRow();
    const row = (id: string) => container.querySelector(`.child-row[data-run-id="${id}"]`)!;
    expect(row('5').querySelector('.hist-pill')?.textContent?.trim()).toBe('● Failed');
    expect(row('6').querySelector('.hist-pill')?.textContent?.trim()).toBe('⟳ Cached');
    expect(row('7').querySelector('.hist-pill')?.textContent?.trim()).toBe('● Succeeded');
    expect(row('8').querySelector('.hist-pill')?.textContent?.trim()).toBe('● Cancelled');
    expect(row('13').querySelector('.hist-pill')?.textContent?.trim()).toBe('Running');
    expect(row('7').querySelector('.ver')?.textContent?.trim()).toBe('v2');
    expect(row('7').querySelector('.ver')?.classList.contains('newest')).toBe(true);
    expect(row('8').querySelector('.ver')?.classList.contains('newest')).toBe(false);
    expect(row('7').querySelector('.duration')?.textContent?.trim()).toBe('41.0s');
    expect(row('6').querySelector('.duration')?.textContent?.trim()).toBe('—');
    expect(row('8').querySelector('.duration')?.textContent?.trim()).toBe('—');
    expect(row('7').querySelector('.run-id')?.textContent?.trim()).toBe('r_7');
  });

  it('says why each cancelled run was cancelled, naming the failed step and sample before its id', async () => {
    const { container } = renderRow();
    const reason = (id: string) =>
      container.querySelector(`.child-row[data-run-id="${id}"] [role="tooltip"]`)!;
    expect(reason('11').textContent?.replace(/\s+/g, ' ').trim())
      .toBe('Upstream step failed: load on S1 r_5');
    expect(reason('8').textContent?.trim()).toBe('Not run: the pipeline ended before it started');
    expect(reason('12').textContent?.trim()).toBe('Stopped by you');

    await fireEvent.click(reason('11').querySelector('button.cause-link')!);
    expect(get(selectedRunId)).toBe('5');
  });

  it('never shows a failed run id on its own, and names a collapsed sample in words', () => {
    runs.set([
      mkRun({ id: '21', nodeId: 'n_a', status: 'cancelled', cancelReason: 'upstream_failed',
              cancelledDueToRunId: '99', cancelledDueToMethod: null, cancelledDueToSample: null }),
      mkRun({ id: '22', nodeId: 'n_b', status: 'cancelled', cancelReason: 'upstream_failed',
              cancelledDueToRunId: '5', cancelledDueToMethod: 'merge',
              cancelledDueToSample: '__all__' }),
    ]);
    const { container } = renderRow();
    const reason = (id: string) =>
      container.querySelector(`.child-row[data-run-id="${id}"] [role="tooltip"]`)!;
    expect(reason('21').textContent?.trim()).toBe('Upstream step failed');
    expect(reason('21').querySelector('button.cause-link')).toBeNull();
    expect(reason('22').textContent?.replace(/\s+/g, ' ').trim())
      .toBe('Upstream step failed: merge on all samples r_5');
  });
});
