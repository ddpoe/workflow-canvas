/**
 * Gallery: per-state PNG capture for the
 * paramEditor / variant / paramEditorAggregator actors.
 *
 * A separate spec file driven by Playwright clicks/typing, following the
 * `behaviors.spec.ts` screenshot drop convention WITHOUT extending
 * `behaviorCatalog`. paramEditor / variant states are interaction-driven
 * (no SSE timeline + no polling fixture), so the catalog's
 * `setupRouteReplay`-shaped row contract doesn't fit; keeping the SSE
 * catalog clean preserves its single-axis purpose.
 *
 * Each test:
 *   1. Navigates to `?fixture=param-editor` (seed branch in App.svelte).
 *   2. Clicks the method node to open the Inspector.
 *   3. Drives the actor into the named state via clicks / typing.
 *   4. Asserts a state-specific DOM signal (not just a screenshot).
 *   5. Captures `gallery/states/<name>.png` so a doc reader can
 *      eyeball the named state on GitHub without re-running.
 *
 * Step 5 is OPT-IN (`CAPTURE_GALLERY`, see playwright.config.ts).  Steps
 * 1-4 are not: every test here asserts real DOM state and runs on every
 * `npx playwright test`, exactly as before.  Only the tracked PNG write is
 * gated, so a default run leaves `gallery/` untouched.
 */
import { expect, test, type Page } from '@playwright/test';
import { CAPTURE_GALLERY } from '../../playwright.config';

test.describe.configure({ mode: 'serial' });

const FIXTURE_URL = '/?fixture=param-editor';

/**
 * Drop the named gallery PNG — only when the gallery is being regenerated.
 * Called after each test's assertions, never instead of them.
 */
async function captureState(page: Page, name: string): Promise<void> {
  if (!CAPTURE_GALLERY) return;
  await page.screenshot({ path: `gallery/states/${name}.png`, fullPage: true });
}

async function openInspector(page: Page): Promise<void> {
  await page.goto(FIXTURE_URL);
  await page.waitForSelector('.svelte-flow', { timeout: 10_000 });
  await page.locator('[data-id="method_a"]').click();
  // ValueList rows render once the Inspector mounts. Both base and v1
  // auto-EDIT on first mount — rows open by default (see spawnBaseActor /
  // spawnVariantActor in inspector/rowActors.ts) — so the first paint already
  // shows both rows in their editing-shaped states.
  await page.waitForSelector('.value-list .row', { timeout: 5_000 });
}

function rowsLocator(page: Page) {
  return page.locator('.value-list .row');
}

test('paramEditor_editing: base row auto-EDITs on Inspector open', async ({ page }) => {
  await openInspector(page);
  const baseRow = rowsLocator(page).first();
  // editing-shaped: paramEditor.machine in `editing`, .row carries the
  // `editing` class (ValueList's `class:editing` on `.row`) and the
  // gutter shows 🔓.
  await expect(baseRow).toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(baseRow.locator('.gutter')).toHaveText('🔓');
  // The text input is rendered as the editable form (no `readonly`),
  // distinguishing this from the `viewing` / `committed` paint where
  // ValueList renders `<input ... readonly>`.
  const baseInput = baseRow.locator('input.text');
  await expect(baseInput).toBeEditable();
  await captureState(page, 'paramEditor_editing');
});

test('paramEditor_invalid: required-empty commit lands in invalid', async ({ page }) => {
  await openInspector(page);
  const baseRow = rowsLocator(page).first();
  // Clear the seeded "hello" draft to force the coerce required-check.
  await baseRow.locator('input.text').fill('');
  await baseRow.locator('.act.commit').click();
  // committing → onDone (ok=false) → invalid (the `committing` state's
  // `onDone` in paramEditor.machine).
  await expect(baseRow).toHaveClass(/(^|\s)invalid(\s|$)/);
  // The `.row-error` sibling div appears immediately after `.row` when
  // validationError is set (ValueList's `{#if rowError}` block). Asserting on the
  // text proves the error string from `coerceParamValue` reaches the DOM,
  // not just a class flip.
  await expect(page.locator('.value-list .row-error').first())
    .toContainText('Value cannot be empty.');
  await captureState(page, 'paramEditor_invalid');
});

test('paramEditor_committed: successful commit locks the base row', async ({ page }) => {
  await openInspector(page);
  const baseRow = rowsLocator(page).first();
  // Don't modify the draft — committing the seeded "hello" round-trips
  // the same value, and the spawn-time subscription's same-value guard
  // (`lastForwardedBase` in inspector/rowActors.ts `spawnBaseActor`)
  // suppresses `onBaseChange`. Without that
  // suppression, the commit-then-baseValue-prop-update would re-fire
  // ValueList's sync-RESET_TO `$effect`, walking the actor through
  // `committed → viewing → auto-EDIT → editing` and erasing the
  // committed paint before Playwright can observe it. Same-value
  // commits keep the actor stable in `committed`.
  await baseRow.locator('.act.commit').click();
  // committed: NOT editing (no `.editing` class) and gutter shows 🔒.
  await expect(baseRow).not.toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(baseRow.locator('.gutter')).toHaveText('🔒');
  // Re-read the readonly input value to confirm currentValue persisted.
  await expect(baseRow.locator('input.text')).toHaveValue('hello');
  await captureState(page, 'paramEditor_committed');
});

test('variant_addingVariant: + variant creates a new editing-shaped row', async ({ page }) => {
  await openInspector(page);
  const beforeCount = await rowsLocator(page).count();
  // The "+ variant" button is rendered either as `.add-variant-inline`
  // (multi-row) or `.add-variant-btn` (single-row). Both carry the same
  // text, so getByRole hits whichever is currently mounted.
  await page.getByRole('button', { name: '+ variant' }).click();
  await expect(rowsLocator(page)).toHaveCount(beforeCount + 1);
  // The new row is the last one; it lands in `addingVariant` (the
  // `noVariants` ADD_VARIANT transition in variant.machine), which
  // ValueList's `snapForRow` reports as editing-shaped.
  const newRow = rowsLocator(page).last();
  await expect(newRow).toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(newRow.locator('.gutter')).toHaveText('🔓');
  await captureState(page, 'variant_addingVariant');
});

test('variant_mergingDuplicate: dedup notice appears when v2 commits same value as v1', async ({ page }) => {
  await openInspector(page);
  // Add v2. v1's currentValue is "hello" from the seed; the parent's
  // `variants` prop is `{v1: 'hello'}`, so the new variantActor spawns
  // with siblingValues=['hello'] (ValueList's `siblingValuesFor`, read by
  // `spawnVariantActor`) — no need to commit v1 first.
  await page.getByRole('button', { name: '+ variant' }).click();
  // Rows: [base, v1, v2].
  const v2Row = rowsLocator(page).nth(2);
  await v2Row.locator('input.text').fill('hello');
  await v2Row.locator('.act.commit').click();
  // Coerce ok + isDuplicate guard true → mergingDuplicate
  // (the `committing` state's `onDone` in variant.machine).
  await expect(v2Row).toHaveClass(/(^|\s)merging(\s|$)/);
  // Sibling notice div appears right after `.row` when state matches
  // `mergingDuplicate` (ValueList's `.row-merge-notice` block).
  await expect(page.locator('.value-list .row-merge-notice'))
    .toContainText('merged with sibling');
  // The ⇆ ack-merge button is the variant-only affordance for dismissing
  // the notice (ValueList's `.act.ack-merge` button).
  await expect(v2Row.locator('.act.ack-merge')).toBeVisible();
  await captureState(page, 'variant_mergingDuplicate');
});

test('variant_confirmingDelete: × on a variant shows modal-shaped prompt', async ({ page }) => {
  await openInspector(page);
  // After auto-EDIT, v1 sits in `editingValue`. DELETE from there lands
  // in `confirmingDelete` with preDeleteState='editingValue' (the
  // `editingValue` DELETE transition in variant.machine) — same modal-shaped UI as DELETE from `committed`,
  // either path is fine for the gallery PNG.
  const v1Row = rowsLocator(page).nth(1);
  await v1Row.locator('.act.delete').click();
  // confirmingDelete: modal-shaped, no parallel `$state` boolean. The
  // actor IS the prompt (ValueList.svelte).
  await expect(v1Row).toHaveClass(/(^|\s)confirming-delete(\s|$)/);
  await expect(v1Row.locator('.confirm-prompt'))
    .toContainText('Remove variant v1?');
  await expect(v1Row.locator('.act.confirm-yes')).toBeVisible();
  await expect(v1Row.locator('.act.confirm-no')).toBeVisible();
  await captureState(page, 'variant_confirmingDelete');
});

test('aggregator_allCommitted: post-Lock-All steady state disables the button', async ({ page }) => {
  await openInspector(page);
  // Delete v1 first so the only commitable surface is the base row.
  // The auto-EDIT in `spawnVariantActor` (inspector/rowActors.ts) re-opens any variant in
  // `committed` back to `editingValue`, so a fixture with v1 never
  // reaches a stable "all locked" steady state. Removing v1 leaves base
  // alone, which auto-EDIT does NOT re-open from `committed` (its
  // base-row branch only matches `viewing`).
  const v1Row = rowsLocator(page).nth(1);
  await v1Row.locator('.act.delete').click();
  await v1Row.locator('.act.confirm-yes').click();
  await expect(rowsLocator(page)).toHaveCount(1);
  // Commit the base row WITHOUT changing the draft — same-value commit
  // (see paramEditor_committed for rationale) keeps the actor stable in
  // `committed` instead of round-tripping back to `editing`.
  // paramEditorAggregator transitions committingAll → allCommitted;
  // nodeHasDirty becomes false; the Lock All button picks up the
  // disabled attribute (inspector/MethodPanel.svelte).
  const baseRow = rowsLocator(page).first();
  await baseRow.locator('.act.commit').click();
  await expect(baseRow).not.toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(page.locator('.lock-all')).toBeDisabled();
  await captureState(page, 'aggregator_allCommitted');
});
