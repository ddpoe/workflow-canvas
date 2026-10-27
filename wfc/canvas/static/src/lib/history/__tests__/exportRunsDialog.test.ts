/**
 * History's Export run / Export runs dialog: dry run first, then download.
 *
 * The dialog asks the bundle route for the plan (`?dryRun=true`) with the
 * "Include outputs" and "Include images" options, both off on open; changing
 * either asks for a fresh plan. A refused plan lists every reason under the
 * plan's own refused lead and downloads nothing. An ok plan shows the plan's
 * own warning lines (a never-pushed image, none when images are included)
 * and an Export button; only that button sends the download, carrying the
 * plan's token and options. Closing the dialog while it plans or downloads
 * saves nothing. Component test over the real dialog; the only boundary stub
 * is `fetch` (and the browser's object-URL and anchor click, which jsdom
 * lacks).
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import ExportRunsDialog from '../ExportRunsDialog.svelte';
import type { ExportRunsPlan } from '../historyApi';

const ROUTE = '/api/wfc/export-runs';
const REFUSED_TEXT = 'The bundle cannot be complete. Nothing was written.';
const LOCAL_ONLY_LINE = 'The image wfc/env-a:1 (env env-a), used by step 1 (run 5), step 2 (run 7), was never '
  + 'pushed to a registry. The bundle points at an image only this machine holds; once it '
  + 'is deleted, those steps cannot be rerun or compared.';
const LOCAL_ONLY_WARNINGS = [
  { run_id: 5, env: 'env-a', image: 'wfc/env-a:1', state: 'never-pushed' },
  { run_id: 7, env: 'env-a', image: 'wfc/env-a:1', state: 'never-pushed' },
];

function mkPlan(partial: Partial<ExportRunsPlan>): ExportRunsPlan {
  return {
    requested: [7],
    steps: [
      { number: 1, run_id: 5, method: 'mod.load', sample: 'S1', parent_run_ids: [], image: 'wfc/env-a:1', pullable_ref: null },
      { number: 2, run_id: 7, method: 'mod.plot', sample: 'S1', parent_run_ids: [5], image: 'wfc/env-a:1', pullable_ref: null },
    ],
    substitutions: [],
    refusals: [],
    warnings: [],
    warning_lines: [],
    refused_text: REFUSED_TEXT,
    with_outputs: false,
    with_image: false,
    ok: true,
    token: 'tok-123',
    ...partial,
  };
}

type Body = Record<string, unknown>;

/**
 * The plan the server answers for a request's options: the image is
 * never-pushed, so the plan warns unless images are included (as Run bundle's
 * plan does), and the token names the options.
 */
function planForOptions(body: Body): ExportRunsPlan {
  const withImage = body.with_image === true;
  const withOutputs = body.with_outputs === true;
  return mkPlan({
    with_outputs: withOutputs,
    with_image: withImage,
    warnings: withImage ? [] : LOCAL_ONLY_WARNINGS,
    warning_lines: withImage ? [] : [LOCAL_ONLY_LINE],
    token: `tok-o${Number(withOutputs)}-i${Number(withImage)}`,
  });
}

interface Served {
  calls: { url: string; body: Body }[];
}

interface DownloadAnswer {
  status?: number;
  body?: unknown;
  /** The download answers only once this settles. */
  until?: Promise<unknown>;
}

function serve(
  plans: ExportRunsPlan | ((body: Body) => ExportRunsPlan | Promise<ExportRunsPlan>),
  download: DownloadAnswer = {},
): Served {
  const served: Served = { calls: [] };
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const body = JSON.parse(String(init?.body ?? '{}')) as Body;
    served.calls.push({ url, body });
    if (url === `${ROUTE}?dryRun=true`) {
      const plan = typeof plans === 'function' ? await plans(body) : plans;
      return { ok: true, status: 200, json: async () => ({ plan }), text: async () => JSON.stringify({ plan }) } as unknown as Response;
    }
    if (download.until) await download.until;
    if (download.status && download.status !== 200) {
      return { ok: false, status: download.status, text: async () => JSON.stringify(download.body) } as unknown as Response;
    }
    return {
      ok: true,
      status: 200,
      blob: async () => new Blob(['PK']),
      headers: new Headers({ 'Content-Disposition': 'attachment; filename="wfc_runs_7_20261020.zip"' }),
    } as unknown as Response;
  }) as unknown as typeof fetch;
  return served;
}

function deferred<T = void>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

let saved: string[];

beforeEach(() => {
  saved = [];
  URL.createObjectURL = vi.fn(() => 'blob:bundle');
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const dryRuns = (s: Served) => s.calls.filter(c => c.url === `${ROUTE}?dryRun=true`);
const downloads = (s: Served) => s.calls.filter(c => c.url === ROUTE);
const flush = () => new Promise(r => setTimeout(r, 0));

describe('Export runs dialog', () => {
  it("shows the plan's never-pushed image warning and downloads only on confirm, with the plan token", async () => {
    const served = serve(mkPlan({ warnings: LOCAL_ONLY_WARNINGS, warning_lines: ['The plan says: image X was never pushed.'] }));
    const onClose = vi.fn();
    const view = render(ExportRunsDialog, { props: { runIds: ['7'], onClose } });

    const warning = await view.findByTestId('export-runs-warning');
    expect(warning).toHaveTextContent('The plan says: image X was never pushed.');
    expect(served.calls[0]).toEqual({
      url: `${ROUTE}?dryRun=true`,
      body: { run_ids: ['7'], with_outputs: false, with_image: false, plan_token: null },
    });
    expect(view.getByTestId('export-runs-confirm')).toHaveTextContent('Export anyway');
    expect(downloads(served)).toHaveLength(0);

    await fireEvent.click(view.getByTestId('export-runs-confirm'));

    await waitFor(() => expect(saved).toEqual(['wfc_runs_7_20261020.zip']));
    expect(downloads(served).map(c => c.body)).toEqual([
      { run_ids: ['7'], with_outputs: false, with_image: false, plan_token: 'tok-123' },
    ]);
    expect(onClose).toHaveBeenCalled();
  });

  it('downloads an ok plan with no local-only image when Export is pressed', async () => {
    const served = serve(mkPlan({}));
    const onClose = vi.fn();
    const view = render(ExportRunsDialog, { props: { runIds: ['7', '9'], onClose } });

    const confirm = await view.findByTestId('export-runs-confirm');
    expect(confirm).toHaveTextContent(/^\s*Export\s*$/);
    expect(view.queryByTestId('export-runs-warning')).toBeNull();
    expect(downloads(served)).toHaveLength(0);

    await fireEvent.click(confirm);

    await waitFor(() => expect(saved).toEqual(['wfc_runs_7_20261020.zip']));
    expect(downloads(served).map(c => c.body.plan_token)).toEqual(['tok-123']);
    expect(downloads(served)[0].body.run_ids).toEqual(['7', '9']);
    expect(onClose).toHaveBeenCalled();
  });

  it("lists every refusal under the plan's refused lead and downloads nothing", async () => {
    const plan = mkPlan({
      ok: false,
      refusals: [
        { run_id: 7, reason: 'Run 7 has no invocation record.' },
        { run_id: 5, reason: 'Output out of run 5 has no content hash.' },
      ],
      warnings: [{ run_id: 5, env: 'env-a', image: 'wfc/env-a:1', state: 'never-pushed' }],
      // A lead no dialog could hard-code: the dialog shows the plan's own.
      refused_text: 'The plan says: this bundle would be incomplete (lead 7f3a).',
    });
    const served = serve(plan);
    const onClose = vi.fn();
    const view = render(ExportRunsDialog, { props: { runIds: ['7'], onClose } });

    const reasons = await view.findAllByTestId('export-runs-refusal');
    expect(reasons.map(r => r.textContent)).toEqual([
      'Run 7 has no invocation record.',
      'Output out of run 5 has no content hash.',
    ]);
    const lead = view.getByTestId('export-runs-refused-lead');
    expect(lead).toHaveTextContent('The plan says: this bundle would be incomplete (lead 7f3a).');
    expect(lead).not.toHaveTextContent(REFUSED_TEXT);
    expect(view.queryByTestId('export-runs-confirm')).toBeNull();
    expect(downloads(served)).toHaveLength(0);
    expect(saved).toEqual([]);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows every reason the download is refused with', async () => {
    serve(
      mkPlan({}),
      { status: 409, body: { detail: { message: 'The export is refused. Nothing was written.', reasons: ['Run 5 not found.'] } } },
    );
    const onClose = vi.fn();
    const view = render(ExportRunsDialog, { props: { runIds: ['7'], onClose } });

    await fireEvent.click(await view.findByTestId('export-runs-confirm'));

    const reasons = await view.findAllByTestId('export-runs-refusal');
    expect(reasons.map(r => r.textContent)).toEqual(['Run 5 not found.']);
    expect(view.getByTestId('export-runs-dialog')).toHaveTextContent('The export is refused. Nothing was written.');
    expect(saved).toEqual([]);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('sends Include outputs and Include images with the dry run and the download', async () => {
    const served = serve(planForOptions);
    const view = render(ExportRunsDialog, { props: { runIds: ['7'], onClose: vi.fn() } });
    await view.findByTestId('export-runs-confirm');

    await fireEvent.click(view.getByLabelText('Include outputs'));
    await waitFor(() => expect(dryRuns(served)).toHaveLength(2));
    await fireEvent.click(view.getByLabelText('Include images'));
    await waitFor(() => expect(dryRuns(served)).toHaveLength(3));

    expect(dryRuns(served).map(c => c.body)).toEqual([
      { run_ids: ['7'], with_outputs: false, with_image: false, plan_token: null },
      { run_ids: ['7'], with_outputs: true, with_image: false, plan_token: null },
      { run_ids: ['7'], with_outputs: true, with_image: true, plan_token: null },
    ]);
    await waitFor(() => expect(view.queryByTestId('export-runs-warning')).toBeNull());

    await fireEvent.click(view.getByTestId('export-runs-confirm'));

    await waitFor(() => expect(saved).toEqual(['wfc_runs_7_20261020.zip']));
    expect(downloads(served).map(c => c.body)).toEqual([
      { run_ids: ['7'], with_outputs: true, with_image: true, plan_token: 'tok-o1-i1' },
    ]);
  });

  it("re-plans when a box changes and shows only the latest plan's warning", async () => {
    const first = deferred();
    const served = serve(async (body) => {
      // The first dry run (images off) answers only after the re-plan has.
      if (dryRuns(served).length === 1) await first.promise;
      return planForOptions(body);
    });
    const view = render(ExportRunsDialog, { props: { runIds: ['7'], onClose: vi.fn() } });
    await waitFor(() => expect(dryRuns(served)).toHaveLength(1));

    await fireEvent.click(view.getByLabelText('Include images'));
    await waitFor(() => expect(view.getByTestId('export-runs-confirm')).toHaveTextContent(/^\s*Export\s*$/));
    first.resolve();
    await flush();
    await flush();

    expect(view.queryByTestId('export-runs-warning')).toBeNull();
    expect(view.getByLabelText('Include images')).toBeChecked();

    await fireEvent.click(view.getByLabelText('Include images'));
    expect(await view.findByTestId('export-runs-warning')).toHaveTextContent(LOCAL_ONLY_LINE);
    expect(view.getByTestId('export-runs-confirm')).toHaveTextContent('Export anyway');
    expect(dryRuns(served).map(c => c.body.with_image)).toEqual([false, true, false]);

    await fireEvent.click(view.getByTestId('export-runs-confirm'));
    await waitFor(() => expect(downloads(served)).toHaveLength(1));
    expect(downloads(served)[0].body).toEqual(
      { run_ids: ['7'], with_outputs: false, with_image: false, plan_token: 'tok-o0-i0' },
    );
  });

  it('saves nothing when closed while planning', async () => {
    const answer = deferred();
    const served = serve(async (body) => {
      await answer.promise;
      return planForOptions(body);
    });
    const onClose = vi.fn();
    const view = render(ExportRunsDialog, { props: { runIds: ['7'], onClose } });
    await waitFor(() => expect(dryRuns(served)).toHaveLength(1));

    await fireEvent.click(view.getByTitle('Close'));
    expect(onClose).toHaveBeenCalledTimes(1);
    answer.resolve();
    await flush();
    await flush();

    expect(view.queryByTestId('export-runs-confirm')).toBeNull();
    expect(downloads(served)).toHaveLength(0);
    expect(saved).toEqual([]);
  });

  it('saves nothing when closed while downloading', async () => {
    const answer = deferred();
    const served = serve(mkPlan({}), { until: answer.promise });
    const onClose = vi.fn();
    const view = render(ExportRunsDialog, { props: { runIds: ['7'], onClose } });

    await fireEvent.click(await view.findByTestId('export-runs-confirm'));
    await waitFor(() => expect(downloads(served)).toHaveLength(1));
    await fireEvent.click(view.getByTitle('Close'));
    answer.resolve();
    await flush();
    await flush();

    expect(saved).toEqual([]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
