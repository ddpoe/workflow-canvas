/**
 * Status vocabulary of the Runs Preview.
 *
 * The cache-status route returns one row per target with a snake_case status
 * and, for a row that has a source run, where each of that run's outputs can
 * be read from. This module joins those rows to the preview's projected rows
 * and turns them into the labels, tallies and actions the preview shows. It
 * decides nothing about caching itself: every status and every output
 * location comes from the route, which uses the engine's own hit rule.
 */
import type { components } from '../types/api.js';

export type CacheStatusResponse = components['schemas']['CacheStatusResponse'];
export type CacheStatusRow = components['schemas']['CacheStatusRowModel'];

/** A row's status as the route reports it. */
export type PreviewStatus = CacheStatusRow['status'];
/** Where one output of the source run can be read from. */
export type OutputLocation = components['schemas']['CacheStatusOutput']['location'];

/** One output line of an expanded row. */
export interface OutputLine {
  slot: string;
  location: OutputLocation;
}

/** The part of a preview row the route's answer fills in. */
export interface RowVerdict {
  status: PreviewStatus;
  reason: string;
  sourceRunId: number | null;
  sourceNid: string;
  outputs: OutputLine[];
}

/** Display label for each status. */
export const STATUS_LABEL: Record<PreviewStatus, string> = {
  cached_local: 'cached · local',
  cached_remote: 'cached · remote',
  outputs_missing: 'outputs missing',
  new_step_changed: 'new · this step changed',
  new_upstream_reruns: 'new · upstream re-runs',
  blocked: 'blocked',
};

/** The row action for each status, when no rename or collision applies. */
export const STATUS_ACTION: Record<PreviewStatus, string> = {
  cached_local: 'skip',
  cached_remote: 'skip · pulls',
  outputs_missing: 're-run',
  new_step_changed: 'will run',
  new_upstream_reruns: 'will run',
  blocked: "can't run",
};

/** The action for one output line, by where its bytes are. */
export const OUTPUT_ACTION: Record<OutputLocation, string> = {
  local: 'read here',
  remote: 'pull',
  missing: 'recompute',
};

/** True for the statuses that reuse a completed run (skip, maybe pull). */
export function isCached(status: PreviewStatus): boolean {
  return status === 'cached_local' || status === 'cached_remote';
}

/** True for the statuses that name a source run (linked to History). */
export function hasSourceRun(status: PreviewStatus): boolean {
  return isCached(status) || status === 'outputs_missing';
}

/** True for the statuses under which the step runs. */
export function willRun(status: PreviewStatus): boolean {
  return status === 'outputs_missing'
    || status === 'new_step_changed'
    || status === 'new_upstream_reruns';
}

/**
 * Give every projected row its verdict from the route's answer.
 *
 * A pipeline-level ``blocked_reason`` (the document was refused before any
 * target could be expanded) blocks every projected row with that one reason.
 * A projected row the route did not answer for is blocked too, saying so:
 * the preview never guesses a status the engine did not report.
 *
 * Args:
 *     keys: The projected rows' keys (``<node id>::<sample>::<variant>``).
 *     response: The route's answer.
 *
 * Returns:
 *     One verdict per key.
 */
export function verdictsFor(
  keys: string[],
  response: CacheStatusResponse,
): Record<string, RowVerdict> {
  const out: Record<string, RowVerdict> = {};
  if (response.blocked_reason) {
    for (const k of keys) out[k] = blockedVerdict(response.blocked_reason);
    return out;
  }
  const byKey: Record<string, CacheStatusRow> = {};
  for (const row of response.rows) byKey[row.key] = row;
  for (const k of keys) {
    const row = byKey[k];
    out[k] = row
      ? {
          status: row.status,
          reason: row.reason ?? '',
          sourceRunId: row.source_run_id ?? null,
          sourceNid: row.source_nid ?? '',
          outputs: (row.outputs ?? []).map(o => ({ slot: o.slot, location: o.location })),
        }
      : blockedVerdict('the engine reported no status for this row');
  }
  return out;
}

/** A blocked verdict carrying one reason. */
export function blockedVerdict(reason: string): RowVerdict {
  return { status: 'blocked', reason, sourceRunId: null, sourceNid: '', outputs: [] };
}

/**
 * One-line summary of where a row's outputs are, e.g. ``1 local · 1 remote``.
 *
 * Returns:
 *     The non-zero location counts in the order local, remote, missing, or
 *     an empty string when the row has no outputs.
 */
export function whereSummary(outputs: OutputLine[]): string {
  const n: Record<OutputLocation, number> = { local: 0, remote: 0, missing: 0 };
  for (const o of outputs) n[o.location]++;
  return (['local', 'remote', 'missing'] as OutputLocation[])
    .filter(loc => n[loc] > 0)
    .map(loc => `${n[loc]} ${loc}`)
    .join(' · ');
}

/** Per-status counts, with the two `new` reasons combined. */
export interface StatusTally {
  cachedLocal: number;
  cachedRemote: number;
  outputsMissing: number;
  new: number;
  blocked: number;
  loading: number;
}

/**
 * Count rows by status: the overview columns and the footer.
 *
 * Args:
 *     statuses: One entry per row; ``null`` for a row still waiting on the
 *         route.
 */
export function tallyStatuses(statuses: (PreviewStatus | null)[]): StatusTally {
  const t: StatusTally = {
    cachedLocal: 0, cachedRemote: 0, outputsMissing: 0, new: 0, blocked: 0, loading: 0,
  };
  for (const s of statuses) {
    if (s === null) t.loading++;
    else if (s === 'cached_local') t.cachedLocal++;
    else if (s === 'cached_remote') t.cachedRemote++;
    else if (s === 'outputs_missing') t.outputsMissing++;
    else if (s === 'blocked') t.blocked++;
    else t.new++;
  }
  return t;
}
