/**
 * Capture userdocs screenshots from a live canvas serving the wfc demo
 * project.  Drives the real app (no route mocking) and writes PNGs into
 * userdocs/guide/_images/ for the Canvas how-to page.
 *
 * Two modes:
 *
 *   npx tsx scripts/docs-screenshots.ts [baseURL]
 *     Capture the main shot set from an already-running demo canvas
 *     (default http://localhost:8500).  Assumes the demo pipeline has
 *     been run so History has content.
 *
 *   npx tsx scripts/docs-screenshots.ts --cache-sequence [dir] [port]
 *     Scaffold a FRESH demo project (default: a new directory under the
 *     OS temp dir, on port 8501; the dir must be outside this repo), run
 *     the demo pipeline twice, and capture
 *     the Lineages view after each run: first run (everything executes,
 *     no CACHED pills) and second run (every step cache-hits).  Leaves
 *     any canvas on other ports untouched.
 *
 * Overview shots are full-viewport; detail shots (inspector, variables
 * panel, run detail panel on its Artifacts and Overview tabs) are element crops so they stay readable at
 * doc-page width.  Everything is captured at 2x device scale.
 */
import { chromium, type Locator, type Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import * as path from 'node:path';
import * as fs from 'node:fs';
import * as os from 'node:os';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(scriptDir, '../../../..');
const OUT = path.join(REPO_ROOT, 'userdocs', 'guide', '_images');

const VIEWPORT = { width: 1440, height: 900 };

async function settle(page: Page, ms = 600): Promise<void> {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(ms);
}

async function shot(target: Page | Locator, name: string): Promise<void> {
  await target.screenshot({ path: path.join(OUT, name) });
  console.log(`  ✓ ${name}`);
}

async function openHistoryLineages(page: Page): Promise<void> {
  await page.locator('.tabs button.tab', { hasText: 'History' }).click();
  await page.waitForSelector('.view-switcher', { timeout: 20_000 });
  await page.locator('.view-switcher .seg', { hasText: 'Lineages' }).click();
  await page.waitForSelector('.path-node', { timeout: 20_000 });
  await settle(page);
}

/** Read a status-summary bucket count, e.g. summaryCount(page, 'success'). */
async function summaryCount(page: Page, bucket: string): Promise<number> {
  const text = await page
    .locator(`.status-summary .summary-${bucket}`)
    .innerText()
    .catch(() => '0');
  const m = text.match(/(\d+)/);
  return m ? parseInt(m[1], 10) : 0;
}

// ---------------------------------------------------------------------
// Main shot set (running server, history already populated)
// ---------------------------------------------------------------------
async function captureMain(base: string): Promise<void> {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: VIEWPORT,
    deviceScaleFactor: 2,
  });

  // ---- Builder with the demo pipeline loaded --------------------
  console.log('Builder…');
  await page.goto(`${base}/?pipeline=demo`);
  await page.waitForSelector('.svelte-flow__node', { timeout: 15_000 });
  await page.waitForFunction(
    () => document.querySelectorAll('.svelte-flow__node').length >= 6,
    undefined,
    { timeout: 15_000 },
  );
  await settle(page);
  await shot(page, 'builder-demo-pipeline.png');

  // ---- Inspector with a method node selected (panel crop) -------
  const labelNode = page.locator('.svelte-flow__node', { hasText: 'label' }).first();
  await labelNode.click();
  await settle(page, 400);
  await shot(page.locator('.inspector'), 'builder-inspector.png');

  // ---- Pipeline Variables panel (crop) + bound param row (crop) -
  console.log('Pipeline variables…');
  const pvBody = page.locator('.pv-body');
  if (!(await pvBody.isVisible().catch(() => false))) {
    await page.getByTestId('pv-toggle-collapse').click();
  }
  await page.getByTestId('pv-add-variable').click();
  // Short name: the panel's name column truncates longer identifiers.
  await page.getByTestId('pv-new-name').fill('val_col');
  await page.getByTestId('pv-new-value').fill('intensity');
  await page.getByTestId('pv-confirm-add').click();
  await page.waitForSelector('[data-testid="pv-row"]');
  await shot(page.locator('.pv-panel'), 'builder-variables-panel.png');

  const plotNode = page.locator('.svelte-flow__node', { hasText: 'plot' }).first();
  await plotNode.click();
  await settle(page, 400);
  const bindBtn = page.getByTestId('open-bind-picker').first();
  if (await bindBtn.isVisible().catch(() => false)) {
    await bindBtn.click();
    await page.getByTestId('bind-picker-item').first().click();
    await settle(page, 400);
    // Just the bound param block — the full inspector is too tall here.
    await shot(
      page.locator('.param-block', { hasText: 'value_column' }).first(),
      'builder-bound-param.png',
    );
  } else {
    console.warn('  ! bind button not visible — skipping builder-bound-param.png');
  }

  // ---- Registry: methods with an expanded contract row ----------
  console.log('Registry…');
  await page.locator('.tabs button.tab', { hasText: 'Registry' }).click();
  await page.locator('.sub-tab', { hasText: 'Methods' }).click();
  await page.waitForSelector('.method-row', { timeout: 15_000 });
  await page.locator('.method-row').first().click();
  await settle(page);
  await shot(page, 'registry-methods.png');

  // ---- History: Descendants and Pipelines -----------------------
  console.log('History…');
  await page.locator('.tabs button.tab', { hasText: 'History' }).click();
  await page.waitForSelector('.tree-card', { timeout: 20_000 });
  await settle(page);
  await shot(page, 'history-descendants.png');

  await page.locator('.view-switcher .seg', { hasText: 'Pipelines' }).click();
  await page.waitForSelector('.pipeline-row', { timeout: 15_000 });
  await page.locator('.pipeline-row .row-head').first().click();
  await settle(page);
  await shot(page, 'history-pipelines.png');

  // ---- Run detail panel with a PNG artifact (panel crop) --------
  console.log('Detail panel…');
  await page.locator('.view-switcher .seg', { hasText: 'Descendants' }).click();
  await page.waitForSelector('.tree-card', { timeout: 15_000 });
  // The plot method emits the PNG artifact.
  await page.locator('.tree-card', { hasText: 'plot' }).first().click();
  await settle(page, 400);
  await page.locator('.detail-panel button', { hasText: 'Artifacts' }).first().click();
  await page.waitForFunction(() => {
    const imgs = Array.from(document.querySelectorAll('.detail-panel img'));
    return imgs.some(i => (i as HTMLImageElement).complete && (i as HTMLImageElement).naturalWidth > 0);
  }, undefined, { timeout: 15_000 }).catch(() => console.warn('  ! no artifact thumbnail detected'));
  await settle(page);
  await shot(page.locator('.detail-panel'), 'run-detail-panel.png');

  // ---- Run detail overview of a sample-reading run (panel crop) -
  // preprocess reads its sample directly, so its overview lists the read
  // under the parent-chip row.
  console.log('Detail overview…');
  await page.locator('.tree-card', { hasText: 'preprocess' }).first().click();
  await settle(page, 400);
  await page.locator('.detail-panel button', { hasText: 'Overview' }).first().click();
  await page.getByTestId('sample-read').first().waitFor({ timeout: 15_000 })
    .catch(() => console.warn('  ! no sample-read line detected'));
  await settle(page);
  await shot(page.locator('.detail-panel'), 'run-detail-overview.png');

  await browser.close();
}

// ---------------------------------------------------------------------
// Cache sequence (fresh demo project, run twice)
// ---------------------------------------------------------------------
async function runPipelineAndWait(
  page: Page,
  base: string,
  expect: { success: number; cached: number },
  prepare?: (page: Page) => Promise<void>,
): Promise<void> {
  await page.goto(`${base}/?pipeline=demo`);
  await page.waitForFunction(
    () => document.querySelectorAll('.svelte-flow__node').length >= 6,
    undefined,
    { timeout: 15_000 },
  );
  await settle(page);
  if (prepare) await prepare(page);
  // Run. With parameter rows still unlocked (opening the inspector leaves
  // rows editing) Run opens the lock summary, and the run starts only when
  // it is confirmed. Verify the run actually started (Stop button appears);
  // retry once.
  for (let attempt = 0; ; attempt++) {
    const submitted = page
      .waitForResponse(
        r => r.url().endsWith('/api/workflow/run') && r.request().method() === 'POST',
        { timeout: 30_000 },
      )
      .catch(() => null);
    await page.locator('.btn-run').click();
    const summary = page.getByTestId('lock-summary');
    await summary.waitFor({ state: 'visible', timeout: 3_000 }).catch(() => {});
    if (await summary.isVisible().catch(() => false)) {
      await summary.getByTestId('lock-summary-confirm').click();
    }
    // A refused submission (e.g. the clean-tree gate's 409) must fail
    // loudly, not leave the poll below waiting 15 minutes for runs.
    const res = await submitted;
    if (res && !res.ok()) {
      await page.screenshot({ path: path.join(OUT, '_debug-run-refused.png') });
      throw new Error(`Run refused: HTTP ${res.status()} ${await res.text()}`);
    }
    const started = await page
      .locator('button', { hasText: 'Stop' })
      .first()
      .waitFor({ state: 'visible', timeout: 20_000 })
      .then(() => true)
      .catch(() => false);
    if (started) break;
    if (attempt >= 1) {
      await page.screenshot({ path: path.join(OUT, '_debug-run-not-started.png') });
      throw new Error('Run never started (see _debug-run-not-started.png)');
    }
    console.warn('  ! run did not start — retrying submit');
  }

  // Poll the History status summary until the expected counts land.
  const deadline = Date.now() + 15 * 60_000;
  for (;;) {
    await page.waitForTimeout(10_000);
    await page.locator('.tabs button.tab', { hasText: 'History' }).click();
    await page.waitForSelector('.status-summary', { timeout: 20_000 }).catch(() => {});
    await page.locator('.refresh-btn').click().catch(() => {});
    await settle(page, 1_000);
    const success = await summaryCount(page, 'success');
    const cached = await summaryCount(page, 'cached');
    const running = await summaryCount(page, 'running');
    console.log(`  … ${success} success / ${cached} cached / ${running} running`);
    if (success >= expect.success && cached >= expect.cached && running === 0) return;
    if (Date.now() > deadline) throw new Error('Timed out waiting for the demo run to finish');
    await page.locator('.tabs button.tab', { hasText: 'Builder' }).click();
  }
}

async function captureCacheSequence(dir: string, port: number): Promise<void> {
  const base = `http://localhost:${port}`;
  fs.mkdirSync(dir, { recursive: true });
  console.log(`Scaffolding fresh demo in ${dir} (port ${port})…`);
  // wfc demo requires an initialised project; init non-interactively. The
  // archive sits beside the scratch dir: wfc init refuses one inside the project.
  await new Promise<void>((resolve, reject) => {
    const init = spawn(
      'poetry',
      ['run', 'wfc', 'init', '--dir', dir, '--archive', `${path.resolve(dir)}-archive`, '--yes'],
      { cwd: REPO_ROOT, shell: true, stdio: 'inherit' },
    );
    init.on('exit', code => (code === 0 ? resolve() : reject(new Error(`wfc init exited ${code}`))));
  });
  const server: ChildProcess = spawn(
    'poetry',
    ['run', 'wfc', 'demo', '--dir', dir, '--port', String(port), '--no-open', '--force'],
    { cwd: REPO_ROOT, shell: true, stdio: 'inherit' },
  );
  try {
    // Wait for the canvas to come up.
    const deadline = Date.now() + 5 * 60_000;
    for (;;) {
      try {
        const res = await fetch(base);
        if (res.ok) break;
      } catch { /* not up yet */ }
      if (Date.now() > deadline) throw new Error(`Canvas never came up on ${base}`);
      await new Promise(r => setTimeout(r, 2_000));
    }

    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 2 });

    console.log('First run (everything executes)…');
    await runPipelineAndWait(page, base, { success: 15, cached: 0 });
    await openHistoryLineages(page);
    await shot(page, 'history-lineages-first-run.png');

    // Second run changes one param on the terminal plot step: the 12
    // unchanged upstream jobs cache-hit and the 3 plot jobs re-execute,
    // so the new chains show CACHED upstream feeding a fresh node.  The
    // unchanged summarize chains re-run entirely from cache, and each one
    // joins the executed row it reused, labelled CACHED.
    console.log('Second run (upstream cache-hits, changed plot re-executes)…');
    await runPipelineAndWait(page, base, { success: 30, cached: 12 }, async p => {
      await p.locator('.svelte-flow__node', { hasText: 'plot' }).first().click();
      await settle(p, 400);
      const bins = p.locator('.inspector input[type="number"]').first();
      await bins.click();
      await bins.fill('30');
      await bins.press('Enter');
      await settle(p, 400);
    });
    await openHistoryLineages(page);
    await shot(page, 'history-lineages-cached.png');

    await browser.close();
  } finally {
    if (server.pid) {
      // Kill the whole tree (poetry → wfc → uvicorn) on Windows.
      spawn('taskkill', ['/pid', String(server.pid), '/T', '/F'], { shell: true });
    }
  }
}

// ---------------------------------------------------------------------
async function main(): Promise<void> {
  fs.mkdirSync(OUT, { recursive: true });
  const args = process.argv.slice(2);
  if (args[0] === '--cache-sequence') {
    // The scratch project must sit OUTSIDE this repo: inside it, `wfc init`
    // and method registration commit onto the repo's branch, and the PNGs
    // this script writes dirty the tracked tree, so the clean-tree gate
    // refuses the second run.
    const dir = path.resolve(
      args[1] ?? path.join(os.tmpdir(), `wfc-docs-cache-seq-${Date.now()}`),
    );
    const rel = path.relative(REPO_ROOT, dir);
    if (!rel.startsWith('..') && !path.isAbsolute(rel)) {
      throw new Error(`--cache-sequence dir must be outside the repo (${REPO_ROOT}): ${dir}`);
    }
    const port = args[2] ? parseInt(args[2], 10) : 8501;
    await captureCacheSequence(dir, port);
  } else {
    await captureMain(args[0] ?? 'http://localhost:8500');
  }
  console.log(`\nDone → ${OUT}`);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
