/**
 * Browser test for History's rerun-bundle export buttons: Export run on the run detail panel and Export runs in
 * multi-select mode both go through the one bundle route, dry run first,
 * and download what its download answers. The route is mocked with
 * page.route(); the Python witness (tests/test_canvas_export_runs_route.py)
 * proves the route's zip is the one `wfc export-run` writes.
 *
 * Coverage:
 *   - Export run: the dry run names a never-pushed image, the warning shows
 *     with a confirm button, nothing downloads until it is pressed, and the
 *     download then carries the plan's token and saves under the server's name.
 *   - Export runs: two selected runs, a plan with no local-only image, so the
 *     dialog shows a plain Export button and the bundle downloads with the
 *     token when it is pressed.
 *   - Include outputs and Include images: each box re-plans with the option;
 *     with images included the never-pushed warning is gone, and the download
 *     carries both options and the token of the plan made with them.
 *   - Export JSON and Export Artifacts still download as before.
 */
import { expect, test, type Page, type Request } from '@playwright/test';

const T0 = 1_700_000_000_000;

function mkRun(id: string, method: string, upstream: string[], timestamp: number) {
  return {
    id,
    module: 'mod_a',
    method,
    version: 1,
    versionIdentity: null,
    timestamp,
    duration: 1.5,
    status: 'success',
    inputs: {},
    outputs: {},
    outputAvailability: {},
    metrics: {},
    dataSource: 'sample_a',
    parentRunIds: upstream,
    upstreamRunIds: upstream,
    parents: [],
    codeFingerprint: null,
    envName: null,
    envFingerprint: null,
    gitCommit: null,
    wfcVersion: null,
    pipelineDocHash: null,
    keyedUnderEarlierRule: false,
    experimentId: 'exp1',
    runName: method,
    nid: 'v1',
    user: 'tester',
    favorite: false,
    pipelineId: null,
    scriptPath: null,
  };
}

const RUNS = [
  mkRun('41', 'load_tiles', [], T0),
  mkRun('42', 'segment', ['41'], T0 + 100_000),
  mkRun('43', 'quantify', ['42'], T0 + 200_000),
];

function step(number: number, runId: number, method: string) {
  return { number, run_id: runId, method: `mod_a.${method}`, sample: 'sample_a', parent_run_ids: [], image: 'wfc/env-a:1', pullable_ref: null };
}

interface ExportBody {
  run_ids: string[];
  with_outputs: boolean;
  with_image: boolean;
  plan_token: string | null;
}

/**
 * The dry run's plan for the runs and options asked for: every run above them
 * is a step too. A never-pushed image warns, in the plan's own lines, unless
 * the images are included (Run bundle's rule); the token names the options.
 */
function planFor(body: ExportBody, localOnly: boolean) {
  const deepest = Math.max(...body.run_ids.map(Number));
  const steps = RUNS.filter(r => Number(r.id) <= deepest).map((r, i) => step(i + 1, Number(r.id), r.method));
  const warn = localOnly && !body.with_image;
  const named = steps.map(s => `step ${s.number} (run ${s.run_id})`).join(', ');
  return {
    requested: body.run_ids.map(Number),
    steps,
    substitutions: [],
    refusals: [],
    warnings: warn ? steps.map(s => ({ run_id: s.run_id, env: 'env-a', image: 'wfc/env-a:1', state: 'never-pushed' })) : [],
    warning_lines: warn
      ? [`The image wfc/env-a:1 (env env-a), used by ${named}, was never pushed to a registry. The bundle `
        + 'points at an image only this machine holds; once it is deleted, those steps cannot be rerun or compared.']
      : [],
    refused_text: 'The bundle cannot be complete. Nothing was written.',
    pulls: [],
    with_outputs: body.with_outputs,
    with_image: body.with_image,
    images: [],
    ok: true,
    token: `tok-${body.run_ids.join('-')}${body.with_outputs ? '-outputs' : ''}${body.with_image ? '-image' : ''}`,
  };
}

interface BundleRoute {
  dryRuns: ExportBody[];
  downloads: ExportBody[];
}

async function setupRoutes(page: Page, localOnly: boolean): Promise<BundleRoute> {
  const seen: BundleRoute = { dryRuns: [], downloads: [] };
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/wfc/runs', route => route.fulfill(json(RUNS)));
  await page.route('**/api/wfc/run/*', async (route) => {
    const m = route.request().url().match(/\/api\/wfc\/run\/([^/?]+)(?:\?.*)?$/);
    if (!m) { await route.fallback(); return; }
    await route.fulfill(json(RUNS.find(r => r.id === decodeURIComponent(m[1]))));
  });
  await page.route('**/api/wfc/run/*/cancelled-descendants', route => route.fulfill(json([])));
  await page.route('**/api/wfc/run/*/artifacts', route => route.fulfill(json([])));
  await page.route('**/api/wfc/modules', route => route.fulfill(json(['mod_a'])));
  await page.route('**/api/wfc/methods', route => route.fulfill(json([])));
  await page.route('**/api/wfc/status', route => route.fulfill(json({ loaded: true, path: '/x', modules: 1, runs: RUNS.length })));
  await page.route('**/api/dev/status', route => route.fulfill(json({ dev: false })));
  await page.route('**/api/samples', route => route.fulfill(json([])));
  await page.route(/\/api\/wfc\/export-runs(\?.*)?$/, async (route) => {
    const request: Request = route.request();
    const body = request.postDataJSON() as ExportBody;
    if (new URL(request.url()).searchParams.get('dryRun') === 'true') {
      seen.dryRuns.push(body);
      await route.fulfill(json({ plan: planFor(body, localOnly) }));
      return;
    }
    seen.downloads.push(body);
    await route.fulfill({
      status: 200,
      contentType: 'application/zip',
      headers: { 'Content-Disposition': `attachment; filename="wfc_runs_${body.run_ids.join('_')}_20261020.zip"` },
      body: 'PK\u0005\u0006' + '\u0000'.repeat(18),
    });
  });
  await page.route('**/api/wfc/preview-artifacts', route => route.fulfill(json({
    total_count: 1, run_count: 1, method_count: 1, total_size_bytes: 3, methods: ['segment'],
    by_type: [{ ext: 'csv', count: 1, size_bytes: 3 }],
    files: [{ method: 'segment', run_name: 'segment', artifact: 'a.csv', ext: 'csv', size_bytes: 3 }],
  })));
  await page.route('**/api/wfc/export-artifacts', route => route.fulfill({
    status: 200,
    contentType: 'application/zip',
    headers: { 'Content-Disposition': 'attachment; filename="wfc_export_42.zip"' },
    body: 'PK\u0005\u0006' + '\u0000'.repeat(18),
  }));
  return seen;
}

async function openHistory(page: Page): Promise<void> {
  await page.goto('/?fixture=history-descendants');
  await page.waitForSelector('.view-switcher', { timeout: 10_000 });
  await expect(page.locator('.tree-card')).toHaveCount(3, { timeout: 5_000 });
}

test.describe('History rerun-bundle exports', () => {
  test('Export run: the never-pushed image warning, then the download on confirm with the plan token', async ({ page }) => {
    const seen = await setupRoutes(page, true);
    await openHistory(page);

    await page.locator('.tree-card', { hasText: 'segment' }).click();
    const panel = page.locator('.detail-panel');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    await panel.getByRole('button', { name: 'Export run' }).click();

    const dialog = page.getByTestId('export-runs-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId('export-runs-warning')).toContainText(
      'The image wfc/env-a:1 (env env-a), used by step 1 (run 41), step 2 (run 42), was never pushed to a registry.',
    );
    await expect(dialog.getByTestId('export-runs-warning')).toContainText('those steps cannot be rerun or compared');
    expect(seen.dryRuns).toEqual([{ run_ids: ['42'], with_outputs: false, with_image: false, plan_token: null }]);
    expect(seen.downloads).toEqual([]);

    const download = page.waitForEvent('download');
    await dialog.getByTestId('export-runs-confirm').click();
    expect((await download).suggestedFilename()).toBe('wfc_runs_42_20261020.zip');
    expect(seen.downloads).toEqual([{ run_ids: ['42'], with_outputs: false, with_image: false, plan_token: 'tok-42' }]);
    await expect(dialog).toHaveCount(0);
  });

  test('Export runs: the selected runs download on Export when no image is local-only', async ({ page }) => {
    const seen = await setupRoutes(page, false);
    await openHistory(page);

    await page.getByTitle('Select mode for export').click();
    await page.locator('.tree-card', { hasText: 'load_tiles' }).click();
    await page.locator('.tree-card', { hasText: 'quantify' }).click();
    const bar = page.locator('.export-bar');
    await expect(bar).toContainText('2 runs selected');

    await bar.getByRole('button', { name: 'Export runs' }).click();
    const dialog = page.getByTestId('export-runs-dialog');
    await expect(dialog.getByTestId('export-runs-confirm')).toHaveText('Export');
    await expect(dialog.getByTestId('export-runs-warning')).toHaveCount(0);
    expect(seen.downloads).toEqual([]);

    const download = page.waitForEvent('download');
    await dialog.getByTestId('export-runs-confirm').click();
    expect((await download).suggestedFilename()).toBe('wfc_runs_41_43_20261020.zip');
    expect(seen.dryRuns.map(b => b.run_ids)).toEqual([['41', '43']]);
    expect(seen.downloads).toEqual([{ run_ids: ['41', '43'], with_outputs: false, with_image: false, plan_token: 'tok-41-43' }]);
    await expect(dialog).toHaveCount(0);
  });

  test('Export run with Include outputs and Include images: re-planned, no image warning, both sent with the download', async ({ page }) => {
    const seen = await setupRoutes(page, true);
    await openHistory(page);

    await page.locator('.tree-card', { hasText: 'segment' }).click();
    const panel = page.locator('.detail-panel');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    await panel.getByRole('button', { name: 'Export run' }).click();

    const dialog = page.getByTestId('export-runs-dialog');
    await expect(dialog.getByTestId('export-runs-warning')).toContainText('was never pushed to a registry');
    await dialog.getByLabel('Include outputs').check();
    await expect.poll(() => seen.dryRuns.length).toBe(2);
    await dialog.getByLabel('Include images').check();
    await expect(dialog.getByTestId('export-runs-confirm')).toHaveText('Export');
    await expect(dialog.getByTestId('export-runs-warning')).toHaveCount(0);
    expect(seen.dryRuns).toEqual([
      { run_ids: ['42'], with_outputs: false, with_image: false, plan_token: null },
      { run_ids: ['42'], with_outputs: true, with_image: false, plan_token: null },
      { run_ids: ['42'], with_outputs: true, with_image: true, plan_token: null },
    ]);

    const download = page.waitForEvent('download');
    await dialog.getByTestId('export-runs-confirm').click();
    expect((await download).suggestedFilename()).toBe('wfc_runs_42_20261020.zip');
    expect(seen.downloads).toEqual([
      { run_ids: ['42'], with_outputs: true, with_image: true, plan_token: 'tok-42-outputs-image' },
    ]);
    await expect(dialog).toHaveCount(0);
  });

  test('Export JSON and Export Artifacts still download', async ({ page }) => {
    await setupRoutes(page, false);
    await openHistory(page);

    await page.locator('.tree-card', { hasText: 'segment' }).click();
    const panel = page.locator('.detail-panel');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    const json = page.waitForEvent('download');
    await panel.getByTitle('Export JSON').click();
    expect((await json).suggestedFilename()).toBe('42.json');

    await page.getByTitle('Select mode for export').click();
    await page.locator('.tree-card', { hasText: 'segment' }).click();
    await page.locator('.export-bar').getByRole('button', { name: 'Export Artifacts' }).click();
    const zip = page.waitForEvent('download');
    await page.locator('.download-btn').click();
    expect((await zip).suggestedFilename()).toBe('wfc_export_42.zip');
  });
});
