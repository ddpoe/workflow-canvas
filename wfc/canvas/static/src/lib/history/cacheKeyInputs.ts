/**
 * The structure of the run detail's "Cache-key inputs" card.
 *
 * The card shows what went into a run's cache key (method and version with
 * the code hash, env, params, input slots), then what the run recorded that
 * is not in the key (commit, wfc version, pipeline document). This module
 * builds every row, every hash tag (its kind, label, short and full value,
 * and the one line on what it is computed from), the params table's rows
 * with their declared types and the slots table's rows; the card component
 * only renders them, so all of it is testable without mounting the panel.
 */
import type { WfcRun } from './historyApi.js';
import type { PARAM_TYPES } from '../shared/types.js';

/** A declared param type: one of the contract vocabulary's names. */
export type ParamType = (typeof PARAM_TYPES)[number];

/** What a value the run did not record reads as. */
export const NOT_RECORDED = 'not recorded';

/** The one line a run keyed under an earlier rule shows. */
export const EARLIER_RULE_LINE = 'This key was computed under an earlier rule.';

/** Characters kept of a hash or fingerprint in its short form. */
const SHORT_HASH = 8;

/** Characters kept of a commit SHA in its short form (git's own default). */
const SHORT_SHA = 7;

/** A param value whose one-line form is longer than this is shown truncated. */
const LONG_VALUE_CHARS = 60;

/** An object or array param with more entries than this is shown truncated. */
const LONG_VALUE_ENTRIES = 3;

/** Lines of a long value its hover previews. */
const PREVIEW_LINES = 8;

/** Which hash a tag shows; stable across runs, for test ids and colours. */
export type HashKind =
  | 'code' | 'env' | 'params' | 'inputs' | 'run-key' | 'content' | 'commit' | 'pipeline';

/** Each hash kind's label, and the hover line on what it is computed from. */
const HASH_KINDS: Record<HashKind, { label: string; meaning: string }> = {
  code: {
    label: 'code',
    meaning: "Code hash: the method's code files. Changes when the code changes.",
  },
  env: {
    label: 'env',
    meaning: 'Env hash: the contents of the env the method runs in. Changes when the env changes.',
  },
  params: {
    label: 'params',
    meaning: 'Params hash: all parameter names and values, keys sorted. Two runs with the same params show the same hash.',
  },
  inputs: {
    label: 'inputs',
    meaning: "Inputs hash: all input slots together, each slot's name plus its hash. Rewiring a value into another slot changes it.",
  },
  'run-key': {
    label: 'run key',
    meaning: 'Run key: the cache key of the upstream run that filled this slot.',
  },
  content: {
    label: 'content',
    meaning: "Content hash: the sample's content when this run read it.",
  },
  commit: {
    label: 'commit',
    meaning: 'Commit: the git commit this run ran at. Recorded only; not in the cache key.',
  },
  pipeline: {
    label: 'pipeline',
    meaning: 'Pipeline hash: the pipeline document this run came from. Recorded only; not in the cache key.',
  },
};

/** One labelled hash: what it is, its short form and the value behind it. */
export interface HashTag {
  kind: HashKind;
  /** The label naming the hash (`code`, `run key`, ...). */
  label: string;
  /** The short hash, or "not recorded". */
  short: string;
  /** The full hash, which the tag's copy icon copies, or null when the run has none. */
  full: string | null;
  /** One plain line on what the hash is computed from: the tag's hover. */
  meaning: string;
}

/** One row of the params table. */
export interface ParamRow {
  name: string;
  value: unknown;
  /** The value on one line, as JSON (a string keeps its quotes). */
  display: string;
  /** The type the run's method version declares, or null when undeclared. */
  type: ParamType | null;
  /** Whether the value is shown truncated, with its size and a ▸ to open it. */
  long: boolean;
  /** The value's size ("2.4 KB"), for a long value only. */
  size: string | null;
  /** The pretty-printed value, for a long value only. */
  pretty: string | null;
  /** Its first lines, ending in "…" when there are more: a long value's hover. */
  preview: string | null;
}

/** One row of the slots table: an upstream run's output, or a sample read. */
export type SlotRow =
  | { kind: 'parent'; slot: string; runId: string; sourceSlot: string | null; tag: HashTag }
  | { kind: 'sample'; slot: string; sample: string; tag: HashTag };

/** The card's content. */
export interface CacheKeyCard {
  /** The earlier-rule line, or null for a run keyed under the current rule. */
  earlierRule: string | null;
  method: {
    /** The chip text, `<module>.<method>`. */
    name: string;
    module: string;
    method: string;
    /** `v<label>`, or null when the run records no version. */
    version: string | null;
    /** The version's content identity, for the Registry link. */
    versionIdentity: string | null;
    tag: HashTag;
  };
  env: { name: string | null; tag: HashTag };
  params: { label: string; rows: ParamRow[]; tag: HashTag };
  slots: { label: string; rows: SlotRow[]; tag: HashTag };
  recorded: {
    commit: HashTag;
    wfcVersion: string;
    pipeline: { tag: HashTag; pipelineId: string | null };
  };
}

/** The run fields the card reads. */
export type KeyInputsRun = Pick<
  WfcRun,
  | 'module'
  | 'method'
  | 'version'
  | 'versionIdentity'
  | 'pipelineId'
  | 'inputs'
  | 'paramTypes'
  | 'paramsHash'
  | 'inputsHash'
  | 'parents'
  | 'sampleInputs'
  | 'codeFingerprint'
  | 'envName'
  | 'envFingerprint'
  | 'gitCommit'
  | 'wfcVersion'
  | 'pipelineDocHash'
  | 'keyedUnderEarlierRule'
>;

function tag(kind: HashKind, value: string | null | undefined): HashTag {
  return {
    kind,
    label: HASH_KINDS[kind].label,
    short: value ? value.slice(0, kind === 'commit' ? SHORT_SHA : SHORT_HASH) : NOT_RECORDED,
    full: value || null,
    meaning: HASH_KINDS[kind].meaning,
  };
}

function plural(n: number, noun: string): string {
  return n === 0 ? 'none' : `${n} ${noun}${n === 1 ? '' : 's'}`;
}

/** A byte count as "312 B" or "2.4 KB". */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Whether a value is shown truncated behind a ▸ that opens it: its one-line
 * form (`display`) is longer than 60 characters, or it is an object or array
 * with more than three entries. The Registry's contract tables and its
 * confirm card use the same rule.
 */
export function isLongValue(value: unknown, display: string): boolean {
  const entries = value !== null && typeof value === 'object'
    ? Object.keys(value as object).length
    : 0;
  return display.length > LONG_VALUE_CHARS || entries > LONG_VALUE_ENTRIES;
}

function paramRow(name: string, value: unknown, type: ParamType | null): ParamRow {
  const display = value === undefined ? '∅' : JSON.stringify(value);
  const long = isLongValue(value, display);
  const pretty = long ? JSON.stringify(value, null, 2) : null;
  const lines = pretty ? pretty.split('\n') : [];
  return {
    name,
    value,
    display,
    type,
    long,
    size: long ? formatSize(new TextEncoder().encode(display).length) : null,
    pretty,
    preview: pretty
      ? lines.slice(0, PREVIEW_LINES).join('\n') + (lines.length > PREVIEW_LINES ? '\n…' : '')
      : null,
  };
}

/**
 * Build the content of a run's "Cache-key inputs" card.
 *
 * *In the cache key*: the method (`<module>.<method>`, the version label and
 * the code hash), the env (name and env hash), the params (a table of name,
 * value and declared type, with the params hash) and the input slots (one
 * row per parent, with its run, output slot and run key, and one per sample
 * read, with its sample and content hash, under the inputs hash).
 * *Recorded, not in the key*: the commit (a tag of the short SHA), the wfc
 * version and the pipeline document's hash. Every tag keeps its full value
 * for its copy icon. Any
 * value the run did not record reads "not recorded". A run keyed under an
 * earlier rule gets the one earlier-rule line; no rule number appears.
 *
 * A param's type is the one its method version's contract declares
 * (`paramTypes`), never read off the value; an undeclared param has none.
 *
 * Args:
 *     run: The run, as the History payload sends it.
 *
 * Returns:
 *     The card's rows, tags and tables.
 */
export function cacheKeyInputs(run: KeyInputsRun): CacheKeyCard {
  const types = run.paramTypes ?? {};
  const params = Object.entries(run.inputs ?? {}).map(
    ([name, value]) => paramRow(name, value, types[name] ?? null),
  );

  const slots: SlotRow[] = [
    ...(run.parents ?? []).map(p => ({
      kind: 'parent' as const,
      slot: p.slot,
      runId: p.sourceRunId,
      sourceSlot: p.sourceSlot,
      tag: tag('run-key', p.cacheKey),
    })),
    ...(run.sampleInputs ?? []).map(s => ({
      kind: 'sample' as const,
      slot: s.slot,
      sample: s.sample,
      tag: tag('content', s.contentHash),
    })),
  ];

  return {
    earlierRule: run.keyedUnderEarlierRule ? EARLIER_RULE_LINE : null,
    method: {
      name: `${run.module}.${run.method}`,
      module: run.module,
      method: run.method,
      version: run.version != null ? `v${run.version}` : null,
      versionIdentity: run.versionIdentity ?? null,
      tag: tag('code', run.codeFingerprint),
    },
    env: { name: run.envName ?? null, tag: tag('env', run.envFingerprint) },
    params: { label: plural(params.length, 'param'), rows: params, tag: tag('params', run.paramsHash) },
    slots: { label: plural(slots.length, 'input slot'), rows: slots, tag: tag('inputs', run.inputsHash) },
    recorded: {
      commit: tag('commit', run.gitCommit),
      wfcVersion: run.wfcVersion ?? NOT_RECORDED,
      pipeline: { tag: tag('pipeline', run.pipelineDocHash), pipelineId: run.pipelineId ?? null },
    },
  };
}
