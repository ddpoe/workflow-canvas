/**
 * ADR-015 Phase D US-5 (Bug 3): the `running` state must paint before
 * the node transitions to `completed`.  Passes today against the
 * 150ms setTimeout defer in `services.ts`; the permanent fix is out
 * of scope for this cycle, but this behavior-first spec keeps the
 * eventual fix green.
 */
import { expect, test } from '@playwright/test';
import { normalSucceededTimeline } from '../../src/lib/__fixtures__/timelines';
import { seedAndRun } from './_helpers';

test('Bug 3: node paints `running...` before `completed`', async ({ page }) => {
  await seedAndRun(page, 'single-method', normalSucceededTimeline);
  const node = page.locator('[data-id="method_a"]');
  // Capture every distinct status text the node passes through, then
  // assert the trajectory contains `running...` BEFORE `completed`.
  // Trajectory capture is more robust than a single toContainText
  // poll: the running window is ~1s wide, but Playwright's polling
  // can miss it if the page is slow to repaint while the test
  // worker is busy.
  const trajectory: string[] = [];
  let last = '';
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const t = (await node.innerText()).toLowerCase();
    if (t !== last) { trajectory.push(t); last = t; }
    if (t.includes('completed')) break;
    await page.waitForTimeout(75);
  }
  expect(last).toContain('completed');
  const runningIdx = trajectory.findIndex(t => t.includes('running...'));
  const completedIdx = trajectory.findIndex(t => t.includes('completed'));
  expect(runningIdx).toBeGreaterThanOrEqual(0);
  expect(runningIdx).toBeLessThan(completedIdx);
});
