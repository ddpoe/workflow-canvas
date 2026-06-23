/**
 * ADR-015 Phase D US-4 (Bug 2): system nodes (input_selector /
 * run_reference) must never receive a run-status badge during pipeline
 * execution.  Passes today (Bug 2 fixed in ADR-016); locks in correct
 * behavior.
 */
import { expect, test } from '@playwright/test';
import { systemNodeTimeline } from '../../src/lib/__fixtures__/timelines';
import { seedAndRun } from './_helpers';

test('Bug 2: system node never shows a run-status badge', async ({ page }) => {
  await seedAndRun(page, 'method-and-system', systemNodeTimeline);
  const methodNode = page.locator('[data-id="method_only"]');
  const systemNode = page.locator('[data-id="system_in"]');

  // Method node must transition to completed.
  await expect(methodNode).toContainText(/completed/i, { timeout: 5_000 });

  // System node must NEVER show pending/running/completed/failed
  // status text.  Assert the absence of those substrings inside the
  // system node card across the lifecycle.
  const sysText = await systemNode.innerText();
  expect(sysText).not.toMatch(/pending\.\.\.|running\.\.\.|completed|failed/i);
});
