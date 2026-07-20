/**
 * Browser smoke (b) for the wfc demo cycle: a run with a PNG artifact
 * renders an inline <img> in the Artifacts tab; clicking opens the
 * lightbox; Escape closes it. Non-image artifacts keep their
 * download-link rows (strictly additive change).
 *
 * Route strategy mirrors history-pipelines.spec.ts: mock the /api/wfc/*
 * endpoints the History view and RunDetailPanel poll.
 */
import { expect, test, type Page } from '@playwright/test';

// 1x1 transparent PNG.
const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);

const RUN = {
  id: 'r1',
  module: '__demo__',
  method: 'plot',
  version: '1',
  timestamp: 1_700_000_000_000,
  duration: 1.5,
  status: 'success',
  inputs: {},
  outputs: {},
  metrics: {},
  dataSource: '__demo__ctrl_01',
  parentRunIds: [],
  parents: [],
  experimentId: 'exp1',
  runName: 'pipe_a/plot_1',
  nid: 'v1',
  user: 'tester',
  favorite: false,
  pipelineId: 'pipe_a',
  scriptPath: null,
};

const ARTIFACTS = [
  { name: 'figure.png', size: PNG_BYTES.length, is_image: true, extension: 'png', type: 'file' },
  { name: 'summary.csv', size: 42, is_image: false, extension: 'csv', type: 'file' },
];

async function setupRoutes(page: Page): Promise<void> {
  // Artifact bytes — registered FIRST so the bare-run route (which
  // route.fallback()s on subpaths) hands artifact URLs down to it.
  await page.route('**/api/wfc/run/r1/artifact/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: PNG_BYTES });
  });
  await page.route('**/api/wfc/run/*/artifacts', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ARTIFACTS) });
  });
  await page.route('**/api/wfc/run/*/cancelled-descendants', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route('**/api/wfc/run/*', async (route) => {
    const url = route.request().url();
    const m = url.match(/\/api\/wfc\/run\/([^/?]+)(?:\?.*)?$/);
    if (!m) {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RUN) });
  });
  await page.route('**/api/wfc/runs', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([RUN]) });
  });
  await page.route('**/api/wfc/modules', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(['__demo__']) });
  });
  await page.route('**/api/wfc/methods', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{ name: 'plot', module: '__demo__', script_path: null, env: 'x' }]),
    });
  });
  await page.route('**/api/wfc/status', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ loaded: true, path: '/x', modules: 1, runs: 1 }) });
  });
  await page.route('**/api/dev/status', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ dev: false }) });
  });
  await page.route('**/api/samples', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
}

test.describe('Inline image preview + lightbox (smoke)', () => {
  test('PNG artifact renders <img>; click opens lightbox; Escape closes', async ({ page }) => {
    await setupRoutes(page);
    await page.goto('/?fixture=history-pipelines');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });

    // Default History view is Descendants — switch to Pipelines first.
    await page.locator('.view-switcher .seg', { hasText: 'Pipelines' }).click();

    // Open the run's detail panel via its pipeline row.
    const row = page.locator('.pipeline-row', { hasText: 'pipe_a' });
    await expect(row).toBeVisible({ timeout: 5_000 });
    await row.locator('.row-head').click();
    await expect(row.locator('.children')).toBeVisible();
    await row.locator('.children > *').first().click();
    await expect(page.locator('.detail-panel')).toBeVisible({ timeout: 5_000 });

    // Switch to the Artifacts tab.
    await page.locator('.detail-panel').getByText('Artifacts').first().click();

    // The PNG renders an inline thumbnail; the CSV keeps ONLY its link row.
    const thumb = page.locator('.thumb-card .thumb-img');
    await expect(thumb).toHaveCount(1);
    await expect(thumb).toBeVisible({ timeout: 5_000 });
    // Both artifacts still have their download-link rows (additive change).
    await expect(page.locator('.file-row', { hasText: 'figure.png' })).toBeVisible();
    await expect(page.locator('.file-row', { hasText: 'summary.csv' })).toBeVisible();

    // Click the thumbnail — the lightbox overlay opens with the image.
    await page.locator('.thumb-card').click();
    await expect(page.locator('.lightbox-overlay')).toBeVisible();
    await expect(page.locator('.lightbox-img')).toBeVisible();

    // Escape closes it.
    await page.keyboard.press('Escape');
    await expect(page.locator('.lightbox-overlay')).toHaveCount(0);

    // Click-outside also closes: reopen, click the dark overlay.
    await page.locator('.thumb-card').click();
    await expect(page.locator('.lightbox-overlay')).toBeVisible();
    await page.locator('.lightbox-overlay').click({ position: { x: 5, y: 5 } });
    await expect(page.locator('.lightbox-overlay')).toHaveCount(0);
  });
});
