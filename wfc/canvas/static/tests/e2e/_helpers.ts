/**
 * Shared helpers for the ADR-015 Phase D Playwright flow tests.
 *
 * Each spec calls `seedAndRun(page, fixtureKey, timeline)` to:
 *   1. Install route-replay interceptors against the timeline
 *   2. Open the canvas with `?fixture=<key>` so the canvas mounts a
 *      pre-built pipeline appropriate for the spec
 *   3. Click Run and wait for the actor tree to start ticking
 *
 * The `?fixture=...` querystring is consumed by `App.svelte` (see
 * the small bootstrap branch added in this cycle) and seeds the
 * `nodes` / `edges` stores synchronously before mount, so route-replay
 * can drive the actor tree without UI gymnastics.
 */
import type { Page } from '@playwright/test';
import { setupRouteReplay } from '../../src/lib/__fixtures__/route-replay';
import type { Timeline } from '../../src/lib/__fixtures__/timelines';

export type FixtureKey =
  | 'single-method'
  | 'two-methods'
  | 'method-and-system'
  | 'cache-hit-method';

export async function seedAndRun(
  page: Page,
  fixture: FixtureKey,
  timeline: Timeline,
): Promise<void> {
  await setupRouteReplay(page, timeline);
  await page.goto(`/?fixture=${fixture}`);
  // Wait for the canvas root to mount.
  await page.waitForSelector('.svelte-flow', { timeout: 10_000 });
  // Run button. Text is "▶ Run" (with U+25B6 prefix), so we use the
  // stable .btn-run class rather than a fragile name regex.
  await page.locator('.btn-run').click();
}
