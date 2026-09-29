/**
 * The Input Selector shows a sample's description and a directory sample's
 * file count, from the fields the samples route serves.
 */
import { expect, test } from '@playwright/test';

const SAMPLES = [
  {
    name: 'reads', file_type: 'directory',
    registered_path: 'data/samples/reads/reads_dir', file_size: 3072,
    registered_at: new Date().toISOString(),
    description: 'Paired-end reads, lane 1', file_count: 3,
  },
  {
    name: 'table', file_type: 'csv',
    registered_path: 'data/samples/table/table.csv', file_size: 2048,
    registered_at: new Date().toISOString(),
    description: null, file_count: null,
  },
];

test('the input selector shows a description and a directory file count', async ({ page }) => {
  const json = (body: unknown) => ({
    status: 200, contentType: 'application/json', body: JSON.stringify(body),
  });
  await page.route('**/api/wfc/runs', (r) => r.fulfill(json([])));
  await page.route('**/api/wfc/modules', (r) => r.fulfill(json([])));
  await page.route('**/api/wfc/methods', (r) => r.fulfill(json([])));
  await page.route('**/api/wfc/status', (r) => r.fulfill(json(
    { loaded: true, path: '/x', modules: 0, runs: 0 })));
  await page.route('**/api/dev/status', (r) => r.fulfill(json({ dev: false })));
  await page.route('**/api/wfc/samples', (r) => r.fulfill(json(SAMPLES)));

  await page.goto('/?fixture=method-and-system');
  await page.waitForSelector('.svelte-flow', { timeout: 10_000 });
  await page.locator('[data-id="system_in"]').click();

  const cards = page.locator('.card-wrap');
  await expect(cards).toHaveCount(2, { timeout: 10_000 });
  await expect(cards.nth(0).locator('.card-desc')).toHaveText('Paired-end reads, lane 1');
  await expect(cards.nth(0).locator('.card-meta')).toContainText('3 files');
  await expect(cards.nth(1).locator('.card-desc')).toHaveCount(0);
  await expect(cards.nth(1).locator('.card-meta')).not.toContainText('file');
});
