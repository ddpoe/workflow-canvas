/**
 * Vitest suite for the per-variant variantActor (ADR-016 Phase 2 expand).
 *
 * Each test is one user story or load-bearing transition:
 *
 *   - T1 (US-3 happy path): noVariants → addingVariant → editingValue
 *     → committing → committed. Drives the full lifecycle for a fresh
 *     variant with no sibling collision.
 *   - T2 (US-3 dedup-merge): editingValue → committing → mergingDuplicate
 *     when the coerced draft equals an existing sibling. Replaces the
 *     "you can have two v's with the same value" pre-cycle behavior.
 *   - T3 (US-3 confirm-delete then cancel): committed → confirmingDelete
 *     → committed (preserves currentValue + validationError on cancel).
 *     Asserts Edge Case #8 (modal-shaped, no parallel $state flag).
 *   - T4 (Edge Case #3 sibling broadcast): SIBLINGS_CHANGED updates the
 *     dedup target list. A draft committed BEFORE the broadcast is
 *     unique; the same draft committed AFTER broadcasting that it now
 *     matches a sibling lands in mergingDuplicate. This is the test the
 *     pitch flagged: snapshot-at-spawn would let two variants commit
 *     identical values when edited concurrently; broadcast keeps it
 *     consistent.
 */
import { describe, expect, it } from 'vitest';
import { createActor } from 'xstate';
import {
  makeVariantMachine,
  type VariantInput,
} from '../variant.machine';

const baseInput: VariantInput = {
  paramName: 'threshold',
  variantId: 'v1',
  paramType: 'string',
  required: false,
  currentValue: '', // empty so boot lands in noVariants
};

function tick(): Promise<void> {
  return new Promise(r => setTimeout(r, 0));
}

describe('variantActor', () => {
  it('happy path: noVariants -> addingVariant -> editingValue -> committing -> committed (US-3)', async () => {
    const actor = createActor(makeVariantMachine(), { input: { ...baseInput } });
    actor.start();

    // boot's `always` lands a brand-new variant in noVariants because
    // currentValue is ''.
    expect(actor.getSnapshot().value).toBe('noVariants');

    actor.send({ type: 'ADD_VARIANT' });
    expect(actor.getSnapshot().value).toBe('addingVariant');

    actor.send({ type: 'CHANGE_VALUE', value: 'foo' });
    expect(actor.getSnapshot().value).toBe('editingValue');
    expect(actor.getSnapshot().context.draftValue).toBe('foo');

    actor.send({ type: 'COMMIT' });
    expect(actor.getSnapshot().value).toBe('committing');
    await tick();

    const snap = actor.getSnapshot();
    expect(snap.value).toBe('committed');
    expect(snap.context.currentValue).toBe('foo');
    expect(snap.context.validationError).toBeNull();
    actor.stop();
  });

  it('committing into a duplicate sibling lands in mergingDuplicate (US-3 dedup)', async () => {
    const actor = createActor(makeVariantMachine(), {
      input: {
        ...baseInput,
        currentValue: '', // start in noVariants
        siblingValues: ['a', 'b'],
      },
    });
    actor.start();
    expect(actor.getSnapshot().value).toBe('noVariants');

    actor.send({ type: 'ADD_VARIANT' });
    actor.send({ type: 'CHANGE_VALUE', value: 'a' }); // dup of sibling
    actor.send({ type: 'COMMIT' });
    await tick();

    const snap = actor.getSnapshot();
    expect(snap.value).toBe('mergingDuplicate');
    // currentValue still gets assigned so the merge UI can show what
    // value collided.
    expect(snap.context.currentValue).toBe('a');
    actor.stop();
  });

  it('confirmingDelete -> CANCEL_DELETE returns to committed with context intact (Edge Case #8)', async () => {
    const actor = createActor(makeVariantMachine(), {
      input: {
        ...baseInput,
        currentValue: 'kept', // boot routes to committed
      },
    });
    actor.start();
    // boot.always sees currentValue='kept' and routes to committed.
    expect(actor.getSnapshot().value).toBe('committed');
    expect(actor.getSnapshot().context.currentValue).toBe('kept');

    actor.send({ type: 'DELETE' });
    expect(actor.getSnapshot().value).toBe('confirmingDelete');

    // CANCEL_DELETE from a committed-origin DELETE returns to committed,
    // not editingValue.
    actor.send({ type: 'CANCEL_DELETE' });
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('committed');
    expect(snap.context.currentValue).toBe('kept');
    expect(snap.context.validationError).toBeNull();
    actor.stop();
  });

  it('SIBLINGS_CHANGED broadcast updates dedup target list mid-edit (Edge Case #3)', async () => {
    // The bug snapshot-at-spawn would hide: two variantActors are
    // editing simultaneously. The first commits 'foo'. The aggregator
    // broadcasts SIBLINGS_CHANGED: ['foo']. The second's pre-broadcast
    // siblingValues was [], so without the broadcast it would commit
    // 'foo' as unique. With the broadcast, the second's commit lands
    // in mergingDuplicate.
    const actor = createActor(makeVariantMachine(), {
      input: {
        ...baseInput,
        currentValue: '', // noVariants
        siblingValues: [], // pretend no siblings at spawn time
      },
    });
    actor.start();
    expect(actor.getSnapshot().value).toBe('noVariants');

    actor.send({ type: 'ADD_VARIANT' });
    actor.send({ type: 'CHANGE_VALUE', value: 'foo' });
    expect(actor.getSnapshot().value).toBe('editingValue');

    // Parent broadcasts that 'foo' just got committed by another sibling.
    actor.send({ type: 'SIBLINGS_CHANGED', siblingValues: ['foo'] });
    expect(actor.getSnapshot().context.siblingValues).toEqual(['foo']);

    // Now COMMIT — coerce returns ok with value='foo', the duplicate
    // guard sees 'foo' in siblingValues, routes to mergingDuplicate.
    actor.send({ type: 'COMMIT' });
    await tick();

    expect(actor.getSnapshot().value).toBe('mergingDuplicate');
    actor.stop();
  });
});
