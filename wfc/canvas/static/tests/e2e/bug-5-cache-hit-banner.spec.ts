/**
 * ADR-015 Phase D US-7 (Bug 5): cache-hit runs must show a banner in
 * the Inspector Output tab naming the original run id.  Expected to
 * FAIL on baseline `main` and PASS on this branch after the
 * `runStatusToNodeState` CACHE_HIT branch + InspectorPanel banner UI
 * are in place.
 */
import { expect, test } from '@playwright/test';
import { cacheHitTimeline } from '../../src/lib/__fixtures__/timelines';
import { seedAndRun } from './_helpers';

test('Bug 5: cache-hit banner visible with originalRunId', async ({ page }) => {
  await seedAndRun(page, 'cache-hit-method', cacheHitTimeline);

  await page.locator('[data-id="method_a"]').click();
  await page.getByRole('button', { name: /output/i }).click();

  const banner = page.getByTestId('cache-hit-banner');
  await expect(banner).toBeVisible({ timeout: 5_000 });
  // The fixture sets original_run_id = 'run-original-42'.
  await expect(banner).toContainText('run-original-42');
});
