/**
 * Network calls for the dev toolbar.
 *
 * Separate from `api.ts` because `/api/dev/*` is outside the generated
 * contract: `scripts/dev_routes.py` serves these routes in development only,
 * and the toolbar that calls them renders only when the server reports dev
 * mode. The one exception is `/api/wfc/refresh`, a real route the toolbar
 * pokes after a reset — it lives here because the toolbar is its only caller
 * in this area.
 *
 * The caller keeps its own `try`/`catch` and its own flash messages.
 */
import type { PipelineJSON } from '../shared/types.js';

/**
 * Fetch a generated demo pipeline for the chosen topology.
 *
 * A reference-fed topology may first seed a run on the server, so the
 * request can take one real run's time to answer.
 *
 * Args:
 *     topology: The graph shape to generate (`fan_in`, `reference_root`, …).
 *
 * Returns:
 *     The parsed pipeline, ready for ``loadPipeline()``.
 *
 * Raises:
 *     Error: On any non-2xx response, carrying the server's detail so a
 *     refused or failed seed run is readable in the flash.
 */
export async function fetchDemoPipeline(topology: string): Promise<PipelineJSON> {
  const resp = await fetch(
    `/api/dev/demo-pipeline?topology=${encodeURIComponent(topology)}`
  );
  if (!resp.ok) {
    let detail = '';
    try {
      const body = await resp.json();
      detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      detail = await resp.text().catch(() => '');
    }
    throw new Error(`Failed to fetch demo pipeline (${resp.status}): ${detail}`);
  }
  return resp.json();
}

/**
 * Drop and re-create the development database. The response is not
 * inspected — the caller reports success from the absence of a throw.
 */
export async function postResetDb(): Promise<void> {
  await fetch('/api/dev/reset-db', { method: 'POST' });
}

/**
 * Ask the server to re-read the registry. The response is not inspected.
 */
export async function postRefresh(): Promise<void> {
  await fetch('/api/wfc/refresh', { method: 'POST' });
}
