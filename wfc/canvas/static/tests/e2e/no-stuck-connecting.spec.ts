/**
 * Cache-hit runs must NOT strand the
 * Inspector Output tab on "Connecting…".  Two paths keep it off:
 * services.ts emits the cache hit ahead of the row's status, and
 * nodeRun.machine.ts handles CACHE_HIT in `running`.
 */
import { expect, test } from '@playwright/test';
import { cacheHitTimeline } from '../../src/lib/__fixtures__/timelines';
import { seedAndRun } from './_helpers';

test('cache-hit run leaves no "Connecting…" in the Output tab', async ({ page }) => {
  await seedAndRun(page, 'cache-hit-method', cacheHitTimeline);

  // Click the cached node so the InspectorPanel selects it.
  await page.locator('[data-id="method_a"]').click();
  // Switch to the Output tab.
  await page.getByRole('button', { name: /output/i }).click();

  // The cache-hit banner should appear (proves the parent reached
  // `cached`); critically, no "Connecting…" element should be
  // visible after the run terminates.
  await expect(page.getByTestId('cache-hit-banner')).toBeVisible({ timeout: 5_000 });
  await expect(page.locator('text=Connecting…')).toHaveCount(0);
});
