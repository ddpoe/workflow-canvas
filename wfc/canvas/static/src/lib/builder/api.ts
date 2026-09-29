/**
 * Network calls for the Builder area.
 *
 * Every Builder request lives here: the demo pipeline and the dev-mode probe
 * `App.svelte` makes at boot, the module registry the sidebar reads, the runs
 * preview's cache-status POST, the registered-samples list behind the
 * `samples` store, and the archive-status pair. The dev *toolbar*'s routes are
 * deliberately separate — see `devApi.ts` — because they are outside the
 * generated contract and served only in development.
 *
 * Each function owns its URL, method, body and response check. Callers keep
 * their own state writes and their own try/catch, so each caller decides how
 * a failure degrades.
 */
import type { ArchiveStatus } from './archiveStatus.js';
import type { PipelineJSON, SampleInfo } from '../shared/types.js';
import type { CacheStatusResponse } from './runsPreviewStatus.js';

/**
 * Fetch the scaffolded demo pipeline document.
 *
 * Returns:
 *     The parsed document, or ``null`` when no demo is scaffolded (non-2xx),
 *     which leaves the canvas boot inert.
 */
export async function fetchDemoPipelineDocument(): Promise<unknown | null> {
  const resp = await fetch('/api/pipelines/demo');
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Probe whether the server is running in development mode.
 *
 * Returns:
 *     The parsed status payload, or ``null`` on a non-2xx response. Rejects
 *     on a wire failure; the caller swallows it.
 */
export function fetchDevStatus(): Promise<{ dev?: boolean } | null> {
  return fetch('/api/dev/status').then(r => (r.ok ? r.json() : null));
}

/**
 * Fetch the registered module/method registry.
 *
 * Returns:
 *     The raw ``{module_name: {description, methods: {...}}}`` payload, or
 *     ``null`` on a non-2xx response. The sidebar owns the transform into
 *     ``ModuleDef[]``.
 */
export async function fetchModuleRegistry(): Promise<Record<string, unknown> | null> {
  const resp = await fetch('/api/modules');
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Ask the backend what the pipeline would do if it ran now.
 *
 * Posts the pipeline document exactly as the run route receives it; the
 * route prepares and loads it the way a run does and answers one row per
 * engine target. Edges, slots, modules and bundles are never sent
 * separately: the document carries them.
 *
 * Args:
 *     pipeline: The exported canvas pipeline document.
 *
 * Returns:
 *     Every target's row, or ``blocked_reason`` when the document is refused.
 *
 * Raises:
 *     Error: On any non-2xx response or wire failure.
 */
export async function fetchCacheStatus(pipeline: PipelineJSON): Promise<CacheStatusResponse> {
  const resp = await fetch('/api/wfc/cache-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pipeline),
  });
  if (!resp.ok) throw new Error(`status ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

/**
 * Fetch the registered samples.
 *
 * Returns:
 *     The sample list, or ``null`` on a non-2xx response.
 */
export async function fetchSamples(): Promise<SampleInfo[] | null> {
  const resp = await fetch('/api/wfc/samples');
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Fetch the current archive status.
 *
 * Returns:
 *     The status payload, or ``null`` on a non-2xx response.
 */
export async function fetchArchiveStatus(): Promise<ArchiveStatus | null> {
  const resp = await fetch('/api/wfc/archive-status');
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Start an archive pass. The response is deliberately not inspected — a 409
 * (a pass already running, or a pipeline in flight) needs no special handling,
 * because the caller's follow-up status refresh already reflects whatever is
 * actually running.
 */
export async function postStartArchive(): Promise<void> {
  await fetch('/api/wfc/cache/archive', { method: 'POST' });
}
