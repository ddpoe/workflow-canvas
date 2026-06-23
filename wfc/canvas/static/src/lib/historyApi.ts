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
  experimentId: string;
  runName: string;
  nid: string;           // Node ID: auto-versioned (v1, v2...) or custom label
  user: string;
  favorite: boolean;
  pipelineId: string | null;
  scriptPath: string | null;
  // Optional user-editable display name. Falls back to `method` when absent.
  // TODO(backend): persist via PATCH /api/runs/:id body { name }
  name?: string | null;
  tags?: string[];
  // Unix epoch ms when the run was archived, or null/undefined if live.
  // Archive is a soft-delete; hard delete only permitted after archiving.
  archivedAt?: number | null;
  // For collapsed fan-in runs (dataSource === "__all__"), the real sample
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
    throw new Error(`API error ${resp.status}: ${text}`);
  }
  return resp.json();
}

// ---------- API functions ----------

export function fetchStatus(): Promise<WfcStatus> {
  return fetchJson('/api/wfc/status');
}

export function fetchRuns(): Promise<WfcRun[]> {
  return fetchJson('/api/wfc/runs');
}

export function fetchRun(runId: string): Promise<WfcRun> {
  return fetchJson(`/api/wfc/run/${encodeURIComponent(runId)}`);
}

export function fetchExperiments(): Promise<Experiment[]> {
  return fetchJson('/api/wfc/experiments');
}

export function fetchLineage(runId: string): Promise<Lineage> {
  return fetchJson(`/api/wfc/lineage/${encodeURIComponent(runId)}`);
}

export function fetchRunTree(runId: string): Promise<WfcRun[]> {
  return fetchJson(`/api/wfc/tree/${encodeURIComponent(runId)}`);
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

export function exportArtifactsUrl(runIds: string[], fileTypes?: string[]): { url: string; body: string } {
  return {
    url: '/api/wfc/export-artifacts',
    body: JSON.stringify({ run_ids: runIds, file_types: fileTypes ?? null }),
  };
}

export function artifactDownloadUrl(runId: string, artifactPath: string): string {
  return `/api/wfc/run/${encodeURIComponent(runId)}/artifact/${artifactPath}`;
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

/** Replace the tags list for a run. */
export function setTags(runId: string, tags: string[]): Promise<void> {
  return patchRun(runId, { tags });
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
 * Delete a run.
 * TODO(backend): implement `DELETE /api/wfc/run/:id` with ref-counted DVC
 * cleanup + 409 on descendants. Deferred to a dedicated cycle.
 */
export function deleteRun(runId: string): Promise<void> {
  console.warn(`TODO(backend): deleteRun(${runId}) — DELETE /api/wfc/run/:id not implemented`);
  return Promise.resolve();
}

// ---------- Load-in-canvas (Actions 1 & 2) ----------

import type { PipelineJSON } from './types.js';

/**
 * Action 1: Fetch the literal pipeline.json that was written at submission
 * time. Returns the parsed PipelineJSON ready for ``loadPipeline()``.
 *
 * Throws on 404 (pipeline never reached run-generation) — caller surfaces
 * the SPEC's 404 toast string.
 */
export async function fetchPipelineDocument(pipelineId: string): Promise<PipelineJSON> {
  // Track 2 (ADR-017): try the editable sidecar first so History "Open
  // in canvas" rehydrates pipelineVariables + per-row binding chips.
  // The editable endpoint falls back to pipeline.json server-side for
  // legacy runs that pre-date the sidecar, so a 200 here may carry a
  // post-substitution form too — which is fine, parsePipelineJSON
  // handles it (no `variables` block + no `$var` refs = no-op for
  // Track 2 logic). We only fall back client-side on 404, in case the
  // editable endpoint is unavailable on an older deployment.
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
 * on 422 — caller maps these to SPEC toast copy.
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
