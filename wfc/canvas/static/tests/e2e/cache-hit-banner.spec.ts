/**
 * Cache-hit runs must show a banner in
 * the Inspector Output tab naming the original run id.  The
 * `runStatusToNodeState` CACHE_HIT branch lands the node in `cached`,
 * which the Inspector's method panel renders as the banner.
 */
import { expect, test } from '@playwright/test';
import { cacheHitTimeline } from '../../src/lib/__fixtures__/timelines';
import { seedAndRun } from './_helpers';

test('cache-hit banner names the original run id', async ({ page }) => {
  await seedAndRun(page, 'cache-hit-method', cacheHitTimeline);

  await page.locator('[data-id="method_a"]').click();
  await page.getByRole('button', { name: /output/i }).click();

  const banner = page.getByTestId('cache-hit-banner');
  await expect(banner).toBeVisible({ timeout: 5_000 });
  // The fixture sets original_run_id = 'run-original-42'.
  await expect(banner).toContainText('run-original-42');
});
