/**
 * Per-row param-editor lifecycle machine (ADR-016 Phase 2).
 *
 * Replaces the implicit edit-mode boolean + `dirtyParams` Set + ad-hoc
 * `commitAllSignal` counter that ValueList.svelte was using. Each canvas
 * row now spawns its own `paramEditorActor`, so:
 *
 *   - Cross-row bleed (0.2.7) is structurally impossible — every row's
 *     state lives in a separate actor with its own context. Two rows
 *     editing simultaneously cannot share `editing` / `validationError`
 *     under any name.
 *   - Commit-before-run (0.2.8) becomes an explicit transition into the
 *     `committed` final state instead of a microtask poke at a global
 *     counter. The Run-button's preflight (post-spike) awaits that
 *     transition rather than reading `dirtyParams.size`.
 *
 * Lifetime: one per visible row. Spawned by ValueList on mount, stopped
 * on unmount. Per ADR-016 §future-phase-2 + Phase 2 cycle decision D-4,
 * paramEditorActors live in their own inspector tree root — they are
 * NOT children of `pipelineRunActor` (run lifecycle and edit lifecycle
 * are conceptually unrelated; nesting would conflate them in the
 * inspector view).
 *
 * State map (matches ADR sketch):
 *
 *   viewing -- EDIT --> editing
 *   editing -- CHANGE_VALUE --> editing            (draft updates)
 *   editing -- CANCEL --> viewing                  (discard draft)
 *   editing -- COMMIT --> committing
 *   committing -- (coerceAndValidate ok)  --> committed   (final)
 *   committing -- (coerceAndValidate err) --> invalid
 *   invalid -- CHANGE_VALUE --> editing            (re-edit clears error)
 *   invalid -- CANCEL --> viewing
 *   committed -- EDIT --> editing                  (re-open after commit)
 *   * -- RESET_TO --> viewing                      (parent forces value)
 *
 * Pipeline Variables (ADR-017 / D-4) extension:
 *
 *   viewing -- BIND_VARIABLE { name } --> bound     (assigns boundVariable)
 *   editing -- BIND_VARIABLE { name } --> bound     (assigns boundVariable;
 *                                                    clears draftValue +
 *                                                    validationError)
 *   committed -- BIND_VARIABLE { name } --> bound   (assigns boundVariable;
 *                                                    clears draftValue +
 *                                                    validationError)
 *   invalid -- BIND_VARIABLE { name } --> bound     (assigns boundVariable;
 *                                                    clears draftValue +
 *                                                    validationError)
 *   bound -- UNBIND_VARIABLE --> viewing            (clears boundVariable)
 *   bound -- EDIT --> editing                       (clears boundVariable;
 *                                                    seeds draftValue from
 *                                                    currentValue)
 *   bound -- BIND_VARIABLE { name } --> bound       (rebind)
 *   * -- OPEN_BIND_PICKER --> (same state, sets context.bindPickerOpen=true)
 *   * -- CLOSE_BIND_PICKER --> (same state, sets context.bindPickerOpen=false)
 *
 * If the actor is spawned with ``input.boundVariable`` set, it lands in
 * ``bound`` instead of ``viewing`` (parsed pipeline JSON with ``{$var}``
 * refs hydrates rows directly into the bound state on spawn).
 *
 * `committed` is NOT a `final` state — re-edit is a normal user flow,
 * and locking the actor at first commit would force one actor per
 * commit cycle. ADR sketch lists `committed` as a "final" outcome of
 * the commit transition, not a terminal of the actor's lifecycle.
 *
 * Tests in `__tests__/paramEditor.test.ts` cover every transition that
 * matters for the 0.2.7 / 0.2.8 bug scenarios + the optional-numeric
 * blank-commit precedent (0.2.13).
 */
import { fromPromise, setup, assign, type ActorRefFrom } from 'xstate';

// ── Param type narrowing ──────────────────────────────────────────────
//
// The machine itself stays type-agnostic — the coerce service is what
// reads `paramType` and decides how to parse the draft. Listed here so
// callers from ValueList can narrow by the same vocabulary.

export type ParamEditorType =
  | 'string'
  | 'enum'
  | 'int'
  | 'float'
  | 'bool'
  | 'list'
  | 'dict';

// ── Context ────────────────────────────────────────────────────────────

export interface ParamEditorContext {
  // Identity. `nodeId` plus `paramName` is the spawn key. `dirtyKeySuffix`
  // is preserved verbatim from the legacy `markDirty` path so per-sample
  // override rows (which reuse the same nodeId+paramName) get a unique
  // identity in inspector breadcrumbs.
  nodeId: string;
  paramName: string;
  dirtyKeySuffix: string;
  // Param-shape inputs needed by the coerce service.
  paramType: ParamEditorType;
  required: boolean;
  enumOptions?: string[];
  min?: number;
  max?: number;
  // Lifecycle values.
  currentValue: unknown;
  draftValue: string | boolean;
  validationError: string | null;
  // Pipeline Variables (ADR-017): when non-null, this row is bound to
  // a pipeline variable by name. The literal `currentValue` is preserved
  // as the unbind fallback. UI reads `boundVariable` from the snapshot
  // to render the `→ varname` chip; aggregator commits with currentValue
  // (variable substitution happens server-side at submission).
  boundVariable: string | null;
  // Picker open-state lives on the actor (D-4): ValueList sends
  // OPEN_BIND_PICKER / CLOSE_BIND_PICKER instead of holding a local Svelte flag.
  bindPickerOpen: boolean;
}

// ── Events ─────────────────────────────────────────────────────────────

export type ParamEditorEvent =
  | { type: 'EDIT' }
  | { type: 'CHANGE_VALUE'; value: string | boolean }
  | { type: 'COMMIT' }
  | { type: 'CANCEL' }
  | { type: 'RESET_TO'; value: unknown }
  | { type: 'OPEN_BIND_PICKER' }
  | { type: 'CLOSE_BIND_PICKER' }
  | { type: 'BIND_VARIABLE'; name: string }
  | { type: 'UNBIND_VARIABLE' };

export interface ParamEditorInput {
  nodeId: string;
  paramName: string;
  dirtyKeySuffix?: string;
  paramType: ParamEditorType;
  required?: boolean;
  enumOptions?: string[];
  min?: number;
  max?: number;
  currentValue: unknown;
  /** When set, the actor lands in `bound` on spawn (rehydration from {$var} refs). */
  boundVariable?: string | null;
}

// ── coerceAndValidate service ──────────────────────────────────────────
//
// Pure function wrapped as `fromPromise`. The async wrapper is purely a
// machine-architecture choice (xstate `invoke.onDone` is the cleanest
// route into one of two target states based on a payload field) — the
// underlying coerce is synchronous.
//
// Output shape mirrors the legacy `ValueList.svelte::coerce()` return
// type: `{ ok: true, value }` or `{ ok: false, error }`. Behavior must
// preserve the 0.2.13 precedent: blank input on an optional numeric
// param coerces to `null`, NOT `invalid`.

export interface CoerceInput {
  raw: string | boolean;
  paramType: ParamEditorType;
  required: boolean;
  enumOptions?: string[];
  min?: number;
  max?: number;
}

export type CoerceResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string };

export function coerceParamValue(input: CoerceInput): CoerceResult {
  const { raw, paramType, required, enumOptions, min, max } = input;
  // Bool: short-circuit, raw is already boolean from a toggle widget.
  if (paramType === 'bool') {
    if (typeof raw === 'boolean') return { ok: true, value: raw };
    const lc = String(raw).trim().toLowerCase();
    if (lc === 'true' || lc === '1') return { ok: true, value: true };
    if (lc === 'false' || lc === '0') return { ok: true, value: false };
    return { ok: false, error: `Expected bool; got "${String(raw)}".` };
  }
  if (paramType === 'enum') {
    const s = String(raw);
    if (s === '') {
      if (required) return { ok: false, error: 'Value cannot be empty.' };
      return { ok: true, value: '' };
    }
    if (enumOptions && enumOptions.length > 0 && !enumOptions.includes(s)) {
      return { ok: false, error: `"${s}" is not one of: ${enumOptions.join(', ')}.` };
    }
    return { ok: true, value: s };
  }
  if (paramType === 'int' || paramType === 'float') {
    const raw_s = String(raw).trim();
    // 0.2.13 precedent: optional numeric blank coerces to null.
    if (raw_s === '') {
      if (required) return { ok: false, error: 'Value cannot be empty.' };
      return { ok: true, value: null };
    }
    if (paramType === 'int') {
      if (!/^-?\d+$/.test(raw_s)) return { ok: false, error: `Expected int; got "${raw_s}".` };
      const n = parseInt(raw_s, 10);
      if (Number.isNaN(n)) return { ok: false, error: `Expected int; got "${raw_s}".` };
      return constrain(n, min, max);
    }
    const n = parseFloat(raw_s);
    if (Number.isNaN(n)) return { ok: false, error: `Expected number; got "${raw_s}".` };
    return constrain(n, min, max);
  }
  if (paramType === 'list' || paramType === 'dict') {
    const s = String(raw).trim();
    if (s === '') {
      if (required) return { ok: false, error: 'Value cannot be empty.' };
      return { ok: true, value: paramType === 'list' ? [] : {} };
    }
    try {
      const parsed = JSON.parse(s);
      if (paramType === 'list' && !Array.isArray(parsed)) {
        return { ok: false, error: 'Expected JSON array.' };
      }
      if (paramType === 'dict' && (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed))) {
        return { ok: false, error: 'Expected JSON object.' };
      }
      return { ok: true, value: parsed };
    } catch (e) {
      return { ok: false, error: `Invalid JSON: ${(e as Error).message}` };
    }
  }
  // string / unknown
  const s = String(raw);
  if (required && s === '') return { ok: false, error: 'Value cannot be empty.' };
  return { ok: true, value: s };
}

function constrain(
  n: number,
  min: number | undefined,
  max: number | undefined,
): CoerceResult {
  if (min !== undefined && n < min) return { ok: false, error: `Must be >= ${min}.` };
  if (max !== undefined && n > max) return { ok: false, error: `Must be <= ${max}.` };
  return { ok: true, value: n };
}

// Default `fromPromise` wrapper for the coerce service. Tests can
// override via `.provide({ actors: { coerceAndValidate: ... } })` to
// inject a stub that always-fails or resolves with a fixed payload.
export const coerceAndValidate = fromPromise<CoerceResult, CoerceInput>(
  async ({ input }) => {
    return coerceParamValue(input);
  },
);

// ── Machine factory ────────────────────────────────────────────────────

export function makeParamEditorMachine() {
  return setup({
    types: {} as {
      context: ParamEditorContext;
      events: ParamEditorEvent;
      input: ParamEditorInput;
    },
    actors: {
      coerceAndValidate,
    },
  }).createMachine({
    id: 'paramEditor',
    // If spawned with input.boundVariable set, hydrate directly into the
    // `bound` state so parsed pipeline JSON with {$var} refs lands in the
    // right state on first render (no flash of viewing/literal).
    initial: 'viewing',
    context: ({ input }) => ({
      nodeId: input.nodeId,
      paramName: input.paramName,
      dirtyKeySuffix: input.dirtyKeySuffix ?? '',
      paramType: input.paramType,
      required: input.required ?? false,
      enumOptions: input.enumOptions,
      min: input.min,
      max: input.max,
      currentValue: input.currentValue,
      // `draftValue` mirrors the legacy `localValue[row.id]` — initialized
      // from currentValue when the row first enters `editing` (see
      // `EDIT` action below) so the user's first keystroke replaces the
      // committed value rather than appending to an empty string.
      draftValue: '',
      validationError: null,
      boundVariable: input.boundVariable ?? null,
      bindPickerOpen: false,
    }),
    // XState v5 `always` lets us pivot at spawn time based on the seeded
    // context. This is the documented pattern for input-driven initial
    // state without losing typed-input ergonomics.
    on: {
      OPEN_BIND_PICKER: {
        actions: assign({ bindPickerOpen: () => true }),
      },
      CLOSE_BIND_PICKER: {
        actions: assign({ bindPickerOpen: () => false }),
      },
    },
    states: {
      viewing: {
        // Spawn-time hydration: if input.boundVariable was set, jump to
        // `bound` immediately. Guards on context (set by the context
        // initializer above) so this runs once on entry and is a no-op
        // on subsequent re-entries (UNBIND_VARIABLE clears boundVariable
        // before transitioning back to viewing).
        always: {
          guard: ({ context }) => context.boundVariable !== null,
          target: 'bound',
        },
        on: {
          EDIT: {
            target: 'editing',
            actions: assign({
              // Seed draftValue from currentValue. Keeps boolean toggles
              // sticky and gives string/numeric editors a sensible
              // starting point.
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
          RESET_TO: {
            actions: assign({
              currentValue: ({ event }) => event.value,
            }),
          },
          BIND_VARIABLE: {
            target: 'bound',
            actions: assign({
              boundVariable: ({ event }) => event.name,
              bindPickerOpen: () => false,
            }),
          },
        },
      },
      bound: {
        on: {
          UNBIND_VARIABLE: {
            target: 'viewing',
            actions: assign({
              boundVariable: () => null,
              bindPickerOpen: () => false,
            }),
          },
          BIND_VARIABLE: {
            // Rebind to a different variable: stay in bound, swap the name.
            actions: assign({
              boundVariable: ({ event }) => event.name,
              bindPickerOpen: () => false,
            }),
          },
          EDIT: {
            // Edge case 12: editing a bound row breaks the binding in one
            // gesture. Atomic UNBIND_VARIABLE + EDIT.
            target: 'editing',
            actions: assign({
              boundVariable: () => null,
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
          RESET_TO: {
            target: 'viewing',
            actions: assign({
              currentValue: ({ event }) => event.value,
              boundVariable: () => null,
            }),
          },
        },
      },
      editing: {
        on: {
          CHANGE_VALUE: {
            actions: assign({
              draftValue: ({ event }) => event.value,
              // Clear stale error as soon as the user resumes typing.
              validationError: () => null,
            }),
          },
          COMMIT: { target: 'committing' },
          CANCEL: {
            target: 'viewing',
            actions: assign({
              draftValue: () => '',
              validationError: () => null,
            }),
          },
          RESET_TO: {
            target: 'viewing',
            actions: assign({
              currentValue: ({ event }) => event.value,
              draftValue: () => '',
              validationError: () => null,
            }),
          },
          BIND_VARIABLE: {
            target: 'bound',
            actions: assign({
              boundVariable: ({ event }) => event.name,
              draftValue: () => '',
              validationError: () => null,
              bindPickerOpen: () => false,
            }),
          },
        },
      },
      committing: {
        invoke: {
          id: 'coerce',
          src: 'coerceAndValidate',
          input: ({ context }) => ({
            raw: context.draftValue,
            paramType: context.paramType,
            required: context.required,
            enumOptions: context.enumOptions,
            min: context.min,
            max: context.max,
          }),
          onDone: [
            {
              target: 'committed',
              guard: ({ event }) => event.output.ok === true,
              actions: assign({
                currentValue: ({ event }) =>
                  event.output.ok ? event.output.value : undefined,
                draftValue: () => '',
                validationError: () => null,
              }),
            },
            {
              target: 'invalid',
              actions: assign({
                validationError: ({ event }) =>
                  !event.output.ok ? event.output.error : null,
              }),
            },
          ],
          // If the invoke itself rejects (shouldn't happen for the pure
          // wrapper, but tests may override with one that throws), route
          // to invalid with a generic error so the UI can recover.
          onError: {
            target: 'invalid',
            actions: assign({
              validationError: () => 'Coercion failed.',
            }),
          },
        },
      },
      committed: {
        // Non-terminal: re-editing a committed value is a normal user
        // flow (the locked-row icon → unlock click in the legacy UI).
        // Tests assert this state is reached after a successful commit;
        // they do NOT assert the actor stops here.
        on: {
          EDIT: {
            target: 'editing',
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
          RESET_TO: {
            target: 'viewing',
            actions: assign({
              currentValue: ({ event }) => event.value,
              draftValue: () => '',
            }),
          },
          BIND_VARIABLE: {
            target: 'bound',
            actions: assign({
              boundVariable: ({ event }) => event.name,
              draftValue: () => '',
              validationError: () => null,
              bindPickerOpen: () => false,
            }),
          },
        },
      },
      invalid: {
        // Edge Case #4: do NOT lock the user out. CHANGE_VALUE re-enters
        // `editing` with the validation error cleared so the next
        // keystroke offers a fresh attempt; CANCEL discards.
        on: {
          CHANGE_VALUE: {
            target: 'editing',
            actions: assign({
              draftValue: ({ event }) => event.value,
              validationError: () => null,
            }),
          },
          COMMIT: { target: 'committing' },
          CANCEL: {
            target: 'viewing',
            actions: assign({
              draftValue: () => '',
              validationError: () => null,
            }),
          },
          RESET_TO: {
            target: 'viewing',
            actions: assign({
              currentValue: ({ event }) => event.value,
              draftValue: () => '',
              validationError: () => null,
            }),
          },
          BIND_VARIABLE: {
            target: 'bound',
            actions: assign({
              boundVariable: ({ event }) => event.name,
              draftValue: () => '',
              validationError: () => null,
              bindPickerOpen: () => false,
            }),
          },
        },
      },
    },
  });
}

export type ParamEditorActor = ActorRefFrom<
  ReturnType<typeof makeParamEditorMachine>
>;
