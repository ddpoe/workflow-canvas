/**
 * Regression: a method that finishes instantly and prints nothing must not
 * strand the Inspector Output tab on "Connecting…".
 *
 * Such a run completes before the live streaming actor connects, so the
 * Inspector falls through to its historical-fetch fallback (its own
 * EventSource against `…/stream-logs?full=1`). An output-less run replays a
 * single `terminal` frame and nothing else. A regressed fallback rescheduled
 * its own effect — tearing down the EventSource before that lone frame
 * arrived — and left the badge frozen on "Connecting…".
 *
 * Expected to FAIL on the unfixed fallback (stuck "Connecting…") and PASS
 * once the effect no longer self-reschedules.
 */
import { expect, test } from '@playwright/test';
import { setupRouteReplay } from '../../src/lib/__fixtures__/route-replay';
import {
  silentFastCompletedTimeline,
  silentTerminalSSE,
} from '../../src/lib/__fixtures__/timelines';

test('silent fast-completed run settles the Output tab, not stuck on Connecting…', async ({
  page,
}) => {
  // sseStream installs the patched EventSource that replays the single
  // terminal frame; seedAndRun doesn't thread it, so call the helper directly.
  await setupRouteReplay(page, silentFastCompletedTimeline, {
    sseStream: silentTerminalSSE,
  });
  await page.goto('/?fixture=single-method');
  await page.waitForSelector('.svelte-flow', { timeout: 10_000 });
  await page.locator('.btn-run').click();

  // Select the completed node and open its Output tab.
  await page.locator('[data-id="method_a"]').click();
  await page.getByRole('button', { name: /output/i }).click();

  // The badge must settle on the terminal success state, and no
  // "Connecting…" placeholder may remain.
  await expect(page.locator('.output-status-succeeded')).toBeVisible({
    timeout: 5_000,
  });
  await expect(page.locator('text=Connecting…')).toHaveCount(0);
});
