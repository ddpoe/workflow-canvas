/**
 * ADR-015 Phase D US-6 (Bug 4): cache-hit runs must NOT strand the
 * Inspector Output tab on "Connecting…".  Expected to FAIL on
 * baseline `main` and PASS on this branch after the dual-path fix
 * (services.ts cache-hit-first emission + nodeRun.machine.ts
 * running.CACHE_HIT handler).
 */
import { expect, test } from '@playwright/test';
import { cacheHitTimeline } from '../../src/lib/__fixtures__/timelines';
import { seedAndRun } from './_helpers';

test('Bug 4: no "Connecting…" stranded on cache-hit run', async ({ page }) => {
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
