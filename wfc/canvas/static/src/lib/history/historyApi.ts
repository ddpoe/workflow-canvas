/**
 * Typed fetch wrappers for all /api/wfc/* history endpoints.
 * This is the only file that knows about URL paths.
 */

import type { components } from '../types/api.js';
import type { PARAM_TYPES } from '../shared/types.js';

// ---------- Response types ----------

/** Why a cancelled run was cancelled (`WfcRun.cancelReason`). */
export type CancelReason =
  | 'user' | 'upstream_failed' | 'not_run' | 'interrupted' | 'engine_failed';

/**
 * One pipeline as `/api/wfc/pipelines` reports it: its status (`running`,
 * `succeeded`, `failed`, `cancelled` or `mixed`, by the server's one rule),
 * the runs its frozen document expects (null without one) and its method
 * nodes in document order.
 */
export type PipelineSummary = components['schemas']['PipelineSummary'];

/** One output's availability in the run payload (`WfcRun.outputAvailability`). */
export type OutputAvailability = 'available' | 'changed';

export interface WfcRun {
  id: string;
  module: string;
  method: string;
  /** The label of the method version the run ran (`v{version}` on screen); null when unrecorded. */
  version: number | null;
  /**
   * That version's content identity (what a pin holds); null when the run
   * records no version or its version row has no env fingerprint.
   */
  versionIdentity: string | null;
  timestamp: number;       // Unix epoch milliseconds
  duration: number;        // seconds
  status: string;          // "success" | "failed" | "running" | "cancelled" | "unknown"
  inputs: Record<string, unknown>;
  outputs: Record<string, string>;
  /**
   * Each output's availability, keyed like ``outputs``: ``changed`` when wfc
   * found the output changed after the run wrote it (it can no longer be
   * used; the step has to run again), else ``available``. Read from the
   * run's records; the server sends it for every output.
   */
  outputAvailability: Record<string, OutputAvailability>;
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
   * filled. ``sourceSlot`` is the output slot of the upstream run the input
   * consumed (null when the input record names none) and ``cacheKey`` that
   * upstream run's own cache key (null for a keyless parent). Order matches
   * ``parentRunIds``; use this for per-slot parent chips in the run detail
   * view.
   */
  parents: {
    slot: string;
    sourceRunId: string;
    sourceSlot: string | null;
    cacheKey: string | null;
  }[];
  /**
   * The samples this run read directly, one entry per input slot a sample
   * filled. Kept apart from ``parents``: a sample read has no source run.
   * ``contentHash`` is the sample's content hash at the claim (null when the
   * read predates its recording). Empty or absent for a run that read no
   * sample.
   */
  sampleInputs?: { slot: string; sample: string; contentHash: string | null }[];
  // The rest of the cache key's inputs: the method version's code
  // fingerprint and the run's own env name and env fingerprint. Null when
  // the run did not record them.
  codeFingerprint: string | null;
  envName: string | null;
  envFingerprint: string | null;
  // Recorded at the claim, not in the key: the commit the run ran at, the
  // installed wfc version and the hash of its pipeline document. Null when
  // the run did not record them.
  gitCommit: string | null;
  wfcVersion: string | null;
  pipelineDocHash: string | null;
  // Whether the run's key was computed under an earlier key rule than the
  // current one. The rule's number is never sent.
  keyedUnderEarlierRule: boolean;
  // The hash of the run's params (equal params give an equal hash whatever
  // their key order); null when the params cannot be hashed.
  paramsHash: string | null;
  // The input fingerprint over the run's recorded inputs, the value its key
  // was built over; null when a parent records no source slot, a sample read
  // no content hash, the run has no key or was keyed under an earlier rule.
  inputsHash: string | null;
  // Each param's type as the run's method version declares it, by name. A
  // param the contract does not declare is absent.
  paramTypes: Record<string, (typeof PARAM_TYPES)[number]>;
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
  // That failed run's method and sample, so a cancelled run's reason can
  // name the step and sample before the run id. Null when it names none.
  cancelledDueToMethod?: string | null;
  cancelledDueToSample?: string | null;
  // Why a cancelled run was cancelled: the user stopped it, an upstream run
  // failed, or the pipeline ended before it started. Null when not cancelled.
  cancelReason?: CancelReason | null;
  // The pipeline node the run executed, by the node's document id. Never
  // shown; the Pipelines view sorts and groups a pipeline's runs by it.
  nodeId?: string | null;
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

export function fetchPipelineSummaries(): Promise<PipelineSummary[]> {
  return fetchJson('/api/wfc/pipelines');
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

// ---------- Run bundles (Export run / Export runs) ----------

/** One step of an export plan: a run the bundle holds, numbered in dependency order. */
export interface ExportRunsStep {
  number: number;
  run_id: number;
  method: string;
  sample: string;
  parent_run_ids: number[];
  image: string | null;
  pullable_ref: string | null;
}

/** A reason the bundle cannot be complete; any one refuses the whole export. */
export interface ExportRunsRefusal {
  run_id: number | null;
  reason: string;
}

/** A step whose image was never pushed: only this machine holds it. */
export interface ExportRunsWarning {
  run_id: number;
  env: string;
  image: string;
  state: string;
}

/**
 * The dry run's plan (`POST /api/wfc/export-runs?dryRun=true`), which writes
 * no bundle. `ok` is false when there is any refusal. `token` names exactly
 * what the download would write, the options included; the download sends it
 * back. `warning_lines` is the never-pushed image warning in Run bundle's own
 * words, one line per image (empty when images are carried), and
 * `refused_text` the lead to show above the refusals.
 */
export interface ExportRunsPlan {
  requested: number[];
  steps: ExportRunsStep[];
  substitutions: { run_id: number; original_run_id: number }[];
  refusals: ExportRunsRefusal[];
  warnings: ExportRunsWarning[];
  warning_lines: string[];
  refused_text: string;
  with_outputs: boolean;
  with_image: boolean;
  ok: boolean;
  token: string;
}

/**
 * What the bundle carries besides the runs' record: the outputs' bytes
 * (`wfc export-run --with-outputs`) and each step's image (`--with-image`).
 */
export interface ExportRunsOptions {
  withOutputs: boolean;
  withImage: boolean;
}

/** The defaults `wfc export-run` uses: neither the outputs nor the images. */
export const EXPORT_RUNS_DEFAULTS: ExportRunsOptions = { withOutputs: false, withImage: false };

type ExportRunsRequest = components['schemas']['ExportRunsRequest'];

const EXPORT_RUNS_URL = '/api/wfc/export-runs';

function exportRunsBody(runIds: string[], options: ExportRunsOptions, planToken?: string): string {
  const body: ExportRunsRequest = {
    run_ids: runIds,
    with_outputs: options.withOutputs,
    with_image: options.withImage,
    plan_token: planToken ?? null,
  };
  return JSON.stringify(body);
}

/**
 * The download refused the export (HTTP 409): the selection cannot be held
 * completely, or it changed since the dry run. `reasons` lists every reason
 * the server gave (empty for a changed plan); `message` is its own words.
 */
export class ExportRunsRefusedError extends Error {
  readonly reasons: string[];

  constructor(message: string, reasons: string[]) {
    super(message);
    this.name = 'ExportRunsRefusedError';
    this.reasons = reasons;
  }
}

/**
 * The dry run of an export: the plan for the runs and every run above them.
 *
 * Writes no bundle. A refused plan still answers (with `ok` false and every
 * refusal listed).
 *
 * Args:
 *     runIds: The runs asked for.
 *     options: What the bundle carries besides the record (default: neither
 *         outputs nor images).
 *
 * Returns:
 *     The plan, carrying the token the download sends back.
 *
 * Raises:
 *     Error: The server's `detail` on a non-2xx response (no runs, a run id
 *         that is not a number).
 */
export async function planExportRuns(
  runIds: string[],
  options: ExportRunsOptions = EXPORT_RUNS_DEFAULTS,
): Promise<ExportRunsPlan> {
  const resp = await fetchJson<{ plan: ExportRunsPlan }>(`${EXPORT_RUNS_URL}?dryRun=true`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: exportRunsBody(runIds, options),
  });
  return resp.plan;
}

/**
 * Download the bundle a dry run planned, as one zip.
 *
 * The request carries the plan's token, so the server writes only the plan
 * the user was shown. The save name is the server's, read off
 * `Content-Disposition`.
 *
 * Args:
 *     runIds: The same runs the dry run was asked for.
 *     planToken: The dry run's `plan.token`.
 *     options: The same options the dry run was asked with (the token
 *         covers them).
 *     signal: Aborts the request, e.g. when the dialog closes.
 *
 * Returns:
 *     The zip and the filename to save it under.
 *
 * Raises:
 *     ExportRunsRefusedError: On a 409, with the server's message and every
 *         reason it listed.
 *     Error: The server's `detail` on any other non-2xx response.
 */
export async function downloadExportRuns(
  runIds: string[],
  planToken: string,
  options: ExportRunsOptions = EXPORT_RUNS_DEFAULTS,
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string }> {
  const resp = await fetch(EXPORT_RUNS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: exportRunsBody(runIds, options, planToken),
    signal,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    let detail: unknown;
    try {
      detail = JSON.parse(text)?.detail;
    } catch {
      // Not JSON: the raw text is all there is.
    }
    if (resp.status === 409 && detail && typeof detail === 'object') {
      const d = detail as { message?: unknown; reasons?: unknown };
      const reasons = Array.isArray(d.reasons) ? d.reasons.map(String) : [];
      throw new ExportRunsRefusedError(String(d.message ?? 'Export refused.'), reasons);
    }
    throw new Error(typeof detail === 'string' ? detail : `Export failed: ${text}`);
  }
  const blob = await resp.blob();
  const disposition = resp.headers.get('Content-Disposition');
  const filename = disposition?.match(/filename="?([^"]+)"?/)?.[1] || 'wfc_runs.zip';
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
 * The URL that downloads a pipeline run's frozen `pipeline.json` as a file.
 *
 * The frozen copy names every method node's resolved version, so running the
 * downloaded document again runs the same versions.
 *
 * Args:
 *     pipelineId: The pipeline run's id (its directory name).
 *
 * Returns:
 *     `/api/pipelines/<id>/download`, the id URL-encoded.
 */
export function pipelineDownloadUrl(pipelineId: string): string {
  return `/api/pipelines/${encodeURIComponent(pipelineId)}/download`;
}

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
 * The server refused to synthesize a run's lineage (HTTP 422).
 *
 * ``detail`` is the server's own message (empty when the body carried none),
 * shown to the user as written.
 */
export class LineageRefusedError extends Error {
  readonly detail: string;

  constructor(detail: string) {
    super('LINEAGE_SYNTHESIS_FAILED');
    this.name = 'LineageRefusedError';
    this.detail = detail;
  }
}

/**
 * The flash text for a failed Open-lineage action.
 *
 * A server refusal shows the server's words as written; the hop-cap and
 * unpinnable-run refusals both arrive this way.
 *
 * Args:
 *     err: What ``fetchLineagePipeline`` threw.
 *
 * Returns:
 *     The message to flash as an error.
 */
export function lineageFailureMessage(err: unknown): string {
  if (err instanceof LineageRefusedError) {
    return err.detail || "Couldn't reconstruct this run's lineage.";
  }
  const code = err instanceof Error ? err.message : String(err);
  if (code === 'LINEAGE_RUN_NOT_FOUND') return 'Run not found.';
  return `Failed to load lineage: ${code}`;
}

/**
 * Action 2: Fetch a synthesized literal-only lineage pipeline for a run.
 * Returns the parsed PipelineJSON ready for ``loadPipeline()``.
 *
 * Throws ``LINEAGE_RUN_NOT_FOUND`` on 404. On 422 (the server refused to
 * synthesize: a malformed ancestor chain, or a run whose version cannot be
 * pinned) throws a ``LineageRefusedError`` carrying the server's ``detail``,
 * which the caller shows as written.
 */
export async function fetchLineagePipeline(runId: string): Promise<PipelineJSON> {
  const resp = await fetch(`/api/runs/${encodeURIComponent(runId)}/lineage-pipeline`);
  if (resp.status === 404) throw new Error('LINEAGE_RUN_NOT_FOUND');
  if (resp.status === 422) {
    const body = await resp.json().catch(() => null);
    const detail = body && typeof body.detail === 'string' ? body.detail : '';
    throw new LineageRefusedError(detail);
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`API error ${resp.status}: ${text}`);
  }
  return resp.json();
}
