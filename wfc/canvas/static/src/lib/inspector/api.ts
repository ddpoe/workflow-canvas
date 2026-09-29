/**
 * Network calls for the Inspector.
 *
 * The column lookup behind a `column_of_input` param, the completed-runs list
 * the run-reference panel picks from, and the output tab's historical-log
 * stream. The Inspector panels call each of these; a failed lookup answers
 * `null` rather than throwing.
 *
 * The registered-samples list is deliberately absent: the `samples` store in
 * `builder/stores.ts` is its one owner, and it fetches through
 * `builder/api.ts`.
 */
import type { CompletedRun } from '../shared/types.js';

/**
 * Column options for a `column_of_input` param, as
 * `/api/contracts/.../output_columns` returns them.
 */
export type ColumnOptionsResp = {
  strict: string[];
  from_params: string[];
  patterns: string[];
  all: string[];
};

/**
 * Look up the output columns of an upstream run reference.
 *
 * The endpoint requires `method_full` in the path, and at this point the
 * method name behind a run reference is not known here — the backend resolves
 * it by reading the run's stored method when `run_id` is given against the
 * special sentinel `__run_reference__`. If no such convention exists the
 * endpoint answers non-2xx and this returns null, which the caller renders as
 * "no options".
 *
 * Args:
 *     sourceSlot: The upstream edge's source handle.
 *     runId: The referenced run.
 *
 * Returns:
 *     The column options, or ``null`` on a non-2xx response or a wire failure.
 */
export function fetchRunReferenceOutputColumns(
  sourceSlot: string,
  runId: string
): Promise<ColumnOptionsResp | null> {
  const url =
    `/api/contracts/__run_reference__/output_columns` +
    `?slot=${encodeURIComponent(sourceSlot)}&run_id=${encodeURIComponent(runId)}`;
  return fetchOutputColumns(url);
}

/**
 * Look up the output columns an upstream method declares for one slot.
 *
 * Args:
 *     methodFull: The upstream's `module.method`.
 *     sourceSlot: The upstream edge's source handle.
 *     params: The upstream's current param values, which can change the
 *         declared columns.
 *
 * Returns:
 *     The column options, or ``null`` on a non-2xx response or a wire failure.
 */
export function fetchMethodOutputColumns(
  methodFull: string,
  sourceSlot: string,
  params: Record<string, unknown>
): Promise<ColumnOptionsResp | null> {
  const paramsJson = encodeURIComponent(JSON.stringify(params));
  const url =
    `/api/contracts/${encodeURIComponent(methodFull)}/output_columns` +
    `?slot=${encodeURIComponent(sourceSlot)}&params=${paramsJson}`;
  return fetchOutputColumns(url);
}

/** Shared tail of the two column lookups: a failed lookup is `null`, never a throw. */
async function fetchOutputColumns(url: string): Promise<ColumnOptionsResp | null> {
  try {
    const resp = await fetch(url);
    if (!resp.ok) return null;
    return (await resp.json()) as ColumnOptionsResp;
  } catch {
    return null;
  }
}

/**
 * Fetch the completed runs the run-reference panel lists.
 *
 * Returns:
 *     The run list, or ``null`` on a non-2xx response.
 */
export async function fetchCompletedRuns(): Promise<CompletedRun[] | null> {
  const resp = await fetch('/api/wfc/completed-runs');
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Open the historical-log stream for a finished run.
 *
 * The output tab's post-run fallback replays a persisted log: the handler
 * emits the historical lines then a terminal frame and closes. The caller
 * attaches its own `onmessage` / `onerror` and owns closing the stream.
 *
 * Args:
 *     runId: The run whose persisted log to replay.
 *
 * Returns:
 *     The open EventSource.
 */
export function openNodeHistoricalLogStream(runId: string): EventSource {
  return new EventSource(
    `/api/wfc/run/${encodeURIComponent(runId)}/stream-logs?full=1`
  );
}
