/**
 * Draft runs in History (US-2): the Pipelines view's DRAFT rows beside the
 * recorded runs and the one greyed line once they are cleared, the Draft
 * status chip and tally, a draft step's place in Descendants (a sibling
 * branch under the run it read), and the draft step's run panel.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { flushSync } from 'svelte';
import PipelineRow from '../PipelineRow.svelte';
import DraftStepPanel from '../DraftStepPanel.svelte';
import PathNodeCard from '../PathNodeCard.svelte';
import DescendantsView from '../DescendantsView.svelte';
import ExportPreview from '../ExportPreview.svelte';
import { draftStepRun } from '../historyDrafts';
import { DRAFT_NODE_HELP, downstreamHelp } from '../../builder/drafts';
import {
  selectedRunId,
  selectedRunIds,
  descendantsCollapsed,
  runs,
  draftSteps,
  pipelineSummaries,
  pipelineRuns,
  expandedPipelineIds,
  filters,
  filteredRuns,
  statusBuckets,
  descendantForest,
  resetFilters,
  openRunOn,
} from '../historyStore';
import { modules } from '../../builder/stores';
import type { DraftStep, PipelineSummary, WfcRun } from '../historyApi';

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: 'r1', module: 'demo', method: 'filter_cells', version: 1, versionIdentity: null,
    timestamp: 1000, duration: 3, status: 'success', inputs: {}, outputs: {},
    outputAvailability: {}, metrics: {}, dataSource: 'ctrl_01', parentRunIds: [],
    upstreamRunIds: [], parents: [], codeFingerprint: null, envName: null,
    envFingerprint: null, gitCommit: null, wfcVersion: null, pipelineDocHash: null,
    keyedUnderEarlierRule: false, paramsHash: null, inputsHash: null, paramTypes: {},
    experimentId: 'e', runName: 'run', nid: 'v1', user: 'u', favorite: false,
    pipelineId: 'p1', pipelineName: 'demo', scriptPath: null, nodeId: 'n1',
    ...partial,
  } as WfcRun;
}

function mkStep(partial: Partial<DraftStep>): DraftStep {
  const method = partial.method ?? 'label';
  const sample = partial.sample ?? 'ctrl_01';
  const nodeId = partial.nodeId ?? 'n2';
  return {
    draftString: `draft:demo.label/${nodeId}@${sample}`, module: 'demo', method, nodeId,
    sample, draft: 'demo.label', isDraft: true, status: 'success', timestamp: 2000,
    duration: 2.4, pipelineId: 'p1', pipelineName: 'demo', nid: 'v1', version: null,
    parentRunIds: ['r1'], parentDraftSteps: [], ...partial,
  } as DraftStep;
}

function summary(partial: Partial<PipelineSummary>): PipelineSummary {
  return {
    pipelineId: 'p1', status: 'mixed', expected: 1, nodeOrder: ['n1', 'n2', 'n3'],
    draftMode: true, drafts: ['demo.label'], draftStepCount: 3, draftSteps: [], cleared: null,
    ...partial,
  } as PipelineSummary;
}

const draft = mkStep({});
const downOk = mkStep({
  draftString: 'draft:demo.label/n3@ctrl_01', method: 'summarize', nodeId: 'n3', isDraft: false,
  version: 1, parentRunIds: [], parentDraftSteps: [draft.draftString],
});
const downFailed = mkStep({
  draftString: 'draft:demo.label/n3@treat_02', method: 'summarize', nodeId: 'n3', sample: 'treat_02',
  isDraft: false, version: 1, status: 'failed', parentRunIds: [], parentDraftSteps: [],
});

beforeEach(() => {
  resetFilters();
  runs.set([mkRun({})]);
  draftSteps.set([]);
  pipelineSummaries.set([]);
  expandedPipelineIds.set(new Set());
  modules.set([]);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Pipelines: DRAFT rows and the cleared line', () => {
  it('lists the draft steps as DRAFT rows beside the recorded runs, in step order', () => {
    pipelineSummaries.set([summary({ draftSteps: [downFailed, downOk, draft] })]);
    expandedPipelineIds.set(new Set(['p1']));
    const row = get(pipelineRuns)[0];
    expect(row.draftMode).toBe(true);
    expect(row.draftSteps.map(d => d.draftString)).toEqual(
      [draft.draftString, downOk.draftString, downFailed.draftString]);

    const { container } = render(PipelineRow, { props: { row, currentCanvasPipelineId: null } });
    expect(container.querySelector('[data-testid="draft-mode-pill"]')?.textContent).toBe('draft mode');
    const rows = Array.from(container.querySelectorAll('[data-testid="draft-row"]'));
    expect(rows.map(r => r.querySelector('[data-testid="draft-pill"]')!.textContent))
      .toEqual(['● Draft · ok', '● Draft · ok', '● Draft · failed']);
    // Only the draft's own step shows ● DRAFT; the downstream ones their version.
    expect(rows[0].querySelector('[data-testid="draft-version"]')?.textContent).toBe('● DRAFT');
    expect(rows[1].querySelector('[data-testid="draft-version"]')).toBeNull();
    expect(rows[1].textContent).toContain('v1');
    // The NID is shown; the Run cell reads "draft" and copies the draft string.
    expect(rows[0].querySelector('.nid')?.textContent).toBe('v1');
    const copy = rows[0].querySelector('[data-testid="draft-copy"]') as HTMLElement;
    expect(copy.textContent).toBe('draft');
    expect(copy.getAttribute('title')).toBe(`Copy ${draft.draftString}`);
    // Recorded runs count as before; the draft steps after them in words.
    expect(container.querySelector('[data-testid="runs-text"]')?.textContent?.trim())
      .toBe('1 ran · 3 draft');
    expect(container.querySelector('[data-testid="cleared-line"]')).toBeNull();
  });

  it('keeps one greyed line once the draft steps are cleared, and counts them as cleared', () => {
    const text = '9 draft steps cleared: replaced by the draft run of 14:31.';
    pipelineSummaries.set([summary({
      status: 'succeeded',
      cleared: { count: 9, text, drafts: [{ draft: 'demo.label', reason: 'replaced', at: '2026-11-04T14:31:00Z' }] },
    })]);
    expandedPipelineIds.set(new Set(['p1']));
    const { container } = render(PipelineRow, {
      props: { row: get(pipelineRuns)[0], currentCanvasPipelineId: null },
    });
    expect(container.querySelectorAll('[data-testid="cleared-line"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="cleared-line"]')?.textContent).toContain(text);
    expect(container.querySelectorAll('[data-testid="draft-row"]')).toHaveLength(0);
    expect(container.querySelector('[data-testid="runs-text"]')?.textContent?.trim())
      .toBe('1 ran · 9 cleared');
    expect(container.querySelector('.seg[data-bucket="cleared"]')).not.toBeNull();
  });

  it('lists a pipeline that recorded nothing while its draft steps exist', () => {
    runs.set([]);
    pipelineSummaries.set([summary({ pipelineId: 'p9', name: 'try-it', draftSteps: [mkStep({ pipelineId: 'p9' })] })]);
    const rows = get(pipelineRuns);
    expect(rows.map(r => [r.pipelineId, r.name, r.draftSteps.length])).toEqual([['p9', 'try-it', 1]]);
  });
});

describe('the Draft status chip and the tally', () => {
  beforeEach(() => draftSteps.set([draft, downOk, downFailed]));

  it('counts draft steps in their status and in draft', () => {
    const b = get(statusBuckets);
    expect(b.draft).toBe(3);
    expect(b.success).toBe(3); // the recorded run and two draft steps
    expect(b.failed).toBe(1);
  });

  it('the Draft chip alone keeps only the draft steps', () => {
    filters.update(f => ({ ...f, statuses: ['draft'] }));
    expect(get(filteredRuns).map(r => r.id).sort()).toEqual(
      [draft.draftString, downOk.draftString, downFailed.draftString].sort());
    filters.update(f => ({ ...f, statuses: ['success'] }));
    expect(get(filteredRuns).every(r => r.status === 'success')).toBe(true);
  });
});

describe('Descendants: the draft run is a sibling branch', () => {
  it('hangs the draft under the run it read, beside that run\'s recorded child, and its downstream under it', () => {
    runs.set([mkRun({}), mkRun({ id: 'r2', method: 'label', parentRunIds: ['r1'], upstreamRunIds: ['r1'], nodeId: 'n2' })]);
    draftSteps.set([draft, downOk]);
    const [section] = get(descendantForest);
    const [root] = section.roots;
    expect(root.run.id).toBe('r1');
    const kids = root.children.map(c => c.run.id).sort();
    expect(kids).toEqual([draft.draftString, 'r2'].sort());
    const draftNode = root.children.find(c => c.run.id === draft.draftString)!;
    expect(draftNode.children.map(c => c.run.id)).toEqual([downOk.draftString]);
    // The recorded label run owns no draft child.
    expect(root.children.find(c => c.run.id === 'r2')!.children).toEqual([]);
  });
});

describe('Lineages: draft-mode cards', () => {
  beforeEach(() => { selectedRunId.set(null); });

  it('marks the draft with the solid DRAFT MODE flag and a draft pill: no star, no run id, a neutral border', () => {
    const { container } = render(PathNodeCard, { props: { run: draftStepRun(draft) } });
    const card = container.querySelector('.path-node') as HTMLElement;
    const flag = container.querySelector('[data-testid="draft-flag"]') as HTMLElement;
    expect(flag.textContent).toContain('DRAFT MODE');
    expect(flag.classList.contains('strong')).toBe(true);
    expect(container.querySelector('[data-testid="draft-tip"]')?.textContent).toContain(DRAFT_NODE_HELP);
    expect(container.querySelector('.star')).toBeNull();
    expect(container.querySelector('[data-testid="draft-nid"]')?.textContent?.trim()).toBe('draft');
    expect(container.querySelector('.sample')?.textContent).toBe('ctrl_01 · v1');
    expect(container.textContent).not.toContain('not recorded');
    expect(container.textContent).not.toContain(draft.draftString);
    // A succeeded draft-mode step keeps the neutral border.
    expect(card.classList.contains('dash-2')).toBe(false);
    expect(card.style.getPropertyValue('--glow-c')).toBe('var(--border)');
  });

  it('dashes a failed downstream card in red with the outline flag, the owner named in its tip', () => {
    const failed = { ...downFailed, error_message: 'exit 1\ntrace' };
    const { container } = render(PathNodeCard, { props: { run: draftStepRun(failed) } });
    const card = container.querySelector('.path-node') as HTMLElement;
    expect(card.classList.contains('dash-2')).toBe(true);
    expect(card.style.getPropertyValue('--dash-color')).toBe('var(--status-failed)');
    expect(container.querySelector('.acc')).not.toBeNull();
    expect(container.querySelector('[data-testid="draft-flag"]')!.classList.contains('strong')).toBe(false);
    expect(container.querySelector('[data-testid="draft-tip"]')?.textContent).toContain(downstreamHelp('label'));
    expect(container.querySelector('[data-testid="draft-nid"]')?.textContent?.trim()).toBe('v1');
    // The card says it failed, not why: the message stays in the step's panel.
    expect(container.querySelector('[data-testid="draft-why"]')?.textContent).toBe('✗ Failed');
  });

  it('glows near-white when the selected draft card succeeded, in its status colour when it failed', () => {
    const ok = render(PathNodeCard, { props: { run: draftStepRun(draft) } }).container.querySelector('.path-node') as HTMLElement;
    const bad = render(PathNodeCard, { props: { run: draftStepRun(downFailed) } }).container.querySelector('.path-node') as HTMLElement;
    selectedRunId.set(draft.draftString);
    flushSync();
    expect(ok.classList.contains('sel-glow')).toBe(true);
    expect(ok.classList.contains('glow-neutral')).toBe(true);
    selectedRunId.set(downFailed.draftString);
    flushSync();
    expect(bad.classList.contains('sel-glow')).toBe(true);
    expect(bad.classList.contains('glow-neutral')).toBe(false);
  });
});

describe('Descendants: draft-mode nodes', () => {
  it('draws NAME/METHOD pills, the light strip on the draft and the darker strip downstream, with no star', () => {
    descendantsCollapsed.set(new Set());
    draftSteps.set([draft, downOk]);
    const { container } = render(DescendantsView);
    const cards = Array.from(container.querySelectorAll('.tree-card')) as HTMLElement[];
    const recorded = cards.find(c => !c.classList.contains('draft-node'))!;
    const [onDraft, downstream] = cards.filter(c => c.classList.contains('draft-node'));
    expect(recorded.querySelector('.star')).not.toBeNull();
    expect(recorded.querySelector('.card-version')?.textContent).toBe('v1');
    expect(recorded.querySelector('[data-testid="draft-strip"]')).toBeNull();

    expect(onDraft.querySelector('.card-method')?.textContent).toBe('label');
    expect(onDraft.querySelector('.star')).toBeNull();
    expect(onDraft.querySelector('.card-nid')?.textContent).toBe('v1');
    expect(onDraft.querySelector('.card-version')?.textContent).toBe('DRAFT');
    const strip = onDraft.querySelector('[data-testid="draft-strip"]') as HTMLElement;
    expect(strip.classList.contains('on')).toBe(true);
    expect(strip.textContent).toContain(DRAFT_NODE_HELP);

    expect(downstream.querySelector('.card-version')?.textContent).toBe('v1');
    expect(downstream.querySelector('[data-testid="draft-strip"]')!.classList.contains('down')).toBe(true);
    // All succeeded: no dashes.
    expect(cards.some(c => c.classList.contains('dash-2'))).toBe(false);
  });
});

describe('select-mode Export Artifacts with a draft step selected', () => {
  it('asks only for the recorded runs and says the draft step was left out', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      run_count: 1, total_count: 2, total_size_bytes: 10, method_count: 1, by_type: [], by_method: [],
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    selectedRunIds.set(new Set(['r1', draft.draftString]));
    const { findByTestId } = render(ExportPreview, { props: { onClose: () => {} } });
    expect((await findByTestId('export-draft-note')).textContent).toContain('1 draft step left out');
    const sent = fetchMock.mock.calls.map(c => `${String(c[0])} ${String(c[1]?.body ?? '')}`).join('\n');
    expect(sent).toContain('r1');
    expect(sent).not.toContain('draft:');
  });
});

describe('the draft step run panel', () => {
  it('opens on Output when asked (✗ Failed on its card), on mount and when already open', async () => {
    const detail = {
      step: downFailed, versionText: 'v1', folder: 'f', keptUntil: 'k', params: {}, metrics: {},
      inputs: [], outputs: [], artifacts: [], log: null,
    };
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(detail), { status: 200 })));
    const outputTab = (c: HTMLElement) => [...c.querySelectorAll('.tab')].find(b => b.textContent?.startsWith('Output')) as HTMLElement;

    openRunOn(downFailed.draftString, 'output');
    const first = render(DraftStepPanel, { props: { draftRef: downFailed.draftString } });
    await waitFor(() => expect(outputTab(first.container)?.classList.contains('active')).toBe(true));
    cleanup();

    const open = render(DraftStepPanel, { props: { draftRef: downFailed.draftString } });
    await waitFor(() => expect(outputTab(open.container)).toBeDefined());
    expect(outputTab(open.container).classList.contains('active')).toBe(false);
    openRunOn(downFailed.draftString, 'output');
    await waitFor(() => expect(outputTab(open.container).classList.contains('active')).toBe(true));
  });


  it('shows History › draft, the draft string, version DRAFT, the folder and Kept until; Rename and the bundle export are disabled', async () => {
    const detail = {
      step: draft, versionText: 'DRAFT', folder: 'C:\\lab\\src\\demo\\label',
      keptUntil: 'label’s draft runs again', params: { k: 2 }, metrics: {}, inputs: [
        { slot: 'data', source: 'run', runId: '4', sourceSlot: 'filtered' },
      ], outputs: [{ slot: 'labeled', filename: 'labeled.csv', sizeBytes: 2048 }], artifacts: [], log: null,
    };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify(detail), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { container, getByTestId, queryByText } = render(DraftStepPanel, { props: { draftRef: draft.draftString } });
    await waitFor(() => expect(getByTestId('draft-string').textContent).toBe(draft.draftString));
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `/api/wfc/draft-step?ref=${encodeURIComponent(draft.draftString)}`);
    expect(container.querySelector('.crumb')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('History › draft');
    expect(getByTestId('draft-method-version').textContent).toBe('DRAFT');
    expect(getByTestId('draft-folder').getAttribute('title')).toBe('C:\\lab\\src\\demo\\label');
    expect(getByTestId('draft-kept-until').textContent).toBe('label’s draft runs again');
    expect(container.textContent).toContain('reads r_4 filtered -> data');
    const exportRun = getByTestId('draft-export-run') as HTMLButtonElement;
    expect(exportRun.disabled).toBe(true);
    expect(exportRun.title).toBe(
      'This lineage has a draft step, label (DRAFT). Register it and run demo again to export a bundle.');
    const rename = Array.from(container.querySelectorAll('button')).find(b => b.textContent?.includes('Rename')) as HTMLButtonElement;
    expect(rename.disabled).toBe(true);
    expect(getByTestId('draft-open-registry').textContent).toBe('Open in Registry ↗');
    expect(getByTestId('draft-view-pipeline').textContent).toBe('View pipeline ↗');
    expect(queryByText(/Open in Canvas|Reference in Canvas/)).toBeNull();
  });
});
