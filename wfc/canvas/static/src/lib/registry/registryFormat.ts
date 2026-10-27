/**
 * How the Registry shows a method's contract and a registration's changes.
 *
 * Pure functions over the declarations the detail route and a registration
 * plan return: values in Python form (`None`, `True`, `'str'`), the type
 * colour class a value takes, the declared fields a table has no column for
 * (its More cell), and the one-line counts the confirm card's rows read.
 * The components render what these return and decide nothing themselves.
 */

import { isLongValue } from '../history/cacheKeyInputs.js';

/** One declaration of a slot or a param, as `method.yaml` declares it. */
export type Declaration = Record<string, unknown>;

/** The fields each contract table has a column for; the rest go to More. */
export const TABLE_COLUMNS = {
  input: ['type', 'required', 'multiple'],
  output: ['type', 'required'],
  param: ['type', 'default', 'required'],
} as const;

/** What a table row of a contract reads from one declaration. */
export type TableKind = keyof typeof TABLE_COLUMNS;

/** A slot declaration written as a bare type (`data: csv`) is `{type}`. */
export function asDeclaration(spec: unknown): Declaration {
  if (spec === null || spec === undefined) return {};
  if (typeof spec === 'string') return { type: spec };
  return typeof spec === 'object' ? (spec as Declaration) : {};
}

/** A string as Python writes it: single-quoted, with `\` and `'` escaped. */
function pyString(s: string): string {
  return `'${s.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

/**
 * A value in Python form: `None`, `True`/`False`, `'text'`, `30`, `[1, 'a']`,
 * `{'k': 1}`. `undefined` (a field not declared) is `None`.
 */
export function pyValue(value: unknown): string {
  if (value === null || value === undefined) return 'None';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return pyString(value);
  if (Array.isArray(value)) return `[${value.map(pyValue).join(', ')}]`;
  if (typeof value === 'object') {
    const parts = Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${pyString(k)}: ${pyValue(v)}`);
    return `{${parts.join(', ')}}`;
  }
  return String(value);
}

/** The declared param types, each with its Cache-key card colour class. */
const PARAM_TYPE_CLASSES = new Set(['str', 'int', 'float', 'bool', 'list', 'dict', 'any']);

/** The colour class of a param's declared type: `t-str`, …, or `t-none`. */
export function typeClass(ptype: unknown): string {
  return typeof ptype === 'string' && PARAM_TYPE_CLASSES.has(ptype) ? `t-${ptype}` : 't-none';
}

/** The colour class of a value by what it is: a string, a number, a boolean, … */
export function valueClass(value: unknown): string {
  if (value === null || value === undefined) return 't-none';
  if (typeof value === 'boolean') return 't-bool';
  if (typeof value === 'number') return Number.isInteger(value) ? 't-int' : 't-float';
  if (typeof value === 'string') return 't-str';
  if (Array.isArray(value)) return 't-list';
  return 't-dict';
}

/** A value shown in a cell: its Python form, its colour, and whether it is long. */
export type CellValue = { text: string; cls: string; long: boolean; pretty: string | null };

/** A declared value as a table cell shows it (the long-value rule is the Cache-key card's). */
export function cellValue(value: unknown): CellValue {
  const text = pyValue(value);
  const long = isLongValue(value, text);
  return { text, cls: valueClass(value), long, pretty: long ? JSON.stringify(value, null, 2) : null };
}

/**
 * The declared fields a table has no column for, by name: what its More cell
 * lists. The description is shown on the row's own line, never in More.
 */
export function moreKeys(decl: Declaration, kind: TableKind): string[] {
  const shown = new Set<string>([...TABLE_COLUMNS[kind], 'description']);
  return Object.keys(decl).filter(k => !shown.has(k)).sort();
}

/** The More fields of a declaration as JSON, or `null` when it has none. */
export function moreJson(decl: Declaration | null | undefined, kind: TableKind): string | null {
  if (!decl) return null;
  const keys = moreKeys(decl, kind);
  if (keys.length === 0) return null;
  return JSON.stringify(Object.fromEntries(keys.map(k => [k, decl[k]])), null, 2);
}

/**
 * One field an open contract row lists: its name, its value on one line
 * (`text`), the whole value its box opens to (`full`: a mapping or a list
 * pretty-printed as JSON, prose as written), its colour class, and whether
 * it is prose (the description) rather than a value.
 */
export type DetailField = { key: string; text: string; full: string; cls: string; prose: boolean };

/**
 * What an open contract row lists: its `description` first (or "No
 * description in method.yaml"), then every other field the entry declares
 * except what its row shows (its type, a param's default), in declaration
 * order. Each value is in Python form.
 */
export function detailFields(decl: Declaration, kind: TableKind): DetailField[] {
  const described = typeof decl.description === 'string' && decl.description.trim() !== '';
  const out: DetailField[] = [{
    key: 'description', text: descriptionOf(decl), full: descriptionOf(decl),
    cls: described ? 'prose' : 'none', prose: true,
  }];
  for (const [key, value] of Object.entries(decl)) {
    if (key === 'type' || key === 'description') continue;
    if (key === 'default' && kind === 'param') continue;
    const cell = cellValue(value);
    const full = value !== null && typeof value === 'object' ? JSON.stringify(value, null, 2) : cell.text;
    out.push({ key, text: cell.text, full, cls: cell.cls, prose: false });
  }
  return out;
}

/** A declaration's description, or the line that says it has none. */
export function descriptionOf(decl: Declaration): string {
  const d = decl.description;
  return typeof d === 'string' && d.trim() ? d : 'No description in method.yaml';
}

/** One entry of a registration plan's slots or params. */
export type PlanEntry = {
  name: string;
  kind?: 'input' | 'output';
  change: 'added' | 'removed' | 'changed';
  before: Declaration | null;
  after: Declaration | null;
};

/** "1 changed · 1 added · 1 removed": the counts of a part's entries, in that order. */
export function changeCounts(entries: Array<{ change: string }>): string {
  const n = (c: string) => entries.filter(e => e.change === c).length;
  return (['changed', 'added', 'removed'] as const)
    .filter(c => n(c) > 0)
    .map(c => `${n(c)} ${c}`)
    .join(' · ');
}

/** The code part's counts: files changed, added and removed. */
export function codeCounts(code: { changed: string[]; added: string[]; removed: string[] }): string {
  return changeCounts([
    ...code.changed.map(() => ({ change: 'changed' })),
    ...code.added.map(() => ({ change: 'added' })),
    ...code.removed.map(() => ({ change: 'removed' })),
  ]);
}

/** A new method's slots: "1 in · 2 out". */
export function slotCounts(slots: Array<{ kind?: string }>): string {
  const ins = slots.filter(s => s.kind === 'input').length;
  return `${ins} in · ${slots.length - ins} out`;
}

/** A new method's params: "1 param", "3 params". */
export function paramCount(n: number): string {
  return `${n} param${n === 1 ? '' : 's'}`;
}

/** Whether one column's value differs between two declarations. */
export function cellChanged(before: Declaration | null, after: Declaration | null,
                            field: string): boolean {
  if (!before || !after) return false;
  return JSON.stringify(before[field]) !== JSON.stringify(after[field]);
}

/** One line of a Before | After JSON box, marked when the other side lacks it. */
export type JsonLine = { text: string; marked: boolean };

/**
 * The More fields of a changed entry as two JSON boxes, each line marked
 * when the other box does not hold it; a side with no More fields reads
 * "not declared".
 */
export function moreDiff(before: Declaration | null, after: Declaration | null,
                         kind: TableKind): { before: JsonLine[] | null; after: JsonLine[] | null } {
  const b = moreJson(before, kind);
  const a = moreJson(after, kind);
  const bl = b ? b.split('\n') : [];
  const al = a ? a.split('\n') : [];
  return {
    before: b ? bl.map(text => ({ text, marked: !al.includes(text) })) : null,
    after: a ? al.map(text => ({ text, marked: !bl.includes(text) })) : null,
  };
}

/** The More keys of either side of a changed entry, for its cell. */
export function moreKeysEither(before: Declaration | null, after: Declaration | null,
                               kind: TableKind): string[] {
  const keys = new Set([...(before ? moreKeys(before, kind) : []),
                        ...(after ? moreKeys(after, kind) : [])]);
  return [...keys].sort();
}
