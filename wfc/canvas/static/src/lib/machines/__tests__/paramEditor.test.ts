/**
 * Vitest suite for the per-row paramEditorActor (ADR-016 Phase 2).
 *
 * Each test corresponds to a user story or canonical bug scenario:
 *
 *   - T1 (US-1) — cross-row isolation: two actors, drive one, second
 *     stays in `viewing` with original context. Replaces 0.2.7's
 *     `dirtyParams` Set bleed by construction.
 *   - T2 (US-2) — happy path: viewing → editing → committing →
 *     committed; final value carried in context.
 *   - T3 — invalid → re-edit: coerce fails, machine lands in `invalid`,
 *     `CHANGE_VALUE` re-enters `editing` with validationError cleared.
 *     Asserts Edge Case #4 (don't lock the user out).
 *   - T4 — blank optional numeric: int + required=false + draft=''
 *     commits to value=null (NOT invalid). Preserves the 0.2.13
 *     blank-commit precedent so the upcoming expand phase doesn't
 *     regress numeric rows.
 *
 * The `committing` state invokes a `fromPromise` coerce service; tests
 * await one microtask after `COMMIT` so the promise resolves and
 * `onDone` fires before assertions.
 */
import { describe, expect, it } from 'vitest';
import { createActor, fromPromise } from 'xstate';
import {
  makeParamEditorMachine,
  type CoerceInput,
  type CoerceResult,
  type ParamEditorInput,
} from '../paramEditor.machine';

function makeActor(input: ParamEditorInput) {
  const machine = makeParamEditorMachine();
  return createActor(machine, { input });
}

// Track 2 (ADR-017) Pipeline Variables — per D-4, bind/unbind owned by
// the actor. The five tests at the bottom of this file (US-2 BIND_VARIABLE,
// EDIT-from-bound, picker open/close, spawn-input seed, etc.) prove the
// machine is the single source of truth for row binding state. UI tests
// in the Vitest UI suite cover the picker render path; these tests cover
// the state-transition contract in isolation.

// Default input — type:str row matching the spike target (C-2).
const baseInput: ParamEditorInput = {
  nodeId: 'node_1',
  paramName: 'sample_name',
  paramType: 'string',
  required: false,
  currentValue: 'old',
};

async function tick(): Promise<void> {
  // Yield to the microtask queue so `fromPromise` invokes resolve and
  // `onDone` transitions fire before the next assertion.
  await new Promise(r => setTimeout(r, 0));
}

describe('paramEditorActor', () => {
  it('cross-row isolation: editing one row does not affect another (US-1, replaces 0.2.7)', async () => {
    // The 0.2.7 bug was a single `dirtyParams: Set<string>` that two
    // rows mutated concurrently. With per-instance actors each row's
    // state lives in its own context, so simultaneous editing of one
    // row leaves any other row's state untouched.
    const rowA = makeActor({
      ...baseInput,
      paramName: 'sample_name',
      currentValue: 'a-original',
    });
    const rowB = makeActor({
      ...baseInput,
      paramName: 'output_dir',
      currentValue: 'b-original',
    });
    rowA.start();
    rowB.start();

    expect(rowA.getSnapshot().value).toBe('viewing');
    expect(rowB.getSnapshot().value).toBe('viewing');

    // Drive rowA all the way through commit.
    rowA.send({ type: 'EDIT' });
    rowA.send({ type: 'CHANGE_VALUE', value: 'a-edited' });
    rowA.send({ type: 'COMMIT' });
    await tick();

    expect(rowA.getSnapshot().value).toBe('committed');
    expect(rowA.getSnapshot().context.currentValue).toBe('a-edited');

    // rowB never received any event — its state and context must be
    // untouched. This is the assertion that fails under the 0.2.7 bug
    // model and passes by construction with per-instance actors.
    const bSnap = rowB.getSnapshot();
    expect(bSnap.value).toBe('viewing');
    expect(bSnap.context.currentValue).toBe('b-original');
    expect(bSnap.context.draftValue).toBe('');
    expect(bSnap.context.validationError).toBeNull();

    rowA.stop();
    rowB.stop();
  });

  it('happy path: viewing -> editing -> committing -> committed carries typed value (US-2)', async () => {
    const actor = makeActor({ ...baseInput, currentValue: 'old' });
    actor.start();
    expect(actor.getSnapshot().value).toBe('viewing');

    actor.send({ type: 'EDIT' });
    expect(actor.getSnapshot().value).toBe('editing');
    // EDIT seeds draftValue from currentValue so an "edit" that ends in
    // commit-without-typing preserves the existing value.
    expect(actor.getSnapshot().context.draftValue).toBe('old');

    actor.send({ type: 'CHANGE_VALUE', value: 'new-value' });
    expect(actor.getSnapshot().context.draftValue).toBe('new-value');

    actor.send({ type: 'COMMIT' });
    // COMMIT transitions to committing synchronously; the invoked
    // promise needs a microtask to resolve before onDone fires.
    expect(actor.getSnapshot().value).toBe('committing');
    await tick();

    const snap = actor.getSnapshot();
    expect(snap.value).toBe('committed');
    expect(snap.context.currentValue).toBe('new-value');
    expect(snap.context.validationError).toBeNull();
    actor.stop();
  });

  it('invalid -> re-edit clears validationError and accepts new draft (Edge Case #4)', async () => {
    // Inject a coerce stub that rejects the first attempt. We don't use
    // the production coerce service here because we want to exercise
    // the invalid → editing transition directly.
    const failingCoerce = fromPromise<CoerceResult, CoerceInput>(
      async () => ({ ok: false, error: 'mock failure' }),
    );
    const machine = makeParamEditorMachine().provide({
      actors: { coerceAndValidate: failingCoerce },
    });
    const actor = createActor(machine, { input: { ...baseInput } });
    actor.start();

    actor.send({ type: 'EDIT' });
    actor.send({ type: 'CHANGE_VALUE', value: 'bogus' });
    actor.send({ type: 'COMMIT' });
    await tick();

    expect(actor.getSnapshot().value).toBe('invalid');
    expect(actor.getSnapshot().context.validationError).toBe('mock failure');

    // CHANGE_VALUE from invalid must re-enter editing AND clear the
    // stale error so the user isn't locked out (Edge Case #4).
    actor.send({ type: 'CHANGE_VALUE', value: 'better' });
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('editing');
    expect(snap.context.draftValue).toBe('better');
    expect(snap.context.validationError).toBeNull();
    actor.stop();
  });

  it('blank commit on optional numeric coerces to null, not invalid (preserves 0.2.13)', async () => {
    // The 0.2.13 fix taught the param editor that an optional numeric
    // param committed blank means "absent" (engine .get() returns None).
    // The new machine must preserve this so when expand-phase migrates
    // numeric rows to the actor they don't regress.
    const actor = makeActor({
      nodeId: 'node_1',
      paramName: 'maybe_count',
      paramType: 'int',
      required: false,
      currentValue: 5,
    });
    actor.start();

    actor.send({ type: 'EDIT' });
    actor.send({ type: 'CHANGE_VALUE', value: '' });
    actor.send({ type: 'COMMIT' });
    await tick();

    const snap = actor.getSnapshot();
    expect(snap.value).toBe('committed');
    expect(snap.context.currentValue).toBeNull();
    expect(snap.context.validationError).toBeNull();
    actor.stop();
  });
});

// ── Track 2 (ADR-017) — Pipeline Variables binding ───────────────────────

describe('paramEditorActor — Pipeline Variables binding (ADR-017 / D-4)', () => {
  it('US-2 BIND_VARIABLE: viewing → bound preserves currentValue', () => {
    const actor = makeActor({ ...baseInput, currentValue: 'label' });
    actor.start();
    actor.send({ type: 'BIND_VARIABLE', name: 'col' });
    let snap = actor.getSnapshot();
    expect(snap.value).toBe('bound');
    expect(snap.context.boundVariable).toBe('col');
    expect(snap.context.currentValue).toBe('label');
    actor.send({ type: 'UNBIND_VARIABLE' });
    snap = actor.getSnapshot();
    expect(snap.value).toBe('viewing');
    expect(snap.context.boundVariable).toBeNull();
    actor.stop();
  });

  it('US-2 EDIT from bound breaks binding (edge case 12)', () => {
    const actor = makeActor({ ...baseInput, currentValue: 'label', boundVariable: 'col' });
    actor.start();
    // Spawn-input seed should land directly in `bound` via the always-guard.
    expect(actor.getSnapshot().value).toBe('bound');
    actor.send({ type: 'EDIT' });
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('editing');
    expect(snap.context.boundVariable).toBeNull();
    expect(snap.context.draftValue).toBe('label');
    actor.stop();
  });

  it('US-2 OPEN/CLOSE_BIND_PICKER toggles bindPickerOpen (no component-local flag)', () => {
    const actor = makeActor(baseInput);
    actor.start();
    expect(actor.getSnapshot().context.bindPickerOpen).toBe(false);
    actor.send({ type: 'OPEN_BIND_PICKER' });
    expect(actor.getSnapshot().context.bindPickerOpen).toBe(true);
    actor.send({ type: 'CLOSE_BIND_PICKER' });
    expect(actor.getSnapshot().context.bindPickerOpen).toBe(false);
    actor.stop();
  });

  it('US-2 spawn-input seeds bound state when boundVariable provided', () => {
    const actor = makeActor({ ...baseInput, currentValue: 'X', boundVariable: 'colmap' });
    actor.start();
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('bound');
    expect(snap.context.boundVariable).toBe('colmap');
    actor.stop();
  });

  it('US-2 BIND_VARIABLE from committed transitions to bound (Reviewer iter 1 fix)', async () => {
    // Drive a successful commit to land in `committed`, then bind.
    const actor = makeActor(baseInput);
    actor.start();
    actor.send({ type: 'EDIT' });
    actor.send({ type: 'CHANGE_VALUE', value: 'new-literal' });
    actor.send({ type: 'COMMIT' });
    await tick();
    expect(actor.getSnapshot().value).toBe('committed');

    actor.send({ type: 'BIND_VARIABLE', name: 'colmap' });
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('bound');
    expect(snap.context.boundVariable).toBe('colmap');
    expect(snap.context.draftValue).toBe('');
    expect(snap.context.validationError).toBeNull();
    actor.stop();
  });

  it('US-2 BIND_VARIABLE from invalid transitions to bound and clears validationError (Reviewer iter 1 fix)', async () => {
    // Drive a failed commit (int param + non-numeric draft) to land in `invalid`.
    const actor = makeActor({ ...baseInput, paramType: 'int', required: true });
    actor.start();
    actor.send({ type: 'EDIT' });
    actor.send({ type: 'CHANGE_VALUE', value: 'not-a-number' });
    actor.send({ type: 'COMMIT' });
    await tick();
    expect(actor.getSnapshot().value).toBe('invalid');
    expect(actor.getSnapshot().context.validationError).not.toBeNull();

    actor.send({ type: 'BIND_VARIABLE', name: 'numvar' });
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('bound');
    expect(snap.context.boundVariable).toBe('numvar');
    expect(snap.context.validationError).toBeNull();
    expect(snap.context.draftValue).toBe('');
    actor.stop();
  });
});
