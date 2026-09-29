/**
 * Component-mounting Vitest tests for the Runs Preview's cache-status view.
 *
 * The preview posts the canvas's pipeline document to
 * `POST /api/wfc/cache-status` and shows the route's answer: six statuses,
 * where each output of a source run is, and the action each row and each
 * output line will take. These tests load a real pipeline document through
 * `loadPipeline`, mount the real component with a stubbed `fetch` that
 * answers in the route's shape, lock (the statuses belong to a locked
 * state; see lockFlow.test.ts for the lock itself), and read what the user
 * sees.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import RunsPreview from '../RunsPreview.svelte';
import { loadPipeline } from '../pipeline';
import { lockNow, clearLock } from '../lockFlow';
import type { PipelineJSON } from '../../shared/types';
import type { CacheStatusResponse, CacheStatusRow } from '../runsPreviewStatus';

const SAMPLES = ['s1', 's2', 's3', 's4', 's5', 's6'];

/** A selector over six samples feeding m1, which feeds m2. */
const DOC: PipelineJSON = {
  name: 'preview',
  nodes: [
    { id: 's', type: 'input_selector', method: '', module: '', params: {}, samples: SAMPLES },
    { id: 'm1', type: 'method', method: 'filter', module: 'mod', params: {} },
    { id: 'm2', type: 'method', method: 'qc', module: 'mod', params: {} },
  ],
  links: [
    { source: 's', target: 'm1', targetHandle: 'data' },
    { source: 'm1', target: 'm2', targetHandle: 'data' },
  ],
  samples: SAMPLES,
};

function row(nodeId: string, sample: string, over: Partial<CacheStatusRow>): CacheStatusRow {
  return {
    key: `${nodeId}::${sample}::default`,
    node_id: nodeId,
    sample,
    variant: 'default',
    status: 'new_step_changed',
    reason: null,
    cache_key: null,
    source_run_id: null,
    source_nid: null,
    outputs: [],
    ...over,
  };
}

/** m1 carries every status once (two `new` reasons); m2 is all new. */
function mixedResponse(): CacheStatusResponse {
  return {
    blocked_reason: null,
    rows: [
      row('m1', 's1', {
        status: 'cached_local', source_run_id: 11, source_nid: 'filter_11',
        outputs: [{ slot: 'out', location: 'local' }, { slot: 'log', location: 'local' }],
      }),
      row('m1', 's2', {
        status: 'cached_remote', source_run_id: 12, source_nid: 'filter_12',
        outputs: [{ slot: 'out', location: 'local' }, { slot: 'log', location: 'remote' }],
      }),
      row('m1', 's3', {
        status: 'outputs_missing', source_run_id: 13, source_nid: 'filter_13',
        outputs: [{ slot: 'out', location: 'missing' }, { slot: 'log', location: 'local' }],
      }),
      row('m1', 's4', { status: 'new_step_changed' }),
      row('m1', 's5', { status: 'new_upstream_reruns' }),
      row('m1', 's6', {
        status: 'blocked',
        reason: "sample 's6' is unreachable: its content is neither in the local cache nor on the remote.",
      }),
      ...SAMPLES.map(s => row('m2', s, { status: 'new_upstream_reruns' })),
    ],
  };
}

let response: CacheStatusResponse;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  response = mixedResponse();
  fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => response,
    text: async () => '',
  } as unknown as Response));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  loadPipeline(DOC);
});

afterEach(() => {
  clearLock();
  cleanup();
  vi.restoreAllMocks();
});

/** The cell texts of the overview row for one method node. */
function overviewCells(container: HTMLElement, nodeId: string): string[] {
  const tr = container.querySelector(`tr.summary-row[data-method="${nodeId}"]`) as HTMLElement;
  expect(tr).not.toBeNull();
  return Array.from(tr.querySelectorAll('td')).map(td => td.textContent!.trim());
}

async function openMethod(container: HTMLElement, nodeId: string) {
  const tr = container.querySelector(`tr.summary-row[data-method="${nodeId}"]`) as HTMLElement;
  await fireEvent.click(tr);
  await waitFor(() => expect(container.querySelector('tr.run-row')).not.toBeNull());
}

function detailRow(container: HTMLElement, key: string): HTMLElement {
  const tr = container.querySelector(`tr.run-row[data-key="${key}"]`) as HTMLElement;
  expect(tr).not.toBeNull();
  return tr;
}

describe('RunsPreview cache status', () => {
  it('posts the pipeline document and tallies the overview and footer six ways', async () => {
    const { container } = render(RunsPreview);
    await lockNow();

    await waitFor(() => expect(overviewCells(container, 'm1')[2]).toBe('1'));

    // The request body is the run route's document, not a projection list.
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    expect(url).toBe('/api/wfc/cache-status');
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.projections).toBeUndefined();
    expect(body.nodes.map((n: { id: string }) => n.id)).toEqual(['s', 'm1', 'm2']);
    expect(body.samples).toEqual(SAMPLES);

    const headers = Array.from(container.querySelectorAll('table.summary th'))
      .map(th => th.textContent!.trim());
    expect(headers).toEqual([
      'Method', 'Runs', 'cached · local', 'cached · remote', 'outputs missing', 'new', 'blocked', '',
    ]);
    // Method | Runs | local | remote | missing | new (both reasons) | blocked | details
    expect(overviewCells(container, 'm1')).toEqual(['filter', '6', '1', '1', '1', '2', '1', 'details ›']);
    expect(overviewCells(container, 'm2')).toEqual(['qc', '6', '0', '0', '0', '6', '0', 'details ›']);

    const footer = within(container.querySelector('.footer') as HTMLElement);
    expect(footer.getByText('● 1 cached · local')).toBeTruthy();
    expect(footer.getByText('● 1 cached · remote')).toBeTruthy();
    expect(footer.getByText('● 1 outputs missing')).toBeTruthy();
    expect(footer.getByText('● 8 new')).toBeTruthy();
    expect(footer.getByText('● 1 blocked')).toBeTruthy();
    // outputs-missing re-runs and new rows run; cached and blocked do not.
    // m1::s6 is blocked, so Run is disabled with the reason as its tooltip.
    const run = footer.getByTestId('preview-run') as HTMLButtonElement;
    expect(run.textContent!.trim()).toBe('Run 9 jobs');
    expect(run.disabled).toBe(true);
    expect(run.title).toContain("sample 's6' is unreachable");
  });

  it('shows status, where, action and link per row, and expands outputs with their actions', async () => {
    const onOpenRun = vi.fn();
    const { container } = render(RunsPreview, { props: { onOpenRun } });
    await lockNow();
    await waitFor(() => expect(overviewCells(container, 'm1')[2]).toBe('1'));
    await openMethod(container, 'm1');

    const expectRow = (sample: string, status: string, where: string, action: string) => {
      const tr = detailRow(container, `m1::${sample}::default`);
      expect(tr.querySelector('.col-status .pill')!.textContent!.trim()).toBe(`● ${status}`);
      expect(tr.querySelector('.col-where')!.textContent!.trim()).toBe(where);
      expect(tr.querySelector('.col-action')!.textContent!.trim()).toBe(action);
    };
    expectRow('s1', 'cached · local', '2 local', 'skip');
    expectRow('s2', 'cached · remote', '1 local · 1 remote', 'skip · pulls');
    expectRow('s3', 'outputs missing', '1 local · 1 missing', 're-run');
    expectRow('s4', 'new · this step changed', '', 'will run');
    expectRow('s5', 'new · upstream re-runs', '', 'will run');
    expectRow('s6', 'blocked', '', "can't run");
    expect(detailRow(container, 'm1::s6::default').querySelector('.status-reason')!.textContent)
      .toContain("sample 's6' is unreachable");

    // Rows with outputs get a chevron; new and blocked rows have no breakdown.
    for (const s of ['s1', 's2', 's3']) {
      expect(detailRow(container, `m1::${s}::default`).querySelector('.btn-chevron')!.textContent).toBe('▸');
    }
    for (const s of ['s4', 's5', 's6']) {
      expect(detailRow(container, `m1::${s}::default`).querySelector('.btn-chevron')).toBeNull();
    }
    expect(container.querySelectorAll('tr.output-line')).toHaveLength(0);

    // Expand the remote row: one line per slot, location and action.
    await fireEvent.click(detailRow(container, 'm1::s2::default').querySelector('.btn-chevron')!);
    const lines = () => Array.from(container.querySelectorAll('tr.output-line')).map(tr => [
      tr.querySelector('.out-slot')!.textContent!.trim(),
      tr.querySelector('.out-location')!.textContent!.trim(),
      tr.querySelector('.out-action')!.textContent!.trim(),
    ]);
    expect(lines()).toEqual([['out', 'local', 'read here'], ['log', 'remote', 'pull']]);
    expect(detailRow(container, 'm1::s2::default').querySelector('.btn-chevron')!.textContent).toBe('▾');

    await fireEvent.click(detailRow(container, 'm1::s3::default').querySelector('.btn-chevron')!);
    expect(lines()).toEqual([
      ['out', 'local', 'read here'], ['log', 'remote', 'pull'],
      ['out', 'missing', 'recompute'], ['log', 'local', 'read here'],
    ]);

    // Collapse again.
    await fireEvent.click(detailRow(container, 'm1::s2::default').querySelector('.btn-chevron')!);
    expect(lines()).toEqual([['out', 'missing', 'recompute'], ['log', 'local', 'read here']]);

    // The NID of a row with a source run opens that run in History.
    const link = detailRow(container, 'm1::s3::default').querySelector('.nid-link') as HTMLElement;
    expect(link.textContent!.trim()).toBe('filter_13');
    await fireEvent.click(link);
    expect(onOpenRun).toHaveBeenCalledWith('13');
    expect(detailRow(container, 'm1::s4::default').querySelector('.nid-link')).toBeNull();
  });

  it('blocks every projected row with the one pipeline-level reason when the document is refused', async () => {
    const reason = 'the pipeline is refused: node m1 has two edges into slot data';
    response = { rows: [], blocked_reason: reason };
    const { container } = render(RunsPreview);
    await lockNow();

    await waitFor(() => expect(overviewCells(container, 'm1')[6]).toBe('6'));
    expect(overviewCells(container, 'm1')).toEqual(['filter', '6', '0', '0', '0', '0', '6', 'details ›']);
    expect(overviewCells(container, 'm2')).toEqual(['qc', '6', '0', '0', '0', '0', '6', 'details ›']);
    const footer = within(container.querySelector('.footer') as HTMLElement);
    expect(footer.getByText('● 12 blocked')).toBeTruthy();

    await openMethod(container, 'm2');
    const rows = Array.from(container.querySelectorAll('tr.run-row'));
    expect(rows).toHaveLength(6);
    for (const tr of rows) {
      expect(tr.querySelector('.col-status .pill')!.textContent!.trim()).toBe('● blocked');
      expect(tr.querySelector('.status-reason')!.textContent!.trim()).toBe(reason);
      expect(tr.querySelector('.col-action')!.textContent!.trim()).toBe("can't run");
    }
  });

  it('blocks a projected row the route gave no row for, saying so, and never shows it as new', async () => {
    // The route answers every row but m1::s4 (a `new` row in the full answer).
    response = { ...mixedResponse() };
    response.rows = response.rows.filter(r => r.key !== 'm1::s4::default');
    const { container } = render(RunsPreview);
    await lockNow();

    await waitFor(() => expect(overviewCells(container, 'm1')[2]).toBe('1'));
    // m1: local 1, remote 1, missing 1, new 1 (only s5 now), blocked 2 (s6 and the unanswered s4).
    expect(overviewCells(container, 'm1')).toEqual(['filter', '6', '1', '1', '1', '1', '2', 'details ›']);

    await openMethod(container, 'm1');
    const tr = detailRow(container, 'm1::s4::default');
    expect(tr.querySelector('.col-status .pill')!.textContent!.trim()).toBe('● blocked');
    expect(tr.querySelector('.status-reason')!.textContent!.trim())
      .toBe('the engine reported no status for this row');
    expect(tr.querySelector('.col-action')!.textContent!.trim()).toBe("can't run");
  });

  it.each([
    ['a non-2xx answer', async () => ({
      ok: false, status: 500, json: async () => ({}), text: async () => 'boom',
    } as unknown as Response), 'status 500: boom'],
    ['a wire failure', async () => { throw new TypeError('Failed to fetch'); }, 'Failed to fetch'],
  ])('blocks every row as "cache status is unavailable" on %s, never new', async (_what, impl, why) => {
    fetchMock.mockImplementation(impl);
    const { container } = render(RunsPreview);
    await lockNow();

    await waitFor(() => expect(overviewCells(container, 'm1')[6]).toBe('6'));
    expect(overviewCells(container, 'm1')).toEqual(['filter', '6', '0', '0', '0', '0', '6', 'details ›']);
    expect(overviewCells(container, 'm2')).toEqual(['qc', '6', '0', '0', '0', '0', '6', 'details ›']);
    const footer = within(container.querySelector('.footer') as HTMLElement);
    expect(footer.getByText('● 12 blocked')).toBeTruthy();
    expect(footer.getByText('● 0 new')).toBeTruthy();

    await openMethod(container, 'm1');
    const rows = Array.from(container.querySelectorAll('tr.run-row'));
    expect(rows).toHaveLength(6);
    for (const tr of rows) {
      expect(tr.querySelector('.col-status .pill')!.textContent!.trim()).toBe('● blocked');
      expect(tr.querySelector('.status-reason')!.textContent!.trim())
        .toBe(`cache status is unavailable (${why})`);
    }
  });
});
