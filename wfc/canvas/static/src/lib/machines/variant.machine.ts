/**
 * Per-variant lifecycle machine (ADR-016 Phase 2 expand).
 *
 * Sister machine to `paramEditorActor`. Each variant row in
 * `ValueList.svelte` spawns one `variantActor` instead of carrying its
 * own `editing[row.id]` / `localValue[row.id]` / `errorMsg[row.id]`
 * trio. Adding the dedicated machine (rather than reusing
 * `paramEditorActor`) buys two user-visible affordances the architect
 * pitch lists as load-bearing: dedup-merge against sibling values
 * (US-3) and modal-shaped delete confirmation (Edge Case #8).
 *
 * State map (matches ADR §future-phase-2 sketch):
 *
 *   noVariants    -- ADD_VARIANT --> addingVariant
 *   addingVariant -- CHANGE_VALUE --> editingValue
 *   addingVariant -- CANCEL --> noVariants
 *   editingValue  -- CHANGE_VALUE --> editingValue       (draft updates)
 *   editingValue  -- COMMIT --> committing
 *   editingValue  -- CANCEL --> committed                (revert to last committed)
 *   editingValue  -- DELETE --> confirmingDelete
 *   committing    -- (ok && dup)  --> mergingDuplicate
 *   committing    -- (ok && !dup) --> committed
 *   committing    -- (err)        --> editingValue       (re-edit clears error)
 *   mergingDuplicate -- ACK_MERGE --> committed
 *   committed     -- EDIT --> editingValue               (re-open after commit)
 *   committed     -- DELETE --> confirmingDelete
 *   confirmingDelete -- CONFIRM_DELETE --> deleted (final)
 *   confirmingDelete -- CANCEL_DELETE --> committed | editingValue (restored prior)
 *   *             -- SIBLINGS_CHANGED --> (assigns siblingValues; no transition)
 *
 * The dedup check uses the parent's broadcast `SIBLINGS_CHANGED` event
 * to keep `siblingValues` fresh — per Edge Case #3, snapshotting at
 * spawn time would let two variants commit the same value if the user
 * edits both quickly in succession.
 */
import { fromPromise, setup, assign, type ActorRefFrom } from 'xstate';
import {
  coerceAndValidate as defaultCoerceAndValidate,
  type CoerceInput,
  type CoerceResult,
  type ParamEditorType,
} from './paramEditor.machine';

// ── Context ────────────────────────────────────────────────────────────

export interface VariantContext {
  paramName: string;
  variantId: string; // e.g. "v1", "v2"
  paramType: ParamEditorType;
  required: boolean;
  enumOptions?: string[];
  min?: number;
  max?: number;
  currentValue: unknown;
  draftValue: string | boolean;
  validationError: string | null;
  // Sibling values from same param (excluding self). Refreshed via
  // SIBLINGS_CHANGED broadcasts from the aggregator/parent.
  siblingValues: unknown[];
  // Tracks the prior state to restore from confirmingDelete on cancel.
  // Either 'committed' or 'editingValue' depending on where DELETE
  // arrived from. Default 'committed' since most deletes are from the
  // locked row.
  preDeleteState: 'committed' | 'editingValue';
}

// ── Events ─────────────────────────────────────────────────────────────

export type VariantEvent =
  | { type: 'ADD_VARIANT' }
  | { type: 'EDIT' }
  | { type: 'CHANGE_VALUE'; value: string | boolean }
  | { type: 'COMMIT' }
  | { type: 'CANCEL' }
  | { type: 'DELETE' }
  | { type: 'CONFIRM_DELETE' }
  | { type: 'CANCEL_DELETE' }
  | { type: 'ACK_MERGE' }
  | { type: 'SIBLINGS_CHANGED'; siblingValues: unknown[] }
  | { type: 'RESET_TO'; value: unknown };

export interface VariantInput {
  paramName: string;
  variantId: string;
  paramType: ParamEditorType;
  required?: boolean;
  enumOptions?: string[];
  min?: number;
  max?: number;
  currentValue: unknown;
  siblingValues?: unknown[];
  // Initial state — 'committed' for an existing variant, 'noVariants'
  // for a brand-new spawn before ADD_VARIANT.
  initialState?: 'committed' | 'noVariants';
}

// ── Helpers ────────────────────────────────────────────────────────────

/**
 * Deep-equality check appropriate for variant values.
 *
 * Variant values are coerced primitives (string, number, boolean, null)
 * or JSON-coerced objects/arrays. JSON.stringify gives stable equality
 * for those without pulling in a deps-heavy deepEqual. NaN won't appear
 * because coerce rejects it; null is JSON-equal to null which is fine.
 */
function valuesEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null) return a === b;
  if (typeof a !== typeof b) return false;
  if (typeof a === 'object' || typeof b === 'object') {
    try {
      return JSON.stringify(a) === JSON.stringify(b);
    } catch {
      return false;
    }
  }
  return false;
}

function isDuplicate(value: unknown, siblings: unknown[]): boolean {
  for (const s of siblings) {
    if (valuesEqual(value, s)) return true;
  }
  return false;
}

// ── Machine factory ────────────────────────────────────────────────────

export function makeVariantMachine() {
  return setup({
    types: {} as {
      context: VariantContext;
      events: VariantEvent;
      input: VariantInput;
    },
    actors: {
      coerceAndValidate: defaultCoerceAndValidate,
    },
    guards: {
      isDuplicate: ({ context, event }) => {
        // Guard runs on the invoke onDone; event.output is the
        // CoerceResult. Only call after we know ok===true.
        const out = (event as unknown as { output: CoerceResult }).output;
        if (!out.ok) return false;
        return isDuplicate(out.value, context.siblingValues);
      },
      isCoerceOk: ({ event }) => {
        const out = (event as unknown as { output: CoerceResult }).output;
        return out.ok === true;
      },
    },
  }).createMachine({
    id: 'variant',
    // Inline initial selector via choosing during spawn input. xstate v5
    // doesn't accept dynamic `initial` from input directly — workaround
    // is a single transient "boot" state with `always` guards.
    initial: 'boot',
    context: ({ input }) => ({
      paramName: input.paramName,
      variantId: input.variantId,
      paramType: input.paramType,
      required: input.required ?? false,
      enumOptions: input.enumOptions,
      min: input.min,
      max: input.max,
      currentValue: input.currentValue,
      draftValue: '',
      validationError: null,
      siblingValues: input.siblingValues ?? [],
      preDeleteState: 'committed',
    }),
    on: {
      // Sibling broadcast — assignable from any state. Aggregator/parent
      // sends one SIBLINGS_CHANGED whenever a sibling commits.
      SIBLINGS_CHANGED: {
        actions: assign({
          siblingValues: ({ event }) =>
            (event as { siblingValues: unknown[] }).siblingValues,
        }),
      },
      RESET_TO: {
        actions: assign({
          currentValue: ({ event }) => (event as { value: unknown }).value,
        }),
      },
    },
    states: {
      boot: {
        // Two-shot routing: respect input.initialState. New variants
        // start in 'noVariants' and transition via ADD_VARIANT; existing
        // variants (loaded from saved pipeline) start in 'committed'.
        always: [
          {
            guard: ({ context }) =>
              // existence is the default — every spawned variantActor
              // for a saved variant should land in committed.
              context.currentValue !== undefined && context.currentValue !== '',
            target: 'committed',
          },
          { target: 'noVariants' },
        ],
      },
      noVariants: {
        on: {
          ADD_VARIANT: { target: 'addingVariant' },
        },
      },
      addingVariant: {
        on: {
          CHANGE_VALUE: {
            target: 'editingValue',
            actions: assign({
              draftValue: ({ event }) =>
                (event as { value: string | boolean }).value,
              validationError: () => null,
            }),
          },
          CANCEL: { target: 'noVariants' },
        },
      },
      editingValue: {
        on: {
          CHANGE_VALUE: {
            actions: assign({
              draftValue: ({ event }) =>
                (event as { value: string | boolean }).value,
              validationError: () => null,
            }),
          },
          COMMIT: { target: 'committing' },
          CANCEL: {
            // CANCEL from editingValue restores last committed value
            // (or clears back to noVariants if there's no prior commit
            // — boot defaults that to currentValue!=='' so addingVariant
            // → editingValue → CANCEL goes back to noVariants).
            target: 'committed',
            actions: assign({
              draftValue: () => '',
              validationError: () => null,
            }),
          },
          DELETE: {
            target: 'confirmingDelete',
            actions: assign({
              preDeleteState: () => 'editingValue' as const,
            }),
          },
        },
      },
      committing: {
        invoke: {
          id: 'coerce',
          src: 'coerceAndValidate',
          input: ({ context }): CoerceInput => ({
            raw: context.draftValue,
            paramType: context.paramType,
            required: context.required,
            enumOptions: context.enumOptions,
            min: context.min,
            max: context.max,
          }),
          onDone: [
            // Coerce ok AND duplicate of an existing sibling → merge UI.
            {
              target: 'mergingDuplicate',
              guard: 'isDuplicate',
              actions: assign({
                currentValue: ({ event }) =>
                  event.output.ok ? event.output.value : undefined,
                draftValue: () => '',
                validationError: () => null,
              }),
            },
            // Coerce ok and unique → committed.
            {
              target: 'committed',
              guard: 'isCoerceOk',
              actions: assign({
                currentValue: ({ event }) =>
                  event.output.ok ? event.output.value : undefined,
                draftValue: () => '',
                validationError: () => null,
              }),
            },
            // Coerce error → re-edit with the error visible.
            {
              target: 'editingValue',
              actions: assign({
                validationError: ({ event }) =>
                  !event.output.ok ? event.output.error : null,
              }),
            },
          ],
          onError: {
            target: 'editingValue',
            actions: assign({
              validationError: () => 'Coercion failed.',
            }),
          },
        },
      },
      mergingDuplicate: {
        // The user sees a "merged into v1" affordance; clicking it
        // (or pressing Enter) confirms the merge by transitioning to
        // committed. Component layer is responsible for hiding the
        // duplicate row in the rendered list (the aggregator/parent
        // looks at the variant dict and dedups), so this state's job
        // is purely UX: tell the user the merge happened.
        on: {
          ACK_MERGE: { target: 'committed' },
          // DELETE from mergingDuplicate skips the confirmation since
          // the variant has effectively no unique value to lose.
          DELETE: { target: 'deleted' },
        },
      },
      committed: {
        on: {
          EDIT: {
            target: 'editingValue',
            actions: assign({
              draftValue: ({ context }) => {
                const v = context.currentValue;
                if (typeof v === 'boolean') return v;
                if (v === null || v === undefined) return '';
                if (typeof v === 'object') {
                  try { return JSON.stringify(v); } catch { return ''; }
                }
                return String(v);
              },
              validationError: () => null,
            }),
          },
          DELETE: {
            target: 'confirmingDelete',
            actions: assign({
              preDeleteState: () => 'committed' as const,
            }),
          },
        },
      },
      confirmingDelete: {
        // Edge Case #8: modal-shaped state. Holds until the user
        // confirms or cancels. No parallel `$state` boolean — the UI
        // reads `state.matches('confirmingDelete')`.
        on: {
          CONFIRM_DELETE: { target: 'deleted' },
          CANCEL_DELETE: [
            {
              target: 'editingValue',
              guard: ({ context }) => context.preDeleteState === 'editingValue',
            },
            { target: 'committed' },
          ],
        },
      },
      deleted: {
        // Final state. The component sees `snapshot.status === 'done'`
        // (or matches('deleted')) and removes the variant from the
        // parent's variants dict; the actor itself stops via the
        // component's onDestroy.
        type: 'final',
      },
    },
  });
}

export type VariantActor = ActorRefFrom<ReturnType<typeof makeVariantMachine>>;
