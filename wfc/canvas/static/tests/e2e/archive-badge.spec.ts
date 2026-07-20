/**
 * Browser smoke for the unarchived-cache toolbar badge.
 *
 * Simulates the state an interrupted run leaves behind (NULL-hash
 * outputs on completed runs) by mocking `/api/wfc/archive-status`:
 * the badge must show the amber `N runs unarchived` pill, and clicking
 * "Archive now" must POST `cache/archive` after which the badge
 * disappears once the poll reports zero unarchived.
 */
import { expect, test } from '@playwright/test';

test('badge shows amber after an interrupted run; Archive now clears it', async ({ page }) => {
  let archiveStarted = false;
  await page.route('**/api/wfc/archive-status', route => {
    const body = archiveStarted
      ? { state: 'idle', unarchived_runs: 0, unarchived_outputs: 0,
          pipeline_running: false, progress: null }
      : { state: 'idle', unarchived_runs: 2, unarchived_outputs: 5,
          pipeline_running: false, progress: null };
    void route.fulfill({ json: body });
  });
  await page.route('**/api/wfc/cache/archive', route => {
    archiveStarted = true;
    void route.fulfill({ json: { status: 'started' } });
  });

  await page.goto('/');
  await page.waitForSelector('.svelte-flow', { timeout: 10_000 });

  const badge = page.getByTestId('archive-badge');
  await expect(badge).toContainText('2 runs unarchived');

  await badge.click();
  await page.getByTestId('archive-now').click();

  // The POST flipped the mocked DB state to zero unarchived; the next
  // poll hides the badge (popover included).
  await expect(badge).toBeHidden({ timeout: 10_000 });
});
