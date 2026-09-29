/**
 * Browser smoke tests for the History view's Descendants tab and the
 * Lineages cached-run marking.
 *
 * Route-replay strategy mirrors history-pipelines.spec.ts: mock the
 * /api/wfc/* endpoints directly via page.route().
 *
 * Fixture lineage (sample_a): load_tiles → stitch (CACHE HIT) → segment
 * → quantify (failed), plus a lone sample_b root. Coverage:
 *   - Test A: Descendants tab renders per-sample sections; the cached
 *     stitch run is absent with segment promoted under load_tiles; a
 *     status-chip toggle reshapes the tree; collapse-all / expand-all.
 *   - Test B: Lineages still shows the cached run, with the 3-sided
 *     amber treatment + CACHED pill and the cached summary bucket; the
 *     status-chip filter works in Lineages too.
 *   - Test C (its own diamond fixture): a run with two parents is drawn
 *     under each, with its subtree and a "2 parents" pill, and selecting
 *     one copy highlights both.
 *   - Test D (its own sample-read fixture): the run detail panel lists a
 *     run's recorded sample reads under its parent chips, one
 *     "reads <sample> -> <slot>" line each, and a run without any shows none.
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
  // The server's resolved upstreams: the input edges in slot order, then
  // cacheSourceRunId for a cache-hit row. The History views walk this.
  upstreamRunIds: string[];
  parents: { slot: string; sourceRunId: string }[];
  sampleInputs?: { slot: string; sample: string }[];
  experimentId: string;
  runName: string;
  nid: string;
  user: string;
  favorite: boolean;
  pipelineId: string | null;
  scriptPath: string | null;
  cacheSourceRunId?: string | null;
  error_message?: string | null;
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
    upstreamRunIds: [],
    parents: [],
    experimentId: 'exp1',
    runName: p.runName ?? 'run',
    nid: 'v1',
    user: 'tester',
    favorite: false,
    pipelineId: null,
    scriptPath: null,
    ...p,
  };
}

const T0 = 1_700_000_000_000;
const FIXTURE_RUNS: MockWfcRun[] = [
  mkRun({ id: 'dload', method: 'load_tiles', runName: 'load_tiles', dataSource: 'sample_a', timestamp: T0 }),
  mkRun({ id: 'dstitch', method: 'stitch', runName: 'stitch', dataSource: 'sample_a', parentRunIds: ['dload'], upstreamRunIds: ['dload', 'orig_stitch'], cacheSourceRunId: 'orig_stitch', timestamp: T0 + 100_000 }),
  mkRun({ id: 'dseg', method: 'segment', runName: 'segment', dataSource: 'sample_a', parentRunIds: ['dstitch'], upstreamRunIds: ['dstitch'], timestamp: T0 + 200_000 }),
  mkRun({ id: 'dquant', method: 'quantify', runName: 'quantify', dataSource: 'sample_a', parentRunIds: ['dseg'], upstreamRunIds: ['dseg'], status: 'failed', error_message: 'boom: exit 1', timestamp: T0 + 300_000 }),
  mkRun({ id: 'eload', method: 'load_tiles', runName: 'load_tiles_b', dataSource: 'sample_b', timestamp: T0 + 400_000 }),
];

// A diamond: load → two segmentations → a comparison fed by both → a plot.
const DIAMOND_RUNS: MockWfcRun[] = [
  mkRun({ id: 'kload', method: 'load_tiles', dataSource: 'sample_d', timestamp: T0 }),
  mkRun({ id: 'kseg1', method: 'segment_cyto', dataSource: 'sample_d', parentRunIds: ['kload'], upstreamRunIds: ['kload'], timestamp: T0 + 100_000 }),
  mkRun({ id: 'kseg2', method: 'segment_tissue', dataSource: 'sample_d', parentRunIds: ['kload'], upstreamRunIds: ['kload'], timestamp: T0 + 90_000 }),
  mkRun({ id: 'kcmp', method: 'compare', dataSource: 'sample_d', parentRunIds: ['kseg1', 'kseg2'], upstreamRunIds: ['kseg1', 'kseg2'], timestamp: T0 + 200_000 }),
  mkRun({ id: 'kplot', method: 'plot', dataSource: 'sample_d', parentRunIds: ['kcmp'], upstreamRunIds: ['kcmp'], timestamp: T0 + 300_000 }),
];

// A step reading its sample beside an upstream: segment reads the sample,
// quantify reads it again beside segment's mask, plot reads quantify only.
const SAMPLE_READ_RUNS: MockWfcRun[] = [
  mkRun({ id: 'sseg', method: 'segment', dataSource: 'dapi_crop5', sampleInputs: [{ slot: 'dapi_image', sample: 'dapi_crop5' }], timestamp: T0 }),
  mkRun({ id: 'squant', method: 'quantify', dataSource: 'dapi_crop5', parentRunIds: ['sseg'], upstreamRunIds: ['sseg'], parents: [{ slot: 'mask', sourceRunId: 'sseg' }], sampleInputs: [{ slot: 'raw', sample: 'dapi_crop5' }], timestamp: T0 + 100_000 }),
  mkRun({ id: 'splot', method: 'plot', dataSource: 'dapi_crop5', parentRunIds: ['squant'], upstreamRunIds: ['squant'], parents: [{ slot: 'table', sourceRunId: 'squant' }], timestamp: T0 + 200_000 }),
];

async function setupHistoryRoutes(page: Page, fixtureRuns: MockWfcRun[] = FIXTURE_RUNS): Promise<void> {
  // The routes below serve whichever fixture the test passed.
  const FIXTURE_RUNS = fixtureRuns;
  await page.route('**/api/wfc/runs', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(FIXTURE_RUNS) });
  });
  await page.route('**/api/wfc/run/*', async (route) => {
    const m = route.request().url().match(/\/api\/wfc\/run\/([^/?]+)(?:\?.*)?$/);
    if (!m) { await route.fallback(); return; }
    const run = FIXTURE_RUNS.find(r => r.id === decodeURIComponent(m[1]));
    if (!run) { await route.fulfill({ status: 404, body: 'not found' }); return; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(run) });
  });
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
  await page.route('**/api/wfc/run/*/cancelled-descendants', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route('**/api/wfc/run/*/artifacts', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route('**/api/dev/status', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ dev: false }) });
  });
  await page.route('**/api/samples', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
}

test.describe('History descendants view (smoke)', () => {
  test('Test A: Descendants tab — sections, cached exclusion, filter reshape, collapse-all', async ({ page }) => {
    await setupHistoryRoutes(page);
    await page.goto('/?fixture=history-descendants');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    // Descendants is the first segment AND the default view.
    const descendantsBtn = page.locator('.view-switcher .seg', { hasText: 'Descendants' });
    await expect(descendantsBtn).toBeVisible();
    await expect(page.locator('.view-switcher .seg').first()).toHaveText('Descendants');
    await expect(descendantsBtn).toHaveClass(/active/);

    // Per-sample sections render in sample order (sample_a then sample_b),
    // whichever started first.
    await expect(page.locator('.section-label')).toHaveCount(2, { timeout: 5_000 });
    await expect(page.locator('.section-label').first()).toHaveText(/sample_a/i);

    // Cached stitch is absent; segment promoted under load_tiles.
    await expect(page.locator('.tree-card', { hasText: 'stitch' })).toHaveCount(0);
    await expect(page.locator('.tree-card', { hasText: 'segment' })).toBeVisible();
    await expect(page.locator('.tree-card')).toHaveCount(4); // dload, dseg, dquant, eload

    // Failed card carries its first error line.
    await expect(page.locator('.tree-card .err-inline')).toContainText('boom');

    // Collapse all — only the two roots stay visible.
    await page.locator('.collapse-all-btn').click();
    await expect(page.locator('.tree-card')).toHaveCount(2);
    await page.locator('.expand-all-btn').click();
    await expect(page.locator('.tree-card')).toHaveCount(4);

    // Status chip reshapes the tree: Success-only hides the failed quantify.
    await page.locator('.status-chip', { hasText: 'Success' }).click();
    await expect(page.locator('.tree-card', { hasText: 'quantify' })).toHaveCount(0);
    await expect(page.locator('.tree-card')).toHaveCount(3);
  });

  test('Test B: Lineages — cached card marking, cached bucket, filters still apply', async ({ page }) => {
    await setupHistoryRoutes(page);
    await page.goto('/?fixture=history-descendants');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    const lineagesBtn = page.locator('.view-switcher .seg', { hasText: 'Lineages' });
    await lineagesBtn.click();
    await expect(lineagesBtn).toHaveClass(/active/);

    // Cached run stays visible in Lineages, with the cached treatment.
    const cachedCard = page.locator('.path-node.cached');
    await expect(cachedCard).toHaveCount(1, { timeout: 5_000 });
    await expect(cachedCard.locator('.cached-pill')).toContainText('CACHED');

    // Summary row gains the cached bucket.
    await expect(page.locator('.summary-cached')).toContainText('1 cached');

    // Filters work in Lineages: Failed-only leaves just the quantify card.
    await expect(page.locator('.path-node')).toHaveCount(5);
    await page.locator('.status-chip', { hasText: 'Failed' }).click();
    await expect(page.locator('.path-node')).toHaveCount(1);
    await expect(page.locator('.path-node')).toContainText('quantify');
  });

  test('Test C: a diamond child appears under both parents, marked, and selecting one copy highlights both', async ({ page }) => {
    await setupHistoryRoutes(page, DIAMOND_RUNS);
    await page.goto('/?fixture=history-descendants');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    // The comparison and its plot appear once under each segmentation.
    const cmpCards = page.locator('.tree-card', { hasText: 'compare' });
    await expect(cmpCards).toHaveCount(2, { timeout: 5_000 });
    await expect(page.locator('.tree-card', { hasText: 'plot' })).toHaveCount(2);
    await expect(page.locator('.tree-card')).toHaveCount(7);

    // Each copy carries the parent-count pill; nothing else does.
    await expect(page.locator('.parents-pill')).toHaveCount(2);
    await expect(cmpCards.first().locator('.parents-pill')).toHaveText('2 parents');
    await expect(cmpCards.nth(1).locator('.parents-pill')).toHaveText('2 parents');

    // Selecting one copy highlights both.
    await cmpCards.first().click();
    await expect(cmpCards.first()).toHaveClass(/selected/);
    await expect(cmpCards.nth(1)).toHaveClass(/selected/);
    await expect(page.locator('.tree-card.selected')).toHaveCount(2);
  });

  test('Test D: the detail panel lists a run\'s sample reads under its parent chips, and none for a run without', async ({ page }) => {
    await setupHistoryRoutes(page, SAMPLE_READ_RUNS);
    await page.goto('/?fixture=history-descendants');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    // quantify reads its sample on `raw` beside segment's mask.
    await page.locator('.tree-card', { hasText: 'quantify' }).first().click();
    const panel = page.locator('.detail-panel');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    const chip = panel.locator('.parent-chip');
    await expect(chip).toHaveCount(1);
    await expect(chip).toContainText('mask');
    const reads = panel.getByTestId('sample-read');
    await expect(reads).toHaveCount(1);
    await expect(reads).toHaveText('reads dapi_crop5 -> raw');
    // The line sits under the parent chips.
    const chipBox = await chip.boundingBox();
    const readBox = await reads.boundingBox();
    expect(chipBox).not.toBeNull();
    expect(readBox).not.toBeNull();
    expect(readBox!.y).toBeGreaterThanOrEqual(chipBox!.y + chipBox!.height);

    // plot read no sample: its parent chip shows, no read line does.
    await page.locator('.tree-card', { hasText: 'plot' }).first().click();
    await expect(panel.locator('.parent-chip')).toContainText('table');
    await expect(panel.getByTestId('sample-reads')).toHaveCount(0);
  });
});
