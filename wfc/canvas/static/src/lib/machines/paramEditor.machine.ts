/**
 * Per-row param-editor lifecycle machine.
 *
 * Each canvas row spawns its own `paramEditorActor`, so:
 *
 *   - Cross-row bleed is structurally impossible — every row's
 *     state lives in a separate actor with its own context. Two rows
 *     editing simultaneously cannot share `editing` / `validationError`
 *     under any name.
 *   - Commit-before-run is an explicit transition into the `committed`
 *     state. The Run button's preflight awaits that transition.
 *
 * Lifetime: one per visible row. Spawned by ValueList on mount, stopped
 * on unmount. paramEditorActors live in their own inspector tree root —
 * they are NOT children of `pipelineRunActor` (run lifecycle and edit
 * lifecycle are conceptually unrelated; nesting would conflate them in
 * the inspector view).
 *
 * State map:
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
 * Pipeline Variables extension:
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
 * commit cycle. `committed` is the outcome of the commit transition,
 * not a terminal of the actor's lifecycle.
 *
 * Tests in `__tests__/paramEditor.test.ts` cover cross-row isolation,
 * invalid re-edit, blank commits on optional numeric params and
 * variable binding; `__tests__/paramEditorAggregator.test.ts` covers
 * commit-before-run.
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
  // gives per-sample override rows (which reuse the same nodeId+paramName)
  // a unique identity in inspector breadcrumbs.
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
  // Pipeline Variables: when non-null, this row is bound to
  // a pipeline variable by name. The literal `currentValue` is preserved
  // as the unbind fallback. UI reads `boundVariable` from the snapshot
  // to render the `→ varname` chip; aggregator commits with currentValue
  // (variable substitution happens server-side at submission).
  boundVariable: string | null;
  // Picker open-state lives on the actor: ValueList sends
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
// Output shape: `{ ok: true, value }` or `{ ok: false, error }`. Blank
// input on an optional numeric param coerces to `null`, NOT `invalid`.

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

// ── The one value rule ────────────────────────────────────────────────
//
// `checkParamValue` is the canvas copy of the server's
// `wfc/contracts/param_check.py::check_param_value`; both run the shared
// table `shared/paramValueOracle.json` and must give each row's verdict.

export interface ValueConstraints {
  min?: number;
  max?: number;
  enum?: unknown[];
}

function shown(value: unknown): string {
  let text: string;
  try {
    text = JSON.stringify(value) ?? String(value);
  } catch {
    text = String(value);
  }
  return text.length <= 60 ? text : `${text.slice(0, 57)}...`;
}

function kindOf(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'boolean') return 'bool';
  if (typeof value === 'number') return Number.isInteger(value) ? 'int' : 'float';
  if (typeof value === 'string') return 'str';
  if (Array.isArray(value)) return 'list';
  return typeof value === 'object' ? 'dict' : typeof value;
}

function typeMatches(ptype: string, value: unknown): boolean {
  switch (ptype) {
    // A safe integer: magnitude at most 2^53-1, the server's bound too.
    case 'int': return typeof value === 'number' && Number.isSafeInteger(value);
    case 'float': return typeof value === 'number';
    case 'bool': return typeof value === 'boolean';
    case 'str': return typeof value === 'string';
    case 'list': return Array.isArray(value);
    case 'dict': return typeof value === 'object' && value !== null && !Array.isArray(value);
    default: return true;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Whether two values are equal as JSON values (enum membership).
 *
 * Both must be the same JSON kind: a boolean never equals a number. Numbers
 * compare by value, strings exactly, `null` only `null`; arrays element by
 * element in order, objects by equal key sets and equal values per key (key
 * order ignored), with this same rule. The server's `_json_equal` is the
 * same rule.
 *
 * Args:
 *     a: One value.
 *     b: The other.
 *
 * Returns:
 *     `true` when the two are the same JSON value.
 */
function jsonEqual(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length
      && a.every((x, i) => jsonEqual(x, b[i]));
  }
  if (isPlainObject(a) || isPlainObject(b)) {
    if (!isPlainObject(a) || !isPlainObject(b)) return false;
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length
      && keys.every((k) => Object.prototype.hasOwnProperty.call(b, k) && jsonEqual(a[k], b[k]));
  }
  return a === b;
}

/**
 * Check one typed parameter value against its declared type and constraints.
 *
 * `any`, and a type outside the vocabulary, is unchecked; `int` takes a whole
 * number, `float` any number, neither a boolean; `null` is "no value",
 * accepted for an optional parameter and refused for a required one, as is
 * an empty string; `min` / `max` bound a number and `enum` lists the values
 * allowed.
 *
 * Args:
 *     ptype: The declared type (one of `PARAM_TYPES`).
 *     constraints: The declared `min`, `max` and `enum`, or none.
 *     value: The value as the document carries it.
 *     required: Whether the parameter is declared required.
 *
 * Returns:
 *     `null` when the value is accepted, else the reason it is refused.
 */
export function checkParamValue(
  ptype: string | undefined,
  constraints: ValueConstraints | undefined,
  value: unknown,
  required = false,
): string | null {
  if (value === null || value === undefined || value === '') {
    if (required) return `is required and cannot be empty (got ${shown(value ?? null)})`;
    if (value !== '') return null;
  }
  const t = ptype ?? 'any';
  if (!typeMatches(t, value)) return `expects ${t}, got ${shown(value)} (${kindOf(value)})`;
  const limits = constraints ?? {};
  if (limits.enum && limits.enum.length > 0 && !limits.enum.some((o) => jsonEqual(value, o))) {
    return `must be one of ${limits.enum.map(shown).join(', ')}, got ${shown(value)}`;
  }
  if (typeof value === 'number') {
    if (limits.min != null && value < limits.min) return `must be >= ${limits.min}, got ${shown(value)}`;
    if (limits.max != null && value > limits.max) return `must be <= ${limits.max}, got ${shown(value)}`;
  }
  return null;
}

/** The declared type an editor type checks against (`enum` checks membership only). */
const CHECKED_TYPE: Record<ParamEditorType, string> = {
  string: 'str', enum: 'any', int: 'int', float: 'float', bool: 'bool', list: 'list', dict: 'dict',
};

export function coerceParamValue(input: CoerceInput): CoerceResult {
  const coerced = coerceRaw(input);
  if (!coerced.ok) return coerced;
  const { paramType, required, enumOptions, min, max } = input;
  // Blank optional values coerce to "no value" ('' / null / an empty
  // container) before the rule sees them, and are kept as they are.
  if (!required && (String(input.raw).trim() === '') && paramType !== 'bool') return coerced;
  const reason = checkParamValue(
    CHECKED_TYPE[paramType], { min, max, enum: enumOptions }, coerced.value, required,
  );
  return reason === null ? coerced : { ok: false, error: `Value ${reason}.` };
}

function coerceRaw(input: CoerceInput): CoerceResult {
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
    // Optional numeric blank coerces to null.
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
      // `draftValue` is initialized from currentValue when the row
      // enters `editing` (see
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
            // Editing a bound row breaks the binding in one
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
        // flow (the unlock-to-edit click on a locked row).
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
        // Do NOT lock the user out. CHANGE_VALUE re-enters
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
