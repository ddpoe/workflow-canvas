/**
 * Typed fetch wrappers for all /api/wfc/* history endpoints.
 * This is the only file that knows about URL paths.
 */

// ---------- Response types ----------

export interface WfcRun {
  id: string;
  module: string;
  method: string;
  version: string;
  timestamp: number;       // Unix epoch milliseconds
  duration: number;        // seconds
  status: string;          // "success" | "failed" | "running" | "cancelled" | "unknown"
  inputs: Record<string, unknown>;
  outputs: Record<string, string>;
  metrics: Record<string, number>;
  dataSource: string;      // sample name
  /**
   * Full upstream run lineage — one entry per input slot. Empty for runs
   * with no registered parents (root runs spawned by an input_selector).
   * A method with fan-in (multiple parents) contributes multiple entries,
   * matching ``parents[*].sourceRunId`` below.
   */
  parentRunIds: string[];
  /**
   * Slot-aware view of the same parents. ``slot`` is the method input name
   * (``experiment_config``, ``stitched_dir``, …) that this upstream run
   * filled. Order matches ``parentRunIds``; use this for per-slot parent
   * chips in the run detail view.
   */
  parents: { slot: string; sourceRunId: string }[];
  /**
   * The samples this run read directly, one entry per input slot a sample
   * filled. Kept apart from ``parents``: a sample read has no source run.
   * Empty or absent for a run that read no sample.
   */
  sampleInputs?: { slot: string; sample: string }[];
  experimentId: string;
  runName: string;
  nid: string;           // Node ID: auto-versioned (v1, v2...) or custom label
  user: string;
  favorite: boolean;
  pipelineId: string | null;
  // Human-readable pipeline name from the Builder toolbar at submission
  // time. Null/absent for legacy or unnamed pipelines.
  pipelineName?: string | null;
  scriptPath: string | null;
  // Optional user-editable display name. Falls back to `method` when absent.
  // Renaming persists through `renameRun`, which PATCHes `nid`.
  name?: string | null;
  tags?: string[];
  // Unix epoch ms when the run was archived, or null/undefined if live.
  // Archive is a soft-delete; hard delete only permitted after archiving.
  archivedAt?: number | null;
  // For collapsed fan-in runs (dataSource === COLLAPSED_SAMPLE), the real sample
  // list bundled into the single run. Empty/absent for per-sample runs.
  bundledSamples?: string[];
  // Populated when status === "failed". Both absent otherwise.
  error_message?: string | null;
  error_traceback?: string | null;
  // Populated when status === "cancelled". The string ID of the failed
  // run whose subtree caused this target to be skipped. Matches the
  // ``parentRunIds`` convention (all run IDs are strings on the canvas).
  cancelledDueToRunId?: string | null;
  // For cache-hit audit rows: the original run whose outputs were reused.
  // Null/absent on fresh executions. RunDetailPanel surfaces a "Cached
  // from #N" fact row when present so users can distinguish reused from
  // freshly-executed results.
  cacheSourceRunId?: string | null;
  // Resolved upstreams from the server's lineage relation: the input edges
  // in slot order, then ``cacheSourceRunId`` for a cache-hit row. The server
  // always sends it. The History views walk this field and none re-derives it.
  upstreamRunIds: string[];
}

export interface Experiment {
  id: string;
  name: string;
  module: string;
  runCount: number;
  creationTime: number;
}

export interface Lineage {
  run: WfcRun | null;
  ancestors: WfcRun[];
  descendants: WfcRun[];
}

export interface Artifact {
  name: string;
  size: number;
  is_image: boolean;
  extension: string;
  // TODO(backend): server should return `type: 'file' | 'dir'` explicitly and, for
  // directories, `count` and optionally `children`. Until then, the frontend
  // derives type from the name via `deriveArtifactType()` below.
  type?: 'file' | 'dir';
  count?: number;
  children?: Artifact[];
}

/**
 * Heuristic: treat any artifact whose name ends with "/" as a directory.
 * TODO(backend): remove once the API returns an explicit `type` field.
 */
export function deriveArtifactType(a: Artifact): 'file' | 'dir' {
  if (a.type) return a.type;
  return a.name.endsWith('/') ? 'dir' : 'file';
}

export interface MethodInfo {
  name: string;
  module: string;
  script_path: string | null;
  env: string;
}

export interface ExportTypeBreakdown {
  ext: string;
  count: number;
  size_bytes: number;
}

export interface ExportPreviewFile {
  method: string;
  run_name: string;
  artifact: string;
  ext: string;
  size_bytes: number;
}

export interface ExportPreviewResponse {
  total_count: number;
  run_count: number;
  method_count: number;
  total_size_bytes: number;
  methods: string[];
  by_type: ExportTypeBreakdown[];
  files: ExportPreviewFile[];
}

export interface WfcStatus {
  loaded: boolean;
  path: string | null;
  modules?: number;
  runs?: number;
}

// ---------- Fetch helpers ----------

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    // Route errors carry their message as a JSON `detail` string, written
    // for the user: show it as it was written. Anything else is raw, so it
    // keeps the status code for context.
    let detail: string | undefined;
    try {
      const body = JSON.parse(text);
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // Not JSON: the raw text is all there is.
    }
    throw new Error(detail ?? `API error ${resp.status}: ${text}`);
  }
  return resp.json();
}

// ---------- API functions ----------

export function fetchRuns(): Promise<WfcRun[]> {
  return fetchJson('/api/wfc/runs');
}

export function fetchRun(runId: string): Promise<WfcRun> {
  return fetchJson(`/api/wfc/run/${encodeURIComponent(runId)}`);
}

export function fetchModules(): Promise<string[]> {
  return fetchJson('/api/wfc/modules');
}

export function fetchMethods(): Promise<MethodInfo[]> {
  return fetchJson('/api/wfc/methods');
}

export function listArtifacts(runId: string): Promise<Artifact[]> {
  return fetchJson(`/api/wfc/run/${encodeURIComponent(runId)}/artifacts`);
}

/**
 * Fetch runs that were cancelled because this run (or its subtree) failed.
 * Returns [] when the run didn't fail or cascaded no skips.
 */
export function fetchCancelledDescendants(runId: string): Promise<WfcRun[]> {
  return fetchJson(`/api/wfc/run/${encodeURIComponent(runId)}/cancelled-descendants`);
}

export function previewArtifacts(runIds: string[]): Promise<ExportPreviewResponse> {
  return fetchJson('/api/wfc/preview-artifacts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  });
}

function exportArtifactsUrl(runIds: string[], fileTypes?: string[]): { url: string; body: string } {
  return {
    url: '/api/wfc/export-artifacts',
    body: JSON.stringify({ run_ids: runIds, file_types: fileTypes ?? null }),
  };
}

/**
 * Download the selected runs' artifacts as one zip.
 *
 * The save name is the server's, read off `Content-Disposition` and falling
 * back to `wfc_export.zip`; the caller gets the blob and the name and owns the
 * anchor click.
 *
 * Args:
 *     runIds: The runs whose artifacts to bundle.
 *     fileTypes: Optional extension filter. Every type when omitted.
 *
 * Returns:
 *     The zip and the filename to save it under.
 *
 * Raises:
 *     Error: ``Export failed: <body text>`` on a non-2xx response.
 */
export async function exportArtifacts(
  runIds: string[],
  fileTypes?: string[],
): Promise<{ blob: Blob; filename: string }> {
  const { url, body } = exportArtifactsUrl(runIds, fileTypes);
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`Export failed: ${text}`);
  }
  const blob = await resp.blob();
  const disposition = resp.headers.get('Content-Disposition');
  const filename = disposition?.match(/filename="?([^"]+)"?/)?.[1] || 'wfc_export.zip';
  return { blob, filename };
}

export function artifactDownloadUrl(runId: string, artifactPath: string): string {
  return `/api/wfc/run/${encodeURIComponent(runId)}/artifact/${artifactPath}`;
}

/**
 * Open the log stream behind the run detail panel's Output tab.
 *
 * Full mode replays the whole persisted log instead of the tail. The caller
 * attaches its own `onmessage` / `onerror` and owns closing the stream.
 *
 * Args:
 *     runId: The run whose logs to stream.
 *     fullMode: Whether to ask for the full log.
 *
 * Returns:
 *     The open EventSource.
 */
export function openRunLogStream(runId: string, fullMode: boolean): EventSource {
  const qs = fullMode ? '?full=1' : '';
  return new EventSource(`/api/wfc/run/${encodeURIComponent(runId)}/stream-logs${qs}`);
}

// ---------- Mutation endpoints ----------

async function patchRun(runId: string, body: Record<string, unknown>): Promise<void> {
  const resp = await fetch(`/api/wfc/run/${encodeURIComponent(runId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`PATCH /api/wfc/run/${runId} failed: ${resp.status} ${text}`);
  }
}

/** Toggle favorite state. Persists to run_annotations. */
export function favoriteRun(runId: string, favorite: boolean): Promise<void> {
  return patchRun(runId, { favorite });
}

/**
 * Rename a run by writing the new label to `Run.nid`. Empty string clears
 * the override and the server regenerates the auto-version (v1, v2, ...).
 */
export function renameRun(runId: string, name: string): Promise<void> {
  return patchRun(runId, { nid: name });
}

/**
 * Archive (soft-delete) or unarchive a run. Archived runs are hidden from
 * default views but still present in the DB; a future hard-delete endpoint
 * will only operate on archived rows.
 */
export function setArchived(runId: string, archived: boolean): Promise<void> {
  return patchRun(runId, { archived });
}

/**
 * Delete a run. A no-op that logs a warning: the server has no
 * `DELETE /api/wfc/run/:id` endpoint.
 * TODO(backend): implement it with ref-counted DVC cleanup + 409 on descendants.
 */
export function deleteRun(runId: string): Promise<void> {
  console.warn(`TODO(backend): deleteRun(${runId}) — DELETE /api/wfc/run/:id not implemented`);
  return Promise.resolve();
}

// ---------- Load-in-canvas (Actions 1 & 2) ----------

import type { PipelineJSON } from '../shared/types.js';

/**
 * Action 1: Fetch the literal pipeline.json that was written at submission
 * time. Returns the parsed PipelineJSON ready for ``loadPipeline()``.
 *
 * Throws on 404 (pipeline never reached run-generation) — caller surfaces
 * a not-found toast.
 */
export async function fetchPipelineDocument(pipelineId: string): Promise<PipelineJSON> {
  // Try the editable sidecar first so History "Open in canvas"
  // rehydrates pipelineVariables + per-row binding chips.
  // The editable endpoint falls back to pipeline.json server-side for
  // runs that have no sidecar, so a 200 here may carry a
  // post-substitution form too — which is fine, parsePipelineJSON
  // handles it (no `variables` block + no `$var` refs = no bindings).
  // When the editable endpoint fails, fall back client-side to /document.
  const editableResp = await fetch(`/api/workflow/${encodeURIComponent(pipelineId)}/editable`);
  if (editableResp.ok) return editableResp.json();
  if (editableResp.status !== 404) {
    // For non-404 errors fall through to /document so a partial outage
    // doesn't block reload; the user will at least get the substituted
    // form.
  }
  const resp = await fetch(`/api/pipelines/${encodeURIComponent(pipelineId)}/document`);
  if (resp.status === 404) {
    throw new Error('PIPELINE_DOCUMENT_NOT_FOUND');
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`API error ${resp.status}: ${text}`);
  }
  return resp.json();
}

/**
 * Action 2: Fetch a synthesized literal-only lineage pipeline for a run.
 * Returns the parsed PipelineJSON ready for ``loadPipeline()``.
 *
 * Throws ``LINEAGE_RUN_NOT_FOUND`` on 404 and ``LINEAGE_SYNTHESIS_FAILED``
 * on 422 — caller maps these to toast copy.
 */
export async function fetchLineagePipeline(runId: string): Promise<PipelineJSON> {
  const resp = await fetch(`/api/runs/${encodeURIComponent(runId)}/lineage-pipeline`);
  if (resp.status === 404) throw new Error('LINEAGE_RUN_NOT_FOUND');
  if (resp.status === 422) throw new Error('LINEAGE_SYNTHESIS_FAILED');
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`API error ${resp.status}: ${text}`);
  }
  return resp.json();
}
