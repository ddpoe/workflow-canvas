/**
 * The editor's one param check, type by type
 * (canvas-ui case `catalog.machines-param-value-check`).
 *
 * `coerceParamValue` is the only param check in the system: a parameter
 * row commits through it and a pipeline variable is saved through it. It
 * decides what the *editor* accepts, not what a run accepts.
 *
 * Existing coverage this does NOT duplicate:
 *   - `paramEditor.test.ts` covers the row's state transitions
 *     (viewing → editing → committing → committed / invalid, binding) and
 *     exactly one coercion row: a blank commit on an optional numeric
 *     coercing to null. The type-by-type table is only here.
 *
 * Written as characterization: the verdicts and the messages are pinned
 * exactly.
 */
import { describe, it, expect } from 'vitest';
import { coerceParamValue } from '../paramEditor.machine';

type Row = {
  what: string;
  raw: unknown;
  paramType: string;
  required?: boolean;
  enumOptions?: string[];
  /** Expected coerced value when the check accepts. */
  value?: unknown;
  /** Expected message when the check refuses. */
  error?: string;
};

// One row per Given: each declared type, a required parameter with a blank
// value, and the int/float split on a trailing-garbage number.
const TABLE: Row[] = [
  // bool — a toggle hands over a real boolean; the string spellings are
  // what a loaded document or a pipeline variable can carry.
  { what: 'bool from a toggle', raw: true, paramType: 'bool', value: true },
  { what: 'bool from "false"', raw: 'false', paramType: 'bool', value: false },
  // bool has no required-or-blank branch, so a blank is a type failure.
  { what: 'blank bool', raw: '', paramType: 'bool', error: 'Expected bool; got "".' },

  // enum — only a declared member.
  { what: 'enum member', raw: 'b', paramType: 'enum', enumOptions: ['a', 'b'], value: 'b' },
  { what: 'enum non-member', raw: 'z', paramType: 'enum', enumOptions: ['a', 'b'], error: '"z" is not one of: a, b.' },

  // int / float — the split the case names: a float reads "5abc" as 5
  // where an int refuses it.
  { what: 'int', raw: '42', paramType: 'int', value: 42 },
  { what: 'int with trailing garbage', raw: '5abc', paramType: 'int', error: 'Expected int; got "5abc".' },
  { what: 'float with trailing garbage', raw: '5abc', paramType: 'float', value: 5 },
  { what: 'blank optional numeric', raw: '', paramType: 'float', required: false, value: null },
  { what: 'blank required numeric', raw: '', paramType: 'int', required: true, error: 'Value cannot be empty.' },

  // list / dict — JSON text, with the empty container for a blank optional.
  { what: 'list', raw: '[1, 2]', paramType: 'list', value: [1, 2] },
  { what: 'list given an object', raw: '{"a": 1}', paramType: 'list', error: 'Expected JSON array.' },
  { what: 'blank optional list', raw: '', paramType: 'list', required: false, value: [] },
  { what: 'dict', raw: '{"a": 1}', paramType: 'dict', value: { a: 1 } },
  { what: 'dict given an array', raw: '[1]', paramType: 'dict', error: 'Expected JSON object.' },

  // string — the fallthrough, and the required-blank refusal on it.
  { what: 'string', raw: 'hello', paramType: 'string', value: 'hello' },
  { what: 'blank required string', raw: '', paramType: 'string', required: true, error: 'Value cannot be empty.' },
];

describe('coerceParamValue — the editor\'s one param check', () => {
  it.each(TABLE)('$what', ({ raw, paramType, required, enumOptions, value, error }) => {
    const result = coerceParamValue({ raw, paramType, required, enumOptions });
    if (error !== undefined) {
      expect(result.ok).toBe(false);
      expect(result.ok === false && result.error).toBe(error);
    } else {
      expect(result.ok === true && result.value).toEqual(value);
    }
  });

  it('refuses a number outside a declared min or max, and accepts the bounds themselves', () => {
    const bounded = { paramType: 'int', min: 1, max: 10 };
    expect(coerceParamValue({ ...bounded, raw: '0' })).toEqual({ ok: false, error: 'Must be >= 1.' });
    expect(coerceParamValue({ ...bounded, raw: '11' })).toEqual({ ok: false, error: 'Must be <= 10.' });
    expect(coerceParamValue({ ...bounded, raw: '1' })).toEqual({ ok: true, value: 1 });
    expect(coerceParamValue({ ...bounded, raw: '10' })).toEqual({ ok: true, value: 10 });
  });
});
