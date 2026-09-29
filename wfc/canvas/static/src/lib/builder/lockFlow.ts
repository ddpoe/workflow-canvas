/**
 * The lock flow: when the Runs Preview shows statuses, and what Lock All
 * and Run do first.
 *
 * Statuses belong to one locked state of the pipeline. Lock All commits
 * every open parameter editor, posts the pipeline to the cache-status route
 * once, and keeps the answer together with a fingerprint of the document it
 * describes. Any later edit (a document change, or a parameter editor
 * opening) clears the statuses until the next lock, so nothing recomputes
 * per keystroke and no status ever describes a pipeline the user has since
 * changed.
 *
 * Lock All, and Run while any row is still unlocked, first open one
 * confirmation view (the lock summary). It lists the rows still unlocked and
 * every parameter that runs the method's declared default, by node and
 * parameter, with its value. Confirming commits the rows; a row that cannot
 * be committed is named in the summary and nothing proceeds, and the wait
 * never hangs. Run is disabled while the current lock has blocked rows.
 */
import { writable, derived, get } from 'svelte/store';
import { nodes, pipelineName, pipelineVariables, edges, setPipelineError } from './stores.js';
import { exportPipeline } from './pipeline.js';
import { fetchCacheStatus } from './api.js';
import { projectRuns } from '../graph/projection.js';
import { blockedVerdict, verdictsFor, type RowVerdict } from './runsPreviewStatus.js';
import {
  awaitAllCommitted, dirtyEditorIds, dispatchRun, editorsDirty, hasDirtyEditors,
} from '../machines/root.js';
import type { CanvasNodeData, PipelineJSON } from '../shared/types.js';

// ---------- The lock ----------

/** Where the statuses stand. */
export type LockState =
  | { status: 'unlocked' }
  | { status: 'locking'; fingerprint: string }
  | { status: 'locked'; fingerprint: string; verdicts: Record<string, RowVerdict> };

export const lockState = writable<LockState>({ status: 'unlocked' });

/**
 * The document as the engine reads it, without node positions: moving a
 * node on the canvas changes no run and so does not clear the statuses.
 */
export function docFingerprint(doc: PipelineJSON): string {
  return JSON.stringify({
    ...doc,
    nodes: doc.nodes.map(n => {
      const { position: _position, ...rest } = n;
      return rest;
    }),
  });
}

/** Clear the statuses; the preview shows run counts only until the next lock. */
export function clearLock(): void {
  stopWatch();
  if (get(lockState).status !== 'unlocked') lockState.set({ status: 'unlocked' });
}

let unwatch: (() => void) | null = null;

function stopWatch(): void {
  unwatch?.();
  unwatch = null;
}

/** Clear the lock on the first edit after it: a document change or an open editor. */
function watchForEdits(fingerprint: string): void {
  stopWatch();
  const check = () => {
    if (!unwatch) return;
    if (get(editorsDirty) || docFingerprint(exportPipeline()) !== fingerprint) clearLock();
  };
  const subs = [nodes, edges, pipelineName, pipelineVariables, editorsDirty]
    .map(s => (s as { subscribe: (fn: () => void) => () => void }).subscribe(check));
  unwatch = () => { for (const u of subs) u(); };
}

/**
 * Post the current pipeline to the cache-status route once and show its
 * answer. Every projected row gets a verdict (a row the route did not
 * answer, or a failed request, is blocked saying so). An edit made while
 * the request is in flight discards the answer.
 */
export async function lockNow(): Promise<void> {
  const doc = exportPipeline();
  const fingerprint = docFingerprint(doc);
  stopWatch();
  lockState.set({ status: 'locking', fingerprint });
  const keys = projectRuns(doc).map(p => p.key);
  let verdicts: Record<string, RowVerdict> = {};
  if (keys.length > 0) {
    try {
      verdicts = verdictsFor(keys, await fetchCacheStatus(doc));
    } catch (err) {
      const why = err instanceof Error ? err.message : String(err);
      const verdict = blockedVerdict(`cache status is unavailable (${why})`);
      for (const k of keys) verdicts[k] = verdict;
    }
  }
  const now = get(lockState);
  if (now.status !== 'locking' || now.fingerprint !== fingerprint) return;
  if (get(editorsDirty) || docFingerprint(exportPipeline()) !== fingerprint) {
    lockState.set({ status: 'unlocked' });
    return;
  }
  lockState.set({ status: 'locked', fingerprint, verdicts });
  watchForEdits(fingerprint);
}

/** The distinct blocked reasons of the current lock; empty when unlocked. */
export const blockedReasons = derived(lockState, $s => {
  if ($s.status !== 'locked') return [] as string[];
  const out = new Set<string>();
  for (const v of Object.values($s.verdicts)) {
    if (v.status === 'blocked') out.add(v.reason || 'blocked');
  }
  return Array.from(out);
});

// ---------- The lock summary ----------

/** One parameter that runs its method's declared default. */
export interface DefaultLine {
  node: string;
  param: string;
  value: string;
}

export interface LockSummary {
  /** What confirming leads to. */
  purpose: 'lock' | 'run';
  /** Rows still unlocked, as ``node · parameter``. */
  unlocked: string[];
  defaults: DefaultLine[];
  /** Rows the last confirm could not commit; empty until then. */
  failed: string[];
  busy: boolean;
}

export const lockSummary = writable<LockSummary | null>(null);

function labelsByNode(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const n of get(nodes)) out[n.id] = (n.data as CanvasNodeData)?.label || n.id;
  return out;
}

/**
 * Turn aggregator child ids (``nodeId::paramName[::suffix]``) into a
 * de-duplicated ``node · parameter`` list.
 */
export function describeEditorRows(ids: Iterable<string>): string[] {
  const labels = labelsByNode();
  const out: string[] = [];
  const seen = new Set<string>();
  for (const id of ids) {
    const [nodeId = '?', paramName = '?'] = id.split('::');
    const k = `${nodeId}::${paramName}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(`${labels[nodeId] ?? nodeId} · ${paramName}`);
  }
  return out;
}

function fmtDefault(v: unknown): string {
  if (v === undefined) return 'no declared default';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

/**
 * Every declared parameter the compiled document leaves out on every row
 * of its node, so the method runs its declared default: a value box never
 * touched (or committed blank, which the compiler omits the same way).
 *
 * Args:
 *     doc: The compiled pipeline document.
 *     canvasNodes: The canvas nodes, for each method's declared parameters.
 */
export function defaultsInUse(
  doc: PipelineJSON,
  canvasNodes: { id: string; data: CanvasNodeData }[],
): DefaultLine[] {
  const out: DefaultLine[] = [];
  for (const cn of canvasNodes) {
    const d = cn.data;
    if (!d || (d.nodeType && d.nodeType !== 'method')) continue;
    const node = doc.nodes.find(n => n.id === cn.id);
    if (!node) continue;
    const rows = [node.params ?? {}, ...Object.values(doc.param_sets?.[cn.id] ?? {})];
    for (const p of d.params ?? []) {
      if (rows.some(r => p.name in r)) continue;
      out.push({ node: d.label || cn.id, param: p.name, value: fmtDefault(p.default) });
    }
  }
  return out;
}

/** Open the lock summary for Lock All (``lock``) or for Run (``run``). */
export function openLockSummary(purpose: 'lock' | 'run'): void {
  lockSummary.set({
    purpose,
    unlocked: describeEditorRows(dirtyEditorIds()),
    defaults: defaultsInUse(exportPipeline(), get(nodes)),
    failed: [],
    busy: false,
  });
}

export function cancelLockSummary(): void {
  lockSummary.set(null);
}

/**
 * Commit every unlocked row, then lock (Lock All) or run (Run).
 *
 * A row still dirty after the commit pulse could not be committed: it is
 * named in the summary, which stays open, and nothing proceeds. The commit
 * pulse settles on every row's commit attempt whatever its outcome, so this
 * never waits forever.
 */
export async function confirmLockSummary(): Promise<void> {
  const s = get(lockSummary);
  if (!s || s.busy) return;
  lockSummary.set({ ...s, busy: true, failed: [] });
  await awaitAllCommitted();
  if (hasDirtyEditors()) {
    lockSummary.set({
      ...s,
      busy: false,
      unlocked: describeEditorRows(dirtyEditorIds()),
      failed: describeEditorRows(dirtyEditorIds()),
    });
    return;
  }
  lockSummary.set(null);
  if (s.purpose === 'lock') await lockNow();
  else startRun();
}

// ---------- Run ----------

/** Required parameters with no value, no default and no variant. */
export function missingRequired(): string[] {
  const errors: string[] = [];
  for (const n of get(nodes)) {
    const d = n.data as CanvasNodeData;
    if (!d?.params || d.nodeType !== 'method') continue;
    for (const p of d.params) {
      if (!p.required) continue;
      const base = d.paramValues?.[p.name];
      const hasBase = base !== undefined && base !== null && base !== '';
      const hasDefault = p.default !== undefined && p.default !== null && p.default !== '';
      const hasVariant = Object.keys(d.variants?.[p.name] ?? {}).length > 0;
      if (!hasBase && !hasDefault && !hasVariant) errors.push(`${d.label || n.id}.${p.name} is required`);
    }
  }
  return errors;
}

/** Submit the pipeline. The statuses described the state before this run, so they clear. */
export function startRun(): void {
  if (get(blockedReasons).length > 0) return;
  const required = missingRequired();
  if (required.length > 0) {
    setPipelineError({
      kind: 'not_found',
      message: 'Cannot run — required params missing:\n' + required.map(r => `  • ${r}`).join('\n'),
      hint: 'Fill in the listed params (or set a default) before running.',
    });
    return;
  }
  setPipelineError(null);
  const doc = exportPipeline();
  clearLock();
  dispatchRun(doc);
}

/**
 * The Run button: with any row unlocked, open the lock summary (the run
 * starts only when the user confirms); otherwise run. Does nothing while
 * the current lock has blocked rows (the button is disabled then).
 */
export function requestRun(): void {
  if (get(blockedReasons).length > 0) return;
  if (hasDirtyEditors()) openLockSummary('run');
  else startRun();
}
