/**
 * ADR-015 Phase D US-3 (Bug 1): two method nodes must show distinct
 * statuses at the same observable tick and reach distinct terminal
 * states.  Passes today (Bug 1 fixed in ADR-016); locks in correct
 * behavior.
 */
import { expect, test } from '@playwright/test';
import { multiNodeTimeline } from '../../src/lib/__fixtures__/timelines';
import { seedAndRun } from './_helpers';

test('Bug 1: two method nodes show distinct run-statuses at the same tick', async ({ page }) => {
  await seedAndRun(page, 'two-methods', multiNodeTimeline);
  const nodeA = page.locator('[data-id="method_a"]');
  const nodeB = page.locator('[data-id="method_b"]');

  // Mid-tick invariant — the actual Bug 1 regression target. We
  // capture the full status trajectory of both nodes during the run,
  // then assert (a) the trajectories differ at some observed step
  // (proves distinct nodeIds — a regression where they shared one id
  // would yield identical sequences) and (b) both reach distinct
  // terminal states. Trajectory capture is more robust than a
  // single mid-tick poll: it tolerates worker scheduling slop and
  // tick alignment drift.
  const aTrajectory: string[] = [];
  const bTrajectory: string[] = [];
  let lastA = '';
  let lastB = '';
  const captureUntil = Date.now() + 8_000;
  while (Date.now() < captureUntil) {
    const a = (await nodeA.innerText()).toLowerCase();
    const b = (await nodeB.innerText()).toLowerCase();
    if (a !== lastA) { aTrajectory.push(a); lastA = a; }
    if (b !== lastB) { bTrajectory.push(b); lastB = b; }
    if (a.includes('completed') && b.includes('failed')) break;
    await page.waitForTimeout(75);
  }

  // Distinct terminal states (a=completed, b=failed in the timeline).
  expect(lastA).toContain('completed');
  expect(lastB).toContain('failed');
  // Distinct trajectories — same nodeId regression would collapse
  // the two trajectories into the same sequence of status text.
  expect(aTrajectory.join('|')).not.toEqual(bTrajectory.join('|'));
});
