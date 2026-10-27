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
 *   - Test E (its own key-input fixture): Overview ends with the Cache-key
 *     inputs card, closed by default; opened, it shows the key's rows with
 *     labelled hash tags and the slots and params tables, for a fresh and a
 *     cached run.
 *   - Test F: every link in the card lands (run, output slot on Artifacts,
 *     method version in the Registry, the Registry's samples, the
 *     pipeline), a chip's hover names it in full and says where it goes, and
 *     the sample's and each hash's copy icons copy.
 *   - Test G: a long param value shows truncated with its size and opens to
 *     a scrolling box of the pretty-printed value with a copy icon.
 */
import { expect, test, type Page } from '@playwright/test';

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
  // The server's resolved upstreams: the input edges in slot order, then
  // cacheSourceRunId for a cache-hit row. The History views walk this.
  upstreamRunIds: string[];
  parents: { slot: string; sourceRunId: string; sourceSlot: string | null; cacheKey: string | null }[];
  sampleInputs?: { slot: string; sample: string; contentHash: string | null }[];
  versionIdentity: string | null;
  codeFingerprint: string | null;
  envName: string | null;
  envFingerprint: string | null;
  gitCommit: string | null;
  wfcVersion: string | null;
  pipelineDocHash: string | null;
  keyedUnderEarlierRule: boolean;
  paramsHash: string | null;
  inputsHash: string | null;
  paramTypes: Record<string, string>;
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
    upstreamRunIds: [],
    parents: [],
    versionIdentity: null,
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
  mkRun({ id: 'sseg', method: 'segment', dataSource: 'dapi_crop5', sampleInputs: [{ slot: 'dapi_image', sample: 'dapi_crop5', contentHash: null }], timestamp: T0 }),
  mkRun({ id: 'squant', method: 'quantify', dataSource: 'dapi_crop5', parentRunIds: ['sseg'], upstreamRunIds: ['sseg'], parents: [{ slot: 'mask', sourceRunId: 'sseg', sourceSlot: 'mask', cacheKey: null }], sampleInputs: [{ slot: 'raw', sample: 'dapi_crop5', contentHash: null }], timestamp: T0 + 100_000 }),
  mkRun({ id: 'splot', method: 'plot', dataSource: 'dapi_crop5', parentRunIds: ['squant'], upstreamRunIds: ['squant'], parents: [{ slot: 'table', sourceRunId: 'squant', sourceSlot: 'table', cacheKey: null }], timestamp: T0 + 200_000 }),
];

// The Cache-key inputs card: a freshly executed plot run with every value
// recorded, and a cached preprocess run that read its sample (its own
// recorded values; the source run keeps its own).
const COMMIT = '1fb99bc5d3e2a1b0c9f8e7d6c5b4a3f2e1d0c9b8';
const PARENT_KEY = '5ca1ab1e' + '0'.repeat(56);
const KEY_INPUT_FIELDS = {
  versionIdentity: 'b7e1d3a9c2f40518',
  codeFingerprint: 'c0de0f1a2b3c4d5e6f708192a3b4c5d6',
  envName: 'scanpy-env',
  envFingerprint: 'e9b1d2c3a4f5061728394a5b6c7d8e9f',
  gitCommit: COMMIT,
  wfcVersion: '0.6.1',
  pipelineDocHash: 'd0c0ffee112233445566778899aabbcc',
  paramsHash: '3b7c0e55aa11bb22cc33dd44ee55ff66',
  inputsHash: 'e41f9a06' + '1'.repeat(56),
};
// A long param value: a dict whose one-line form runs far past one line.
const REGIONS = { roi: Array.from({ length: 60 }, (_, i) => ({ name: `region_${i}`, x: 120 + i, y: 40 + i })) };
const KEY_INPUT_RUNS: MockWfcRun[] = [
  mkRun({ id: 'korig', method: 'preprocess', dataSource: 'pbmc3k', ...KEY_INPUT_FIELDS, sampleInputs: [{ slot: 'raw', sample: 'pbmc3k', contentHash: 'abcdef0123456789abcdef0123456789' }], timestamp: T0 }),
  mkRun({ id: 'kpre', method: 'preprocess', dataSource: 'pbmc3k', ...KEY_INPUT_FIELDS, gitCommit: 'aa11bb22cc33dd44ee55ff6677889900aabbccdd', cacheSourceRunId: 'korig', upstreamRunIds: ['korig'], outputs: { adata: 'adata.h5ad' }, outputAvailability: { adata: 'available' }, sampleInputs: [{ slot: 'raw', sample: 'pbmc3k', contentHash: 'abcdef0123456789abcdef0123456789' }], timestamp: T0 + 100_000 }),
  mkRun({ id: 'kplot2', module: 'viz', method: 'plot', dataSource: 'pbmc3k', ...KEY_INPUT_FIELDS, pipelineId: 'pl-kplot', inputs: { title: 'umap', dpi: 300, regions: REGIONS, seed: 7 }, paramTypes: { title: 'str', dpi: 'int', regions: 'dict' }, parentRunIds: ['kpre'], upstreamRunIds: ['kpre'], parents: [{ slot: 'adata', sourceRunId: 'kpre', sourceSlot: 'adata', cacheKey: PARENT_KEY }], sampleInputs: [{ slot: 'palette', sample: 'colors_v2', contentHash: 'feedface00112233445566778899aabb' }], metrics: { n_cells: 2700 }, timestamp: T0 + 200_000 }),
];

// The Registry rows the card's method and sample links land on.
async function setupRegistryRoutes(page: Page): Promise<void> {
  const recorded = { envFingerprint: 'e'.repeat(64), image: 'docker://local/scanpy-env@sha256:' + 'f'.repeat(64) };
  const versions = [
    { label: 1, identity: 'b7e1d3a9c2f40518', registered_at: '2026-10-01T00:00:00', env: 'scanpy-env', newest: false, runCount: 3,
      commit: null, codeTree: '1'.repeat(40), ...recorded },
    { label: 2, identity: 'f00dfeed99887766', registered_at: '2026-10-10T00:00:00', env: 'scanpy-env', newest: true, runCount: 1,
      commit: 'aa11bb22cc33dd44ee55ff6677889900aabbccdd', codeTree: '2'.repeat(40), ...recorded },
  ];
  await page.route('**/api/registry/modules', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ modules: [] }) });
  });
  await page.route('**/api/registry/methods', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ methods: [
        { name: 'plot', module: 'viz', env: 'scanpy-env', runCount: 4, source: null, versions },
      ] }),
    });
  });
  await page.route('**/api/registry/samples', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ samples: [
        { name: 'colors_v2', source: 'data/colors', size: 10, hash: 'feedface', pushed: false, runCount: 1, registered_at: null, file_type: 'csv' },
      ] }),
    });
  });
  await page.route('**/api/registry/methods/viz/plot/detail*', async (route) => {
    const version = new URL(route.request().url()).searchParams.get('version');
    const v = versions.find(x => x.identity === version) ?? versions[1];
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        files: [], env: v.env, version: v,
        contract: { input_slots: {}, output_slots: {}, params_schema: {}, executor: 'python' },
      }),
    });
  });
}

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

    // A card names the method version the run used, apart from its NID.
    await expect(page.locator('.tree-card', { hasText: 'segment' }).locator('.card-version')).toHaveText('v1');

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

  // The rows of an opened card's group, as [row, text] pairs.
  async function rows(panel: ReturnType<Page['locator']>, group: string): Promise<string[][]> {
    return panel.getByTestId(`cache-key-group-${group}`).getByTestId('cache-key-row')
      .evaluateAll(els => els.map(e => {
        // Each text node of the value cell, trimmed, joined by one space.
        const parts: string[] = [];
        const walker = document.createTreeWalker(e.querySelector('.val')!, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          const s = (n.textContent ?? '').trim();
          if (s) parts.push(s);
        }
        return [e.getAttribute('data-row') ?? '', parts.join(' ')];
      }));
  }

  // The hash tags under a locator, as [kind, label, short, full] rows.
  async function tags(scope: ReturnType<Page['locator']>): Promise<string[][]> {
    return scope.getByTestId('hash-tag').evaluateAll(els => els.map(e => [
      e.getAttribute('data-kind') ?? '',
      e.querySelector('.k')?.textContent ?? '',
      e.querySelector('.v')?.textContent ?? '',
      e.getAttribute('data-full') ?? '',
    ]));
  }

  test('Test E: Overview ends with the Cache-key inputs card, closed by default, for a fresh and a cached run', async ({ page }) => {
    await setupHistoryRoutes(page, KEY_INPUT_RUNS);
    await page.goto('/?fixture=history-descendants');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    const panel = page.locator('.detail-panel');

    // The fresh plot run.
    await page.locator('.tree-card', { hasText: 'plot' }).first().click();
    await expect(panel).toBeVisible({ timeout: 5_000 });
    const card = panel.getByTestId('cache-key-inputs');
    await expect(card).toBeVisible();
    // The card is the last block of Overview; the facts grid stays first.
    await expect(panel.locator('.overview > *').last()).toHaveAttribute('data-testid', 'cache-key-inputs');
    await expect(panel.locator('.overview > *').first().locator('.facts-grid')).toBeVisible();

    // Closed by default: the header, the ? and the method and version chips.
    await expect(card).toHaveAttribute('data-open', 'false');
    await expect(card.getByTestId('cache-key-toggle')).toHaveText(/Cache-key inputs/);
    await expect(card.getByTestId('cache-key-help')).toBeVisible();
    await expect(card.locator('.meta')).toHaveText(/viz\.plot\s*v1/);
    await expect(card.locator('[data-testid^="cache-key-group-"]')).toHaveCount(0);

    // The ? explains the key with its formula, and nothing about the grey group.
    await card.getByTestId('cache-key-help').click();
    const help = page.getByTestId('cache-key-help-popover');
    await expect(help).toContainText('cache key = hash(');
    await expect(help).toContainText('reuses the earlier run');
    await expect(help).not.toContainText('Recorded');
    await page.keyboard.press('Escape');
    await expect(help).toHaveCount(0);

    // Opened: both groups, in order, each row's chips and tags.
    await card.getByTestId('cache-key-toggle').click();
    const groups = card.locator('[data-testid^="cache-key-group-"]');
    await expect(groups).toHaveCount(2);
    await expect(groups.nth(0)).toHaveAttribute('data-testid', 'cache-key-group-in-key');
    await expect(groups.nth(0)).toContainText('In the cache key');
    await expect(groups.nth(1)).toHaveAttribute('data-testid', 'cache-key-group-recorded');
    await expect(groups.nth(1)).toContainText('Recorded, not in the key');
    expect(await rows(panel, 'in-key')).toEqual([
      ['method', 'viz.plot v1 code c0de0f1a'],
      ['env', 'scanpy-env env e9b1d2c3'],
      ['slots', '▸ 2 input slots inputs e41f9a06'],
      ['params', '▸ 4 params params 3b7c0e55'],
    ]);
    expect(await rows(panel, 'recorded')).toEqual([
      ['commit', 'commit 1fb99bc'],
      ['wfc-version', '0.6.1'],
      ['pipeline', 'pipeline d0c0ffee'],
    ]);
    // Every hash names its kind and keeps its full value.
    expect(await tags(groups.nth(0))).toEqual([
      ['code', 'code', 'c0de0f1a', KEY_INPUT_FIELDS.codeFingerprint],
      ['env', 'env', 'e9b1d2c3', KEY_INPUT_FIELDS.envFingerprint],
      ['inputs', 'inputs', 'e41f9a06', KEY_INPUT_FIELDS.inputsHash],
      ['params', 'params', '3b7c0e55', KEY_INPUT_FIELDS.paramsHash],
    ]);
    await expect(card).not.toContainText('not recorded');
    await expect(card.getByTestId('cache-key-earlier-rule')).toHaveCount(0);

    // Hovering a hash says what it is computed from, and its copy icon says
    // what it copies; the tag at the bottom of the card keeps its hover
    // inside the window.
    const tagOf = (kind: string) => card.locator(`.hsh:has([data-testid="hash-tag"][data-kind="${kind}"])`).first();
    for (const kind of ['code', 'env', 'params', 'inputs', 'commit', 'pipeline']) {
      await tagOf(kind).hover();
      const tip = page.getByTestId('hash-tip');
      await expect(tip).toContainText(kind === 'commit' ? /commit/i : /hash/i);
      await expect(tip).not.toContainText(KEY_INPUT_FIELDS.codeFingerprint);
      await card.locator(`[data-testid="hash-copy"][data-kind="${kind}"]`).hover();
      await expect(tip).toHaveText(kind === 'commit' ? 'Copy full commit SHA' : 'Copy full hash');
      const box = await tip.boundingBox();
      const viewport = page.viewportSize()!;
      expect(box!.y).toBeGreaterThanOrEqual(0);
      expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    }
    await tagOf('env').hover();
    await expect(page.getByTestId('hash-tip')).toContainText('the contents of the env');

    // The params table: each declared type, and a dash for an undeclared one.
    await card.getByTestId('cache-key-params-toggle').click();
    const params = card.getByTestId('cache-key-params-table');
    await expect(params).toBeVisible();
    const types = await params.getByTestId('param-type').allTextContents();
    expect(types).toEqual(['str', 'int', 'dict', '—']);
    expect(await params.getByTestId('param-row').allTextContents()).toEqual(['title', 'dpi', 'regions', 'seed']);

    // The slots table: a parent row and a sample row, each with its hash.
    await card.getByTestId('cache-key-slots-toggle').click();
    const slots = card.getByTestId('cache-key-slots-table');
    await expect(slots.getByTestId('slot-row')).toHaveCount(2);
    expect(await slots.getByTestId('slot-row').evaluateAll(els => els.map(e => e.getAttribute('data-kind'))))
      .toEqual(['parent', 'sample']);
    await expect(slots.getByTestId('slot-run-link')).toHaveText('#kpre');
    await expect(slots.getByTestId('slot-output-link')).toHaveText('adata');
    await expect(slots.getByTestId('slot-sample-link')).toHaveText('sample colors_v2');
    expect((await tags(slots)).map(t => t.slice(0, 3))).toEqual([
      ['run-key', 'run key', '5ca1ab1e'],
      ['content', 'content', 'feedface'],
    ]);

    // The cached preprocess run, which Lineages shows: the card is closed
    // again, and opened shows its sample row and its own commit.
    await page.locator('.view-switcher .seg', { hasText: 'Lineages' }).click();
    await page.locator('.path-node.cached').first().click();
    await expect(panel.locator('.cache-chip')).toContainText('korig');
    await expect(card).toHaveAttribute('data-open', 'false');
    await card.getByTestId('cache-key-toggle').click();
    expect(await rows(panel, 'in-key')).toEqual([
      ['method', 'mod_a.preprocess v1 code c0de0f1a'],
      ['env', 'scanpy-env env e9b1d2c3'],
      ['slots', '▸ 1 input slot inputs e41f9a06'],
      ['params', 'none params 3b7c0e55'],
    ]);
    expect((await rows(panel, 'recorded'))[0]).toEqual(['commit', 'commit aa11bb2']);
  });

  test('Test F: every link in the card lands, chips name themselves on hover, and the copy icons copy', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await setupHistoryRoutes(page, KEY_INPUT_RUNS);
    await setupRegistryRoutes(page);
    await page.goto('/?fixture=history-descendants');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    const panel = page.locator('.detail-panel');
    const card = panel.getByTestId('cache-key-inputs');
    // The run the panel shows: the Run ID, first of its facts.
    const runId = panel.locator('.facts-grid .fact-mono').first();
    const openPlot = async () => {
      await page.locator('.toolbar .tab', { hasText: 'History' }).click();
      await page.locator('.view-switcher .seg', { hasText: 'Descendants' }).click();
      await page.locator('.tree-card', { hasText: 'plot' }).first().click();
      await expect(runId).toHaveText('kplot2');
      await card.getByTestId('cache-key-toggle').click();
      await card.getByTestId('cache-key-slots-toggle').click();
    };

    // Commit copy: the full SHA.
    await openPlot();
    await card.locator('[data-testid="hash-copy"][data-kind="commit"]').click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(COMMIT);
    // A chip's hover names it in full and says where a click goes.
    const tip = page.getByTestId('hash-tip');
    await card.getByTestId('slot-output-link').hover();
    await expect(tip).toHaveText('adata — open run #kpre on its Artifacts');
    await card.getByTestId('cache-key-method-link').hover();
    await expect(tip).toHaveText('viz.plot — open this version in the Registry');
    // Sample copy: the sample's name; its icon says so, then "Copied".
    await card.getByTestId('slot-sample-copy').hover();
    await expect(tip).toHaveText('Copy sample name');
    await card.getByTestId('slot-sample-copy').click();
    await expect(tip).toHaveText('Copied');
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('colors_v2');

    // Run chip: that run, on its Overview.
    await card.getByTestId('slot-run-link').click();
    await expect(runId).toHaveText('kpre');
    await expect(panel.locator('.tab.active')).toHaveText(/Overview/);

    // Output-slot chip: that run, on its Artifacts.
    await openPlot();
    await card.getByTestId('slot-output-link').click();
    await expect(panel.locator('.tab.active')).toHaveText(/Artifacts/);
    await expect(panel.locator('.hero-module')).toHaveText('mod_a');
    await panel.locator('.tab', { hasText: 'Overview' }).click();
    await expect(runId).toHaveText('kpre');

    // Method chip: the Registry's Methods, the row expanded on the run's version.
    await openPlot();
    await card.getByTestId('cache-key-method-link').click();
    await expect(page.locator('.registry')).toBeVisible();
    await expect(page.locator('.registry .sub-tab.active')).toHaveText(/Methods/);
    await expect(page.locator('.registry [data-testid="method-row"][aria-expanded="true"]')).toContainText('plot');
    await expect(page.locator('.registry [data-testid="version-picker"]')).toContainText('v1');
    await page.locator('.registry [data-testid="version-picker"]').click();
    await expect(page.locator('[data-testid="version-option"][aria-pressed="true"]')).toContainText('v1');
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('version-note')).toHaveCount(0);

    // Sample chip: the Registry's Samples, at the top.
    await openPlot();
    await card.getByTestId('slot-sample-link').click();
    await expect(page.locator('.registry .sub-tab.active')).toHaveText(/Samples/);
    await expect(page.locator('.registry')).toContainText('colors_v2');
    expect(await page.locator('.registry .body').evaluate(el => el.scrollTop)).toBe(0);

    // Pipeline tag: the Pipelines view.
    await openPlot();
    await card.getByTestId('cache-key-pipeline-link').click();
    await expect(page.locator('.view-switcher .seg', { hasText: 'Pipelines' })).toHaveClass(/active/);
  });

  test('Test F2: a method version the Registry no longer lists opens the newest, with a note', async ({ page }) => {
    const runs = KEY_INPUT_RUNS.map(r => (r.id === 'kplot2' ? { ...r, versionIdentity: 'gone0000' } : r));
    await setupHistoryRoutes(page, runs);
    await setupRegistryRoutes(page);
    await page.goto('/?fixture=history-descendants');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    await page.locator('.tree-card', { hasText: 'plot' }).first().click();
    const card = page.locator('.detail-panel').getByTestId('cache-key-inputs');
    await card.getByTestId('cache-key-toggle').click();
    await card.getByTestId('cache-key-method-link').click();
    await expect(page.locator('.registry [data-testid="method-row"][aria-expanded="true"]')).toContainText('plot');
    await expect(page.locator('.registry [data-testid="version-picker"]')).toContainText('v2');
    await expect(page.getByTestId('version-note')).toHaveText("The run's version v1 is not listed; showing v2.");
  });

  test('Test G: a long param value shows truncated with its size and opens to a scrolling box with a copy icon', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await setupHistoryRoutes(page, KEY_INPUT_RUNS);
    await page.goto('/?fixture=history-descendants');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    await page.locator('.tree-card', { hasText: 'plot' }).first().click();
    const card = page.locator('.detail-panel').getByTestId('cache-key-inputs');
    await card.getByTestId('cache-key-toggle').click();
    await card.getByTestId('cache-key-params-toggle').click();
    const table = card.getByTestId('cache-key-params-table');

    // Only the long value is truncated; it carries its size.
    await expect(table.getByTestId('param-long-toggle')).toHaveCount(1);
    const toggle = table.getByTestId('param-long-toggle');
    await expect(toggle.getByTestId('param-size')).toHaveText(/^\d+(\.\d)? KB$/);
    const value = toggle.getByTestId('param-value');
    expect(await value.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true);

    // Opened: the pretty-printed value in a box that scrolls, and a copy icon.
    await toggle.click();
    const json = table.getByTestId('param-json');
    await expect(json).toHaveText(JSON.stringify(REGIONS, null, 2));
    const box = json.locator('..');
    expect(await box.evaluate(el => el.clientHeight <= 240 && el.scrollHeight > el.clientHeight)).toBe(true);
    await table.getByTestId('param-json-copy').click();
    // The OS clipboard may hand line breaks back as CRLF.
    expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n'))
      .toBe(JSON.stringify(REGIONS, null, 2));
  });
});
