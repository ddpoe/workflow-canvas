/**
 * Browser smoke tests for the History view's Pipelines tab (Task 10 of
 * the load-in-canvas cycle, incarnation 3).
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
 */
import { expect, test, type Page } from '@playwright/test';

interface MockWfcRun {
  id: string;
  module: string;
  method: string;
  version: string;
  timestamp: number;
  duration: number;
  status: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, string>;
  metrics: Record<string, number>;
  dataSource: string;
  parentRunIds: string[];
  parents: { slot: string; sourceRunId: string }[];
  experimentId: string;
  runName: string;
  nid: string;
  user: string;
  favorite: boolean;
  pipelineId: string | null;
  scriptPath: string | null;
}

function mkRun(p: Partial<MockWfcRun>): MockWfcRun {
  return {
    id: p.id ?? 'r1',
    module: p.module ?? 'mod_a',
    method: p.method ?? 'method_a',
    version: '1',
    timestamp: p.timestamp ?? 1_700_000_000_000,
    duration: 1.5,
    status: p.status ?? 'success',
    inputs: {},
    outputs: {},
    metrics: {},
    dataSource: p.dataSource ?? 'sample_a',
    parentRunIds: [],
    parents: [],
    experimentId: 'exp1',
    runName: p.runName ?? 'pipe_a/run_1',
    nid: 'v1',
    user: 'tester',
    favorite: false,
    pipelineId: p.pipelineId ?? 'pipe_a',
    scriptPath: null,
    ...p,
  };
}

// Three pipelines: one running, one done, one failed. Each has 2 child
// runs (one per sample) so PipelineRow rolls up to {2 total, sample
// counts, status priority} per the historyStore.ts derivations.
const FIXTURE_RUNS: MockWfcRun[] = [
  mkRun({ id: 'r1', pipelineId: 'pipe_running', runName: 'pipe_running/foo', dataSource: 'sample_a', status: 'running', timestamp: 1_700_003_000_000 }),
  mkRun({ id: 'r2', pipelineId: 'pipe_running', runName: 'pipe_running/bar', dataSource: 'sample_b', status: 'pending', timestamp: 1_700_003_100_000 }),
  mkRun({ id: 'r3', pipelineId: 'pipe_done', runName: 'pipe_done/foo', dataSource: 'sample_a', status: 'success', timestamp: 1_700_002_000_000 }),
  mkRun({ id: 'r4', pipelineId: 'pipe_done', runName: 'pipe_done/bar', dataSource: 'sample_b', status: 'success', timestamp: 1_700_002_100_000 }),
  mkRun({ id: 'r5', pipelineId: 'pipe_failed', runName: 'pipe_failed/foo', dataSource: 'sample_a', status: 'failed', timestamp: 1_700_001_000_000 }),
  mkRun({ id: 'r6', pipelineId: 'pipe_failed', runName: 'pipe_failed/bar', dataSource: 'sample_b', status: 'success', timestamp: 1_700_001_100_000 }),
];

async function setupHistoryRoutes(page: Page): Promise<void> {
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

    // Default view is Pipelines (D-7); active class is on the Pipelines button.
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

    // Find the pipe_done row (most recent succeeded one) and expand it.
    const doneRow = page.locator('.pipeline-row', { hasText: 'pipe_done' });
    await expect(doneRow).toBeVisible({ timeout: 5_000 });
    await doneRow.locator('.row-head').click();
    // Expanded state: the row gains the .expanded class and reveals .children.
    await expect(doneRow).toHaveClass(/expanded/);
    await expect(doneRow.locator('.children')).toBeVisible();

    // Click a child run row — RunDetailPanel mounts.
    const childRows = doneRow.locator('.children > *');
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
});
