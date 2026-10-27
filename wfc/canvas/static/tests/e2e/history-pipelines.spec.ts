/**
 * Browser smoke tests for the History view's Pipelines tab.
 *
 * Route-replay strategy: this spec mocks the /api/wfc/* endpoints directly
 * via `page.route()` rather than reusing `setupRouteReplay` (which targets
 * /api/workflow/* and SSE for canvas-side flow tests). The History view
 * polls /api/wfc/runs, /api/wfc/modules, /api/wfc/methods, /api/wfc/status,
 * /api/dev/status, plus per-run /api/wfc/run/<id> when the detail panel
 * opens — all stubbed below.
 *
 * Coverage:
 *   - Test A: Pipelines view structure renders; switcher toggles Lineages
 *     and back.
 *   - Test B: Cross-nav round trip — expand pipeline → click child run →
 *     RunDetailPanel opens with Pipeline meta-row → click meta-row → row
 *     stays expanded and is highlighted.
 *   - Test D: Watch in canvas — a running pipeline the canvas did not start
 *     (another tab, the CLI, a server restart) is put on the canvas from
 *     History; its node shows the status route's state, the Output tab
 *     streams, and the Toolbar's Stop posts the cancel route. Uses
 *     `setupRouteReplay` for the status, SSE and cancel routes.
 */
import { expect, test, type Page } from '@playwright/test';
import { setupRouteReplay } from '../../src/lib/__fixtures__/route-replay';
import { behaviorCatalog, type Timeline } from '../../src/lib/__fixtures__/timelines';

interface MockWfcRun {
  id: string;
  module: string;
  method: string;
  version: number | null;
  timestamp: number;
  duration: number;
  status: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, string>;
  outputAvailability: Record<string, 'available' | 'changed'>;
  metrics: Record<string, number>;
  dataSource: string;
  parentRunIds: string[];
  parents: { slot: string; sourceRunId: string; sourceSlot: string | null; cacheKey: string | null }[];
  versionIdentity: string | null;
  codeFingerprint: string | null;
  envName: string | null;
  envFingerprint: string | null;
  gitCommit: string | null;
  wfcVersion: string | null;
  pipelineDocHash: string | null;
  keyedUnderEarlierRule: boolean;
  experimentId: string;
  runName: string;
  nid: string;
  user: string;
  favorite: boolean;
  pipelineId: string | null;
  // The Pipelines view titles a row from the first non-empty pipelineName
  // in the group, falling back to `pipelineId.slice(0, 8)`
  // (historyStore.ts::pipelineRuns). A payload without it renders
  // "pipe_don" for pipe_done, which no hasText filter on the full id can
  // match.
  pipelineName: string | null;
  scriptPath: string | null;
  nodeId?: string | null;
  cacheSourceRunId?: string | null;
  cancelReason?: 'user' | 'upstream_failed' | 'not_run' | null;
  cancelledDueToRunId?: string | null;
  cancelledDueToMethod?: string | null;
  cancelledDueToSample?: string | null;
}

function mkRun(p: Partial<MockWfcRun>): MockWfcRun {
  return {
    id: p.id ?? 'r1',
    module: p.module ?? 'mod_a',
    method: p.method ?? 'method_a',
    version: 1,
    timestamp: p.timestamp ?? 1_700_000_000_000,
    duration: 1.5,
    status: p.status ?? 'success',
    inputs: {},
    outputs: {},
    outputAvailability: {},
    metrics: {},
    dataSource: p.dataSource ?? 'sample_a',
    parentRunIds: [],
    parents: [],
    versionIdentity: null,
    codeFingerprint: null,
    envName: null,
    envFingerprint: null,
    gitCommit: null,
    wfcVersion: null,
    pipelineDocHash: null,
    keyedUnderEarlierRule: false,
    experimentId: 'exp1',
    runName: p.runName ?? 'pipe_a/run_1',
    nid: 'v1',
    user: 'tester',
    favorite: false,
    pipelineId: p.pipelineId ?? 'pipe_a',
    pipelineName: p.pipelineName ?? p.pipelineId ?? 'pipe_a',
    scriptPath: null,
    ...p,
  };
}

// Three pipelines: one running, one done (one run a cache hit), one mixed
// with a cancelled run of each reason. The server's summary gives each its
// status, its expected run count and its node order.
const FIXTURE_RUNS: MockWfcRun[] = [
  mkRun({ id: 'r1', pipelineId: 'pipe_running', runName: 'pipe_running/foo', dataSource: 'sample_a', status: 'running', timestamp: 1_700_003_000_000 }),
  mkRun({ id: 'r3', pipelineId: 'pipe_done', runName: 'pipe_done/foo', dataSource: 'sample_a', status: 'success', timestamp: 1_700_002_000_000 }),
  mkRun({ id: 'r4', pipelineId: 'pipe_done', runName: 'pipe_done/bar', dataSource: 'sample_b', status: 'success', cacheSourceRunId: 'r0', timestamp: 1_700_002_100_000 }),
  mkRun({ id: 'r5', pipelineId: 'pipe_mixed', nodeId: 'n_load', method: 'load', dataSource: 'sample_a', status: 'failed', timestamp: 1_700_001_000_000 }),
  mkRun({ id: 'r6', pipelineId: 'pipe_mixed', nodeId: 'n_load', method: 'load', dataSource: 'sample_b', status: 'success', timestamp: 1_700_001_100_000 }),
  mkRun({ id: 'r7', pipelineId: 'pipe_mixed', nodeId: 'n_seg', method: 'segment', dataSource: 'sample_a', status: 'cancelled',
          cancelReason: 'upstream_failed', cancelledDueToRunId: 'r5', cancelledDueToMethod: 'load',
          cancelledDueToSample: 'sample_a', timestamp: 0 }),
  mkRun({ id: 'r8', pipelineId: 'pipe_mixed', nodeId: 'n_seg', method: 'segment', dataSource: 'sample_b', status: 'cancelled',
          cancelReason: 'user', timestamp: 1_700_001_200_000 }),
  mkRun({ id: 'r9', pipelineId: 'pipe_mixed', nodeId: 'n_qc', method: 'qc', dataSource: 'sample_a', status: 'cancelled',
          cancelReason: 'not_run', timestamp: 0 }),
];

const FIXTURE_SUMMARIES = [
  { pipelineId: 'pipe_running', status: 'running', expected: 2, nodeOrder: ['n_foo'] },
  { pipelineId: 'pipe_done', status: 'succeeded', expected: 2, nodeOrder: null },
  { pipelineId: 'pipe_mixed', status: 'mixed', expected: 5, nodeOrder: ['n_load', 'n_seg', 'n_qc'] },
];

async function setupHistoryRoutes(page: Page): Promise<void> {
  // Each pipeline's status and expected run count.
  await page.route('**/api/wfc/pipelines', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(FIXTURE_SUMMARIES),
    });
  });
  // Run-list polling endpoint — returns the full fixture set on every call.
  await page.route('**/api/wfc/runs', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(FIXTURE_RUNS),
    });
  });

  // Per-run fetch (RunDetailPanel calls this when opened).
  await page.route('**/api/wfc/run/*', async (route) => {
    const url = route.request().url();
    // Only the bare run fetch — not artifact/cancelled-descendants subpaths.
    const m = url.match(/\/api\/wfc\/run\/([^/?]+)(?:\?.*)?$/);
    if (!m) {
      await route.fallback();
      return;
    }
    const runId = decodeURIComponent(m[1]);
    const run = FIXTURE_RUNS.find(r => r.id === runId);
    if (!run) {
      await route.fulfill({ status: 404, body: 'not found' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(run),
    });
  });

  // Modules / methods / status — empty/minimal stubs (FilterBar reads them).
  await page.route('**/api/wfc/modules', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(['mod_a']) });
  });
  await page.route('**/api/wfc/methods', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ name: 'method_a', module: 'mod_a', script_path: null, env: 'x' }]),
    });
  });
  await page.route('**/api/wfc/status', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ loaded: true, path: '/x', modules: 1, runs: FIXTURE_RUNS.length }) });
  });
  // Cancelled-descendants and artifacts, called per-run from the detail panel.
  await page.route('**/api/wfc/run/*/cancelled-descendants', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route('**/api/wfc/run/*/artifacts', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  // Dev status banner — keep dev-mode off to avoid the DevToolbar overlaying anything.
  await page.route('**/api/dev/status', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ dev: false }) });
  });
  // Samples endpoint loaded by stores.ts::loadSamples.
  await page.route('**/api/samples', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
}

test.describe('History pipelines view (smoke)', () => {
  test('Test A: Pipelines view renders; toggle Pipelines/Lineages/Pipelines', async ({ page }) => {
    await setupHistoryRoutes(page);
    await page.goto('/?fixture=history-pipelines');

    // Wait for the History tab to mount and the segmented switcher to render.
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });
    const pipelinesBtn = page.locator('.view-switcher .seg', { hasText: 'Pipelines' });
    const lineagesBtn = page.locator('.view-switcher .seg', { hasText: 'Lineages' });
    await expect(pipelinesBtn).toBeVisible();
    await expect(lineagesBtn).toBeVisible();

    // Default view is Descendants; this suite exercises Pipelines.
    await pipelinesBtn.click();
    await expect(pipelinesBtn).toHaveClass(/active/);

    // At least one pipeline row should render once /api/wfc/runs has resolved.
    await expect(page.locator('.pipeline-row').first()).toBeVisible({ timeout: 5_000 });
    // Three fixture pipelines.
    await expect(page.locator('.pipeline-row')).toHaveCount(3);

    // Toggle to Lineages — PathsView mounts. The empty path strip is
    // acceptable; what we assert is that the switcher state flipped and
    // PipelinesView is no longer rendered.
    await lineagesBtn.click();
    await expect(lineagesBtn).toHaveClass(/active/);
    await expect(page.locator('.pipeline-row')).toHaveCount(0);

    // Toggle back to Pipelines — the rows return.
    await pipelinesBtn.click();
    await expect(pipelinesBtn).toHaveClass(/active/);
    await expect(page.locator('.pipeline-row')).toHaveCount(3);
  });

  test('Test B: cross-nav round trip — child run → detail panel → pipeline meta-row', async ({ page }) => {
    await setupHistoryRoutes(page);
    await page.goto('/?fixture=history-pipelines');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    // Default view is Descendants — switch to Pipelines first.
    await page.locator('.view-switcher .seg', { hasText: 'Pipelines' }).click();

    // Find the pipe_done row (most recent succeeded one) and expand it.
    const doneRow = page.locator('.pipeline-row', { hasText: 'pipe_done' });
    await expect(doneRow).toBeVisible({ timeout: 5_000 });
    await doneRow.locator('.row-head').click();
    // Expanded state: the row gains the .expanded class and reveals .children.
    await expect(doneRow).toHaveClass(/expanded/);
    await expect(doneRow.locator('.children')).toBeVisible();

    // Click a child run row — RunDetailPanel mounts.
    const childRows = doneRow.locator('.children .child-row');
    await expect(childRows.first()).toBeVisible({ timeout: 3_000 });
    await childRows.first().click();

    // Detail panel header appears; its breadcrumb starts with "History".
    await expect(page.locator('.detail-panel')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('.detail-panel .crumb-text')).toContainText('History');

    // Pipeline meta-row link is rendered (via run.pipelineId === 'pipe_done').
    const pipelineLink = page.locator('.detail-panel .pipeline-meta');
    await expect(pipelineLink).toBeVisible({ timeout: 5_000 });
    await expect(pipelineLink).toContainText('pipe_done');

    // Click it. Cross-nav: stays on Pipelines, expands the target row,
    // highlights the run via highlightedRunId.
    await pipelineLink.click();

    // Pipelines view still active.
    await expect(page.locator('.view-switcher .seg', { hasText: 'Pipelines' })).toHaveClass(/active/);
    // pipe_done row is still expanded (jumpToPipelineRun adds to the set).
    await expect(doneRow).toHaveClass(/expanded/);
  });

  test('Test C: headers, status pills, runs counts, the ⋯ menu and a cancelled run\'s reason', async ({ page }) => {
    await setupHistoryRoutes(page);
    await page.goto('/?fixture=history-pipelines');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });
    await page.locator('.view-switcher .seg', { hasText: 'Pipelines' }).click();

    await expect(page.getByTestId('pipelines-header')).toContainText('Status');
    await expect(page.getByTestId('pipelines-header')).toContainText('Actions');

    const mixedRow = page.locator('.pipeline-row', { hasText: 'pipe_mixed' });
    await expect(mixedRow.locator('.row-head .hist-pill')).toHaveText(/mixed/i);
    await expect(mixedRow.getByTestId('runs-text')).toHaveText('1 ran · 1 failed · 3 cancelled');
    const doneRow = page.locator('.pipeline-row', { hasText: 'pipe_done' });
    await expect(doneRow.getByTestId('runs-text')).toHaveText('1 ran · 1 cached');
    const runningRow = page.locator('.pipeline-row', { hasText: 'pipe_running' });
    await expect(runningRow.getByTestId('runs-text')).toHaveText('1 running · 1 pending');

    // The ⋯ menu holds the pipeline's three actions.
    await mixedRow.locator('button.kebab').click();
    await expect(page.getByTestId('pipeline-menu').getByRole('menuitem')).toHaveText(
      ['Open in Canvas ↗', 'Download pipeline JSON', 'Copy pipeline ID']);
    await page.keyboard.press('Escape');

    // Expanded: step order from the document, and each cancelled pill's
    // reason on hover.
    await mixedRow.locator('.row-head').click();
    await expect(mixedRow.locator('.child-row .step')).toHaveText(
      ['load', 'load', 'segment', 'segment', 'qc']);
    const upstream = mixedRow.locator('.child-row[data-run-id="r7"]');
    await upstream.locator('.hist-pill').hover();
    await expect(upstream.getByRole('tooltip')).toBeVisible();
    await expect(upstream.getByRole('tooltip')).toContainText('Upstream step failed: load on sample_a');
    // Not hovered, so hidden: read it by its role attribute.
    await expect(mixedRow.locator('.child-row[data-run-id="r9"] [role="tooltip"]'))
      .toHaveText('Not run: the pipeline ended before it started');
  });

  test('Test D: Watch in canvas puts a running pipeline on the canvas; its node streams and Stop cancels it', async ({ page }) => {
    // The status route reports pipe_running's one node running for as long
    // as it is polled; its log replays from a recorded stream.
    const running: Timeline = [{
      delayMs: 0,
      payload: {
        job_id: 'pipe_running',
        overall_status: 'running',
        node_states: {
          n_foo: {
            status: 'running',
            run_ids: ['r1'],
            tally: { running: 1, completed: 0, failed: 0 },
          },
        },
        thread_alive: true,
        log: '',
        error: null,
      },
    }];
    await setupRouteReplay(page, running, {
      jobId: 'pipe_running',
      sseStream: behaviorCatalog.liveLogLineAppend.sseStream,
    });
    await setupHistoryRoutes(page);
    // The pipeline's editable document, which Watch loads onto the canvas.
    await page.route('**/api/workflow/pipe_running/editable', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          name: 'pipe_running',
          nodes: [{ id: 'n_foo', type: 'method', method: 'method_a', module: 'mod_a', params: {} }],
          links: [],
          samples: ['sample_a'],
        }),
      });
    });
    const cancels: string[] = [];
    await page.route('**/api/workflow/cancel/**', async (route) => {
      cancels.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'cancelled', job_id: 'pipe_running', noop: false }),
      });
    });

    // History lists the running pipeline, with Watch in place of Open.
    await page.goto('/?fixture=history-pipelines');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });
    await page.locator('.view-switcher .seg', { hasText: 'Pipelines' }).click();
    const runningRow = page.locator('.pipeline-row', { hasText: 'pipe_running' });
    await expect(runningRow.locator('.row-head .hist-pill')).toHaveText(/running/i);
    await runningRow.getByTestId('pipeline-watch').click();

    // On the Builder tab the node shows the status route's state.
    await page.locator('button.tab', { hasText: 'Builder' }).click();
    const node = page.locator('[data-id="n_foo"]');
    await expect(node).toContainText(/running/i, { timeout: 8_000 });

    // Its Output tab streams the running run's log.
    await node.click();
    await page.getByRole('button', { name: /output/i }).click();
    await expect(page.locator('.output-status')).toContainText(/streaming/i, { timeout: 5_000 });
    await expect(page.locator('.log-pane span').first()).toBeVisible({ timeout: 5_000 });

    // The Toolbar shows Stop, and Stop cancels the watched pipeline.
    expect(cancels).toEqual([]);
    await page.locator('.btn-stop').click();
    await expect.poll(() => cancels, { timeout: 5_000 })
      .toEqual(['POST /api/workflow/cancel/pipe_running']);
    await expect(page.locator('.btn-run')).toBeVisible();
  });
});
