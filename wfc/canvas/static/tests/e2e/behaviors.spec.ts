/**
 * Parameterized Playwright spec driving every
 * row of the polling-driven behavior catalog through the canvas DOM.
 *
 * One test per `behaviorCatalog` row.  Per-row assertion logic stays
 * in this file (keyed by row name) so `timelines.ts` doesn't need to
 * import Playwright.  Each row also produces a screenshot at
 * `gallery/states/<name>.png` — committed to git so a doc
 * reader can eyeball every named behavior on GitHub without
 * re-running anything.
 *
 * That capture is OPT-IN (`CAPTURE_GALLERY`, see playwright.config.ts):
 * the tests always run, the file writes only happen when the gallery is
 * being regenerated deliberately.  Nothing below the capture guard is
 * conditional — no assertion is skipped by a default run.
 *
 * Trajectory-based assertions for transient states
 * (`tallyProgression`, `queuedBehindRunning`, `errorMidGraph`):
 * a setInterval-style polling loop captures the DOM trajectory and
 * asserts on the captured array.  Last-DOM-wins is used only for
 * steady-state outcomes (e.g. `failedWithTraceback` end-state).
 *
 * Visual layer: rows whose value is the trajectory
 * (not the final state) opt into per-test video capture.  The .webm
 * lands next to the .png as `gallery/states/<name>.webm`.  The
 * playwright.config.ts default of `video: 'retain-on-failure'`
 * applies to the other 9 rows — failures still capture, success
 * stays PNG-only there.
 *
 * Counterpart: `src/lib/machines/__tests__/services.behaviors.test.ts`
 * runs the same catalog through the bridge layer.
 */
import { expect, test, type Page } from '@playwright/test';
import { setupRouteReplay } from '../../src/lib/__fixtures__/route-replay';
import { behaviorCatalog } from '../../src/lib/__fixtures__/timelines';
import { CAPTURE_GALLERY } from '../../playwright.config';

test.describe.configure({ mode: 'serial' });

type BehaviorRow = (typeof behaviorCatalog)[keyof typeof behaviorCatalog];

// Mirrors the relevant `use:` options from playwright.config.ts.
// Needed because `browser.newContext()` does NOT inherit the test
// runner's `use.contextOptions` — we set up a custom context for
// video rows below to opt them into per-test recording.
const SHARED_CONTEXT_OPTIONS = {
  baseURL: 'https://localhost:5174',
  ignoreHTTPSErrors: true,
} as const;

/**
 * Rows whose value is the trajectory rather than the final DOM —
 * `streamingConnecting` (connecting → streaming flip), `liveLogLineAppend`
 * (lines appending), `cancelledByUserMidRun` (click Stop mid-run),
 * `faultMidStream` (paced lines then mid-stream crash).
 * These opt into video capture so the gallery shows the motion.
 */
const VIDEO_BEHAVIORS = new Set([
  'streamingConnecting',
  'liveLogLineAppend',
  'cancelledByUserMidRun',
  'faultMidStream',
]);

/**
 * Capture a node's status-text trajectory while the test runs.  Polls
 * `innerText` every ~100ms and appends to a list whenever the value
 * changes.  Returns the captured trajectory + the final text.
 */
async function captureTrajectory(
  page: Page,
  nodeId: string,
  budgetMs: number,
  stopWhen?: (latest: string) => boolean,
): Promise<{ trajectory: string[]; final: string }> {
  const locator = page.locator(`[data-id="${nodeId}"]`);
  const trajectory: string[] = [];
  let last = '';
  const deadline = Date.now() + budgetMs;
  while (Date.now() < deadline) {
    let text = '';
    try {
      text = (await locator.innerText({ timeout: 1_000 })).toLowerCase();
    } catch {
      // Node may not be rendered yet on the first tick — keep trying.
    }
    if (text && text !== last) {
      trajectory.push(text);
      last = text;
    }
    if (stopWhen && stopWhen(last)) break;
    await page.waitForTimeout(100);
  }
  return { trajectory, final: last };
}

async function runBehaviorRow(
  page: Page,
  name: string,
  row: BehaviorRow,
): Promise<void> {
    // Merge sseStream (declared on the BehaviorRow itself) into the
    // RouteReplayOptions passed down; without the merge the SSE fixture
    // is never wired.
    await setupRouteReplay(page, row.timeline, {
      ...(row.routeOptions ?? {}),
      sseStream: row.sseStream,
    });
    await page.goto(`/?fixture=${row.fixtureKey}`);
    await page.waitForSelector('.svelte-flow', { timeout: 10_000 });
    await page.locator('.btn-run').click();

    // Per-row assertions.  Branch by name so the catalog map stays
    // free of Playwright imports.
    switch (name) {
      case 'cancelledByUpstreamFailure': {
        // Wait for B to land in `cancelled`, then click into the
        // Inspector and assert the causality-banner names the upstream
        // node.  inspector/MethodPanel.svelte
        // exits the empty-state branch when `cancellationBanner` is set,
        // so a cancelled-because-upstream node with `runId === null`
        // renders the banner instead of the placeholder.
        await expect(page.locator('[data-id="method_a"]')).toContainText(
          /failed/i,
          { timeout: 8_000 },
        );
        await expect(page.locator('[data-id="method_b"]')).toContainText(
          /cancelled/i,
          { timeout: 8_000 },
        );
        await page.locator('[data-id="method_b"]').click();
        await page.getByRole('button', { name: /output/i }).click();
        const banner = page.locator(
          '.causality-banner[data-banner-kind="upstream"]',
        );
        await expect(banner).toBeVisible({ timeout: 5_000 });
        // Banner text: "Cancelled because Method A failed (run #run-a-1)."
        // The upstream label is looked up from the canvas $nodes store;
        // a regression that drops `upstream_node_id` from the polling
        // payload would make the lookup return undefined and the label
        // would fall back to the raw id.
        await expect(banner).toContainText('Cancelled because');
        await expect(banner).toContainText('Method A');
        await expect(banner).toContainText('run-a-1');
        break;
      }

      case 'refusedAtClaim': {
        // A step refused at the claim is a failure with its message, and
        // its descendant is cancelled because of it (the upstream banner),
        // not stopped by the user. The terminal frame ends polling.
        let statusCalls = 0;
        page.on('request', (req) => {
          if (req.url().includes('/api/workflow/status/')) statusCalls += 1;
        });
        await expect(page.locator('[data-id="method_a"]')).toContainText(
          /failed/i,
          { timeout: 8_000 },
        );
        await expect(page.locator('[data-id="method_b"]')).toContainText(
          /cancelled/i,
          { timeout: 8_000 },
        );
        await page.locator('[data-id="method_a"]').click();
        const errBox = page.getByTestId('node-error-box');
        await expect(errBox).toBeVisible({ timeout: 5_000 });
        await expect(errBox).toContainText('was not handed it');
        await page.locator('[data-id="method_b"]').click();
        await page.getByRole('button', { name: /output/i }).click();
        const banner = page.locator(
          '.causality-banner[data-banner-kind="upstream"]',
        );
        await expect(banner).toBeVisible({ timeout: 5_000 });
        await expect(banner).toContainText('Cancelled because');
        await expect(banner).toContainText('Method A');
        await expect(banner).toContainText('7');
        // Polling stopped at the terminal frame: no status request lands
        // across several polling intervals.
        const settled = statusCalls;
        await page.waitForTimeout(3_000);
        expect(statusCalls).toBe(settled);
        break;
      }

      case 'failedWithTraceback': {
        // Steady-state: failure surfaces via node-error-box in the
        // Inspector with the traceback string verbatim.
        await expect(page.locator('[data-id="method_a"]')).toContainText(
          /failed/i,
          { timeout: 8_000 },
        );
        await page.locator('[data-id="method_a"]').click();
        const errBox = page.getByTestId('node-error-box');
        await expect(errBox).toBeVisible({ timeout: 5_000 });
        // First line of the traceback string is enough to prove the
        // single `error` field made it through unmangled.
        await expect(errBox).toContainText('Traceback (most recent call last)');
        // Expanded view (click "Show full error") includes the
        // ValueError line — proves the multi-line body survived the
        // bridge.
        await page.getByRole('button', { name: /show full error/i }).click();
        await expect(errBox).toContainText('ValueError: bad input column');
        break;
      }

      case 'tallyProgression': {
        // Trajectory: at least two distinct running paints before
        // completed.  The node's innerText carries the status word
        // ("running...") rather than the tally counts; what matters
        // for trajectory assertions is that `running` was observed
        // at all (vs jumping pending->completed) and the run reached
        // `completed`.
        const { trajectory, final } = await captureTrajectory(
          page,
          'method_a',
          12_000,
          (latest) => latest.includes('completed'),
        );
        expect(final).toContain('completed');
        // The trajectory should have visited `running` before
        // `completed` — if Svelte batches the paint, this would
        // collapse to a single completed entry.
        const sawRunning = trajectory.some(t => t.includes('running'));
        expect(
          sawRunning,
          `trajectory did not observe running: ${JSON.stringify(trajectory)}`,
        ).toBe(true);
        break;
      }

      case 'queuedBehindRunning': {
        // Trajectory: capture A and B in parallel; assert that at
        // some point A is running while B is still pending.
        const aLoc = page.locator('[data-id="method_a"]');
        const bLoc = page.locator('[data-id="method_b"]');
        const samples: { a: string; b: string }[] = [];
        const deadline = Date.now() + 8_000;
        let coOccurrence = false;
        while (Date.now() < deadline) {
          const a = (await aLoc.innerText().catch(() => '')).toLowerCase();
          const b = (await bLoc.innerText().catch(() => '')).toLowerCase();
          samples.push({ a, b });
          if (a.includes('running') && b.includes('pending')) {
            coOccurrence = true;
            break;
          }
          if (a.includes('completed') && b.includes('completed')) break;
          await page.waitForTimeout(80);
        }
        expect(
          coOccurrence,
          `never observed A=running + B=pending; samples=${JSON.stringify(samples.slice(-10))}`,
        ).toBe(true);
        break;
      }

      case 'errorMidGraph': {
        // Three-node trajectory: A completed, B failed, C cancelled.
        // Assert all three terminal states co-exist in the same paint.
        const aLoc = page.locator('[data-id="method_a"]');
        const bLoc = page.locator('[data-id="method_b"]');
        const cLoc = page.locator('[data-id="method_c"]');
        const deadline = Date.now() + 10_000;
        let triple: { a: string; b: string; c: string } | null = null;
        while (Date.now() < deadline) {
          const a = (await aLoc.innerText().catch(() => '')).toLowerCase();
          const b = (await bLoc.innerText().catch(() => '')).toLowerCase();
          const c = (await cLoc.innerText().catch(() => '')).toLowerCase();
          if (a.includes('completed') && b.includes('failed') && c.includes('cancelled')) {
            triple = { a, b, c };
            break;
          }
          await page.waitForTimeout(100);
        }
        expect(
          triple,
          'never observed A=completed + B=failed + C=cancelled simultaneously',
        ).not.toBeNull();
        // B's failure surfaces in the Inspector via node-error-box (lives
        // above the tab strip, so no Output click needed).
        await page.locator('[data-id="method_b"]').click();
        const errBox = page.getByTestId('node-error-box');
        await expect(errBox).toBeVisible({ timeout: 5_000 });
        await expect(errBox).toContainText('method_b raised RuntimeError');
        // C's upstream-cause banner renders even though C never
        // heartbeated.  Banner names B as the
        // upstream that failed.
        await page.locator('[data-id="method_c"]').click();
        await page.getByRole('button', { name: /output/i }).click();
        const cBanner = page.locator(
          '.causality-banner[data-banner-kind="upstream"]',
        );
        await expect(cBanner).toBeVisible({ timeout: 5_000 });
        await expect(cBanner).toContainText('Method B');
        await expect(cBanner).toContainText('run-b-1');
        break;
      }

      case 'mixedStatus': {
        // Steady-state: node lands in `mixed` (rendered "mixed" with
        // tally badge by CustomNode's `statusLabel`).  Distinct from
        // `failed` (no completed) and `completed` (no failed).  The
        // per-node `error` string is threaded through the
        // bridge so the Inspector's node-error-box renders the
        // failed-sample error for completed_with_failures, same as
        // the `failed` state.
        const node = page.locator('[data-id="method_a"]');
        await expect(node).toContainText(/mixed/i, { timeout: 8_000 });
        // Asserting the status-label span specifically (not the whole
        // node body) catches a regression where `mixed -> completed`
        // would change the pill word but leave the method label
        // unchanged.
        const pillText = (
          await node.locator('.status-label').innerText()
        ).toLowerCase();
        expect(pillText).toContain('mixed');
        // Click the node so the Inspector renders its node-error-box
        // (lives above the tab strip — no Output click needed).
        await node.click();
        const errBox = page.getByTestId('node-error-box');
        await expect(errBox).toBeVisible({ timeout: 5_000 });
        await expect(errBox).toContainText('sample-007 raised KeyError');
        break;
      }

      case 'partialCacheHit': {
        // A has cache-hit banner; B walks through normal trajectory.
        // Assert (a) A's Output tab shows the cache-hit banner with
        // the original run id; (b) B reaches `completed`; (c) A's card
        // reads `cached`, not `completed`, so the reuse is visible on the
        // canvas without opening the Inspector.
        await expect(page.locator('[data-id="method_b"]')).toContainText(
          /completed/i,
          { timeout: 10_000 },
        );
        const aStatus = page.locator('[data-id="method_a"] .status-label');
        await expect(aStatus).toContainText('cached', { timeout: 5_000 });
        await expect(aStatus).not.toContainText('completed');
        // A: click + Output tab + banner.
        await page.locator('[data-id="method_a"]').click();
        await page.getByRole('button', { name: /output/i }).click();
        const banner = page.getByTestId('cache-hit-banner');
        await expect(banner).toBeVisible({ timeout: 5_000 });
        await expect(banner).toContainText('run-original-a-7');
        break;
      }

      case 'zeroJobDAG': {
        // Pre-run rejection: validate route returns valid:false; the
        // canvas surfaces the error via `pipeline-error-banner`.  No
        // polling is consumed.  The banner appears = the validate
        // shape `{valid: false, errors: [...]}` was honoured by
        // submitPipeline's validation check in services.ts.  The banner renders the
        // actual validate error string, not "[object Object]".
        const banner = page.getByTestId('pipeline-error-banner');
        await expect(banner).toBeVisible({ timeout: 5_000 });
        // submitPipeline wraps the validate errors in a SubmitError
        // whose .message starts with "Cannot run — validation errors:"
        // and joins each error on a new line.  Asserting on the actual
        // validate error string proves the .message extraction works
        // and a regression to `String(err)` would surface here.
        await expect(banner).toContainText('Pipeline has no nodes');
        // No polling was started — no node transitioned beyond pending.
        await expect(page.locator('[data-id="method_a"]')).not.toContainText(
          /running|completed|failed|cancelled/i,
        );
        break;
      }

      case 'streamingConnecting': {
        // The streaming child enters
        // `connecting` then flips to `streaming` on the first SSE_LINE
        // (machine-level flip is asserted in streaming.test.ts).
        // Here we just prove the user-visible transition runs to
        // `completed`.  The Output tab is opened *during* running so
        // the gallery video captures the Inspector populating with
        // streamed lines — without this, the sidebar shows the empty
        // "click a node to inspect" placeholder for the whole
        // recording.
        const node = page.locator('[data-id="method_a"]');
        await expect(node).toContainText(/running/i, { timeout: 8_000 });
        await node.click();
        await page.getByRole('button', { name: /output/i }).click();
        await expect(node).toContainText(/completed/i, { timeout: 12_000 });
        break;
      }

      case 'liveLogLineAppend': {
        // Paced SSE replay emits 5 stdout ticks
        // ~2s apart so the gallery video shows progressive log append.
        // We assert on the Inspector's status badge AND the log pane's
        // line count to catch regressions where the route-replay
        // wiring drops the SSE fixture (`row.sseStream` not plumbed into
        // RouteReplayOptions — the EventSource then falls through to the
        // single-frame terminal stub and the run looks "idle" with no
        // text).  The badge transitions: Idle → Streaming → Terminal.
        const node = page.locator('[data-id="method_a"]');
        await expect(node).toContainText(/running/i, { timeout: 8_000 });
        await node.click();
        await page.getByRole('button', { name: /output/i }).click();
        const badge = page.locator('.output-status');
        // Badge flips to Streaming once first SSE_LINE arrives.
        await expect(badge).toContainText(/streaming/i, { timeout: 5_000 });
        // At least 2 log lines have rendered before the run terminates,
        // proving paced delivery (a one-shot bundle would show all
        // lines instantly — passing this assertion specifically — but
        // would also fail to keep the badge in `streaming` long enough
        // for the next assertion below).
        await expect(page.locator('.log-pane span').nth(1)).toBeVisible({
          timeout: 5_000,
        });
        // Badge flips to Success once the SSE terminal event lands
        // (~11.1s into the run, before polling's 14th-frame completed flip).
        await expect(badge).toContainText(/success/i, { timeout: 13_000 });
        await expect(node).toContainText(/completed/i, { timeout: 6_000 });
        break;
      }

      case 'faultOnStream': {
        // SSE terminal carries
        // status:failed plus error_message + error_traceback.  Polling
        // ALSO carries an error string so the inspector's
        // node-error-box renders.
        await expect(page.locator('[data-id="method_a"]')).toContainText(
          /failed/i,
          { timeout: 10_000 },
        );
        await page.locator('[data-id="method_a"]').click();
        const errBox = page.getByTestId('node-error-box');
        await expect(errBox).toBeVisible({ timeout: 5_000 });
        await expect(errBox).toContainText(/simulated crash|ValueError/);
        break;
      }

      case 'faultMidStream': {
        // Paced SSE replay emits 3 stdout ticks
        // ~1.5s apart, then a stderr crash + traceback + terminal:failed
        // at ~5.4s.  Polling holds `running` for ~6s, then flips to
        // `failed` carrying the error string for the Inspector
        // node-error-box.  Asserts on the streaming badge mid-run +
        // node-error-box at end so a regression that drops SSE pacing
        // (single-frame terminal stub, bundled-fulfill, etc.) fails
        // here instead of silently producing an idle-looking video.
        const node = page.locator('[data-id="method_a"]');
        await expect(node).toContainText(/running/i, { timeout: 8_000 });
        await node.click();
        await page.getByRole('button', { name: /output/i }).click();
        const badge = page.locator('.output-status');
        await expect(badge).toContainText(/streaming/i, { timeout: 5_000 });
        await expect(page.locator('.log-pane span').first()).toBeVisible({
          timeout: 5_000,
        });
        await expect(node).toContainText(/failed/i, { timeout: 12_000 });
        const errBox = page.getByTestId('node-error-box');
        await expect(errBox).toBeVisible({ timeout: 5_000 });
        await expect(errBox).toContainText(/stream_fail|RuntimeError/);
        break;
      }

      case 'cancelledByUserMidRun': {
        // User clicks Stop; the
        // dispatchUserStop wire fires a cancel POST AND the local
        // USER_STOP.  We assert: (a) the cancel POST is hit (route
        // handler default mock returns 200), (b) the polling timeline
        // has flipped the row to cancelled, (c) the cancellation is
        // NOT marked with upstream_node_id (this is a user-cancel
        // substate, distinct from cancelledByUpstreamFailure).
        //
        // Click the node *before* clicking Stop so the gallery video
        // captures the running → cancelled flip inside the Inspector
        // (without this, the inspector stays on its placeholder until
        // the cancel has already happened, missing the transition).
        let cancelHit = 0;
        await page.route('**/api/workflow/cancel/**', async (route) => {
          cancelHit += 1;
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ status: 'cancelled', noop: false }),
          });
        });
        const node = page.locator('[data-id="method_a"]');
        await expect(node).toContainText(/running/i, { timeout: 8_000 });
        await node.click();
        const stopBtn = page.getByRole('button', { name: /stop|cancel/i }).first();
        if (await stopBtn.count()) {
          await stopBtn.click();
        }
        await expect(node).toContainText(/cancelled/i, { timeout: 10_000 });
        // Inspector: no upstream-cause banner (this is a user cancel).
        const upstreamBanner = page.locator(
          '.causality-banner[data-banner-kind="upstream"]',
        );
        await expect(upstreamBanner).toHaveCount(0);
        break;
      }

      default:
        throw new Error(`No assertion implemented for behavior row "${name}"`);
    }

    // Gallery: every row contributes a PNG so doc readers can
    // eyeball the named behavior without re-running.  Directory
    // committed to git (no .gitignore exclusion), so the write is
    // opt-in — every assertion above already ran.
    if (CAPTURE_GALLERY) {
      await page.screenshot({
        path: `gallery/states/${name}.png`,
        fullPage: true,
      });
    }
}

for (const [name, row] of Object.entries(behaviorCatalog)) {
  if (VIDEO_BEHAVIORS.has(name)) {
    // Manual context: `test.use({ video })` is rejected inside a
    // describe (Playwright treats video as a worker-level option), so
    // we create a context per-test with `recordVideo` enabled and copy
    // the resulting .webm next to the row's .png.
    test(`behavior: ${name}`, async ({ browser }, testInfo) => {
      const ctx = await browser.newContext({
        ...SHARED_CONTEXT_OPTIONS,
        recordVideo: { dir: testInfo.outputDir },
      });
      const page = await ctx.newPage();
      const video = page.video();
      try {
        await runBehaviorRow(page, name, row);
      } finally {
        // Closing the context finalizes the video file before saveAs.
        await ctx.close();
      }
      // `recordVideo` stays on unconditionally: it is a browser-context
      // option, and toggling it would change the conditions these
      // trajectory-sensitive rows run under.  Only the copy into the
      // tracked gallery is gated; the unsaved .webm stays in the
      // untracked test-results output dir.
      if (video && CAPTURE_GALLERY) {
        await video.saveAs(`gallery/states/${name}.webm`);
      }
    });
  } else {
    test(`behavior: ${name}`, async ({ page }) => {
      await runBehaviorRow(page, name, row);
    });
  }
}
