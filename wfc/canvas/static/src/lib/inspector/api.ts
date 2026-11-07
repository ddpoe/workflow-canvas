/**
 * Network calls for the Inspector.
 *
 * The options lookup behind an `options_from` param, the completed-runs list
 * the run-reference panel picks from, and the output tab's historical-log
 * stream. The Inspector panels call each of these; a failed lookup answers
 * `null` rather than throwing.
 *
 * The registered-samples list is deliberately absent: the `samples` store in
 * `builder/stores.ts` is its one owner, and it fetches through
 * `builder/api.ts`.
 */
import type { CompletedRun } from '../shared/types.js';
import type { components } from '../types/api.js';
import type { OutputOptions } from './options.js';

/**
 * What the options lookup asks of one upstream output slot: the slot plus
 * the upstream's pin and param sets, or a Run Reference's run id.
 */
export type OutputOptionsRequest = components['schemas']['OutputOptionsRequest'];

/** The path segment a Run Reference's lookup names; the run names its own method. */
export const RUN_REFERENCE_METHOD = '__run_reference__';

/**
 * Ask the server what one upstream output slot offers.
 *
 * Args:
 *     methodFull: The upstream's `module.method`, or `RUN_REFERENCE_METHOD`
 *         for a request that names a run.
 *     request: The slot and either the version and param sets or the run id.
 *
 * Returns:
 *     The slot's options, or ``null`` on a non-2xx response or a wire
 *     failure; never throws.
 */
export async function fetchOutputOptions(
  methodFull: string,
  request: OutputOptionsRequest,
): Promise<OutputOptions | null> {
  try {
    const resp = await fetch(
      `/api/contracts/${encodeURIComponent(methodFull)}/output_options`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      },
    );
    if (!resp.ok) return null;
    return (await resp.json()) as OutputOptions;
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
