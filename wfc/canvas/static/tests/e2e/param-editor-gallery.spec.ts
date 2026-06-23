/**
 * ADR-016 Phase 2 gallery: per-state PNG capture for the new
 * paramEditor / variant / paramEditorAggregator actors merged at 4e5afea.
 *
 * Pattern decision (mini-pitch, /pev-instance, 2026-05-01): option (b) —
 * a separate spec file driven by Playwright clicks/typing, mirroring the
 * `behaviors.spec.ts:383` screenshot drop convention WITHOUT extending
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
 * Constraint: NO changes to `paramEditor.machine.ts` / `variant.machine.ts`
 * / `paramEditorAggregator.machine.ts` / `ValueList.svelte`. The actors
 * are already correct; this cycle adds visual artifacts only.
 */
import { expect, test, type Page } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

const FIXTURE_URL = '/?fixture=param-editor';

async function openInspector(page: Page): Promise<void> {
  await page.goto(FIXTURE_URL);
  await page.waitForSelector('.svelte-flow', { timeout: 10_000 });
  await page.locator('[data-id="method_a"]').click();
  // ValueList rows render once the Inspector mounts. Both base and v1
  // auto-EDIT on first mount per the legacy "rows open by default"
  // baseline (ValueList.svelte:393-417), so the first paint already
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
  // `editing` class (ValueList.svelte:594) and the gutter shows 🔓.
  await expect(baseRow).toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(baseRow.locator('.gutter')).toHaveText('🔓');
  // The text input is rendered as the editable form (no `readonly`),
  // distinguishing this from the `viewing` / `committed` paint where
  // ValueList renders `<input ... readonly>`.
  const baseInput = baseRow.locator('input.text');
  await expect(baseInput).toBeEditable();
  await page.screenshot({
    path: 'gallery/states/paramEditor_editing.png',
    fullPage: true,
  });
});

test('paramEditor_invalid: required-empty commit lands in invalid', async ({ page }) => {
  await openInspector(page);
  const baseRow = rowsLocator(page).first();
  // Clear the seeded "hello" draft to force the coerce required-check.
  await baseRow.locator('input.text').fill('');
  await baseRow.locator('.act.commit').click();
  // committing → onDone (ok=false) → invalid (paramEditor.machine:312-329).
  await expect(baseRow).toHaveClass(/(^|\s)invalid(\s|$)/);
  // The `.row-error` sibling div appears immediately after `.row` when
  // validationError is set (ValueList.svelte:709-711). Asserting on the
  // text proves the error string from `coerceParamValue` reaches the DOM,
  // not just a class flip.
  await expect(page.locator('.value-list .row-error').first())
    .toContainText('Value cannot be empty.');
  await page.screenshot({
    path: 'gallery/states/paramEditor_invalid.png',
    fullPage: true,
  });
});

test('paramEditor_committed: successful commit locks the base row', async ({ page }) => {
  await openInspector(page);
  const baseRow = rowsLocator(page).first();
  // Don't modify the draft — committing the seeded "hello" round-trips
  // the same value, and the spawn-time subscription's same-value guard
  // (ValueList.svelte:166-168) suppresses `onBaseChange`. Without that
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
  await page.screenshot({
    path: 'gallery/states/paramEditor_committed.png',
    fullPage: true,
  });
});

test('variant_addingVariant: + variant creates a new editing-shaped row', async ({ page }) => {
  await openInspector(page);
  const beforeCount = await rowsLocator(page).count();
  // The "+ variant" button is rendered either as `.add-variant-inline`
  // (multi-row) or `.add-variant-btn` (single-row). Both carry the same
  // text, so getByRole hits whichever is currently mounted.
  await page.getByRole('button', { name: '+ variant' }).click();
  await expect(rowsLocator(page)).toHaveCount(beforeCount + 1);
  // The new row is the last one; it lands in `addingVariant` (variant.
  // machine:204-208) which `snapForRow` reports as editing-shaped
  // (ValueList.svelte:382-383).
  const newRow = rowsLocator(page).last();
  await expect(newRow).toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(newRow.locator('.gutter')).toHaveText('🔓');
  await page.screenshot({
    path: 'gallery/states/variant_addingVariant.png',
    fullPage: true,
  });
});

test('variant_mergingDuplicate: dedup notice appears when v2 commits same value as v1', async ({ page }) => {
  await openInspector(page);
  // Add v2. v1's currentValue is "hello" from the seed; the parent's
  // `variants` prop is `{v1: 'hello'}`, so the new variantActor spawns
  // with siblingValues=['hello'] (ValueList.svelte:189 + 109-116) — no
  // need to commit v1 first.
  await page.getByRole('button', { name: '+ variant' }).click();
  // Rows: [base, v1, v2].
  const v2Row = rowsLocator(page).nth(2);
  await v2Row.locator('input.text').fill('hello');
  await v2Row.locator('.act.commit').click();
  // Coerce ok + isDuplicate guard true → mergingDuplicate
  // (variant.machine:264-274).
  await expect(v2Row).toHaveClass(/(^|\s)merging(\s|$)/);
  // Sibling notice div appears right after `.row` when state matches
  // `mergingDuplicate` (ValueList.svelte:703-707).
  await expect(page.locator('.value-list .row-merge-notice'))
    .toContainText('merged with sibling');
  // The ⇆ ack-merge button is the variant-only affordance for dismissing
  // the notice (ValueList.svelte:677-683).
  await expect(v2Row.locator('.act.ack-merge')).toBeVisible();
  await page.screenshot({
    path: 'gallery/states/variant_mergingDuplicate.png',
    fullPage: true,
  });
});

test('variant_confirmingDelete: × on a variant shows modal-shaped prompt', async ({ page }) => {
  await openInspector(page);
  // After auto-EDIT, v1 sits in `editingValue`. DELETE from there lands
  // in `confirmingDelete` with preDeleteState='editingValue' (variant.
  // machine:243-249) — same modal-shaped UI as DELETE from `committed`,
  // either path is fine for the gallery PNG.
  const v1Row = rowsLocator(page).nth(1);
  await v1Row.locator('.act.delete').click();
  // confirmingDelete: modal-shaped, no parallel `$state` boolean. The
  // actor IS the prompt (Edge Case #8, ValueList.svelte:602-616).
  await expect(v1Row).toHaveClass(/(^|\s)confirming-delete(\s|$)/);
  await expect(v1Row.locator('.confirm-prompt'))
    .toContainText('Remove variant v1?');
  await expect(v1Row.locator('.act.confirm-yes')).toBeVisible();
  await expect(v1Row.locator('.act.confirm-no')).toBeVisible();
  await page.screenshot({
    path: 'gallery/states/variant_confirmingDelete.png',
    fullPage: true,
  });
});

test('aggregator_allCommitted: post-Lock-All steady state disables the button', async ({ page }) => {
  await openInspector(page);
  // Delete v1 first so the only commitable surface is the base row.
  // ValueList's auto-EDIT effect (lines 393-417) re-opens any variant in
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
  // disabled attribute (InspectorPanel.svelte:1144-1149).
  const baseRow = rowsLocator(page).first();
  await baseRow.locator('.act.commit').click();
  await expect(baseRow).not.toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(page.locator('.lock-all')).toBeDisabled();
  await page.screenshot({
    path: 'gallery/states/aggregator_allCommitted.png',
    fullPage: true,
  });
});
