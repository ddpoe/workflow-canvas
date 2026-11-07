/**
 * Draft steps in History: the rows, pills and wording every History view
 * uses for a draft's latest draft run.
 *
 * A draft step is kept but not recorded. History draws it beside the
 * recorded runs as a row of its own, shaped like a run so the Lineages and
 * Descendants derivations place it with the same rules: its id is its draft
 * string and its upstreams are the recorded runs and draft steps that fed
 * it. A draft step therefore hangs under the recorded run it read, as a
 * sibling of that run's recorded children, never under a recorded run it
 * does not own.
 */
import type { DraftStep, WfcRun } from './historyApi.js';
import type { PillKind } from './historyUtils.js';

/** Whether a History row id names a draft step (its draft string). */
export function isDraftRef(id: string | null | undefined): boolean {
  return !!id && id.startsWith('draft:');
}

/** The owning draft's module and method, from `<module>.<method>`. */
export function draftOwner(step: Pick<DraftStep, 'draft'>): { module: string; method: string } {
  const dot = step.draft.indexOf('.');
  return { module: step.draft.slice(0, dot), method: step.draft.slice(dot + 1) };
}

/**
 * A draft step as a History row: a run-shaped record with the step on
 * `draftStep`. It has no run id, no cache key and is never a cache hit.
 *
 * Args:
 *     step: The draft step.
 *
 * Returns:
 *     The row, its id the step's draft string.
 */
export function draftStepRun(step: DraftStep): WfcRun {
  const parentRunIds = step.parentRunIds ?? [];
  const parentDraftSteps = step.parentDraftSteps ?? [];
  return {
    id: step.draftString,
    module: step.module,
    method: step.method,
    version: step.version ?? null,
    versionIdentity: null,
    timestamp: step.timestamp ?? 0,
    duration: step.duration ?? 0,
    status: step.status,
    inputs: {},
    outputs: {},
    outputAvailability: {},
    metrics: {},
    dataSource: step.sample,
    parentRunIds,
    parents: [],
    codeFingerprint: null,
    envName: null,
    envFingerprint: null,
    gitCommit: null,
    wfcVersion: null,
    pipelineDocHash: null,
    keyedUnderEarlierRule: false,
    paramsHash: null,
    inputsHash: null,
    paramTypes: {},
    experimentId: '',
    runName: step.method,
    nid: step.nid ?? '',
    user: '',
    favorite: false,
    pipelineId: step.pipelineId,
    pipelineName: step.pipelineName ?? null,
    scriptPath: null,
    archivedAt: null,
    error_message: step.error_message ?? null,
    nodeId: step.nodeId,
    cacheSourceRunId: null,
    upstreamRunIds: [...parentRunIds, ...parentDraftSteps],
    draftStep: step,
  };
}

/** The pill class of a draft step's status (the run pills' colours). */
export function draftPillKind(status: string): PillKind {
  switch (status) {
    case 'success': return 'succeeded';
    case 'failed': return 'failed';
    case 'cancelled': return 'cancelled';
    case 'running':
    case 'pending': return 'running';
    default: return 'unknown';
  }
}

const DRAFT_STATUS_WORD: Record<string, string> = {
  success: 'ok', failed: 'failed', cancelled: 'cancelled', running: 'running', pending: 'pending',
};

/** A draft step's pill text: `● Draft · ok`, `● Draft · failed` (CSS upper-cases it). */
export function draftPillText(status: string): string {
  const word = DRAFT_STATUS_WORD[status] ?? status;
  const dot = status === 'running' || status === 'pending' ? '' : '● ';
  return `${dot}Draft · ${word}`;
}

/**
 * The border colour of a draft-mode card in History: only an exception
 * status is drawn (failed, cancelled, running), as red, slate or amber
 * dashes; a succeeded draft-mode step keeps the ordinary neutral border
 * (null).
 */
export function draftExceptionColor(status: string): string | null {
  switch (status) {
    case 'failed': return 'var(--status-failed)';
    case 'cancelled': return 'var(--status-cancelled)';
    case 'running':
    case 'pending': return 'var(--status-active)';
    default: return null;
  }
}

/** The name History gives a pipeline: its name, else its id. */
export function pipelineLabel(name: string | null | undefined, id: string): string {
  return name || id;
}

/**
 * Why a lineage with this draft step cannot become a rerun bundle, naming
 * the owning draft and the pipeline to run again.
 */
export function draftBundleRefusal(step: DraftStep): string {
  const owner = draftOwner(step).method;
  const pipeline = pipelineLabel(step.pipelineName, step.pipelineId);
  return `This lineage has a draft step, ${owner} (DRAFT). Register it and run ${pipeline} again to export a bundle.`;
}

/** What a draft step's run panel says about how long it is kept. */
export function keptUntilText(step: DraftStep): string {
  return `${draftOwner(step).method}’s draft runs again`;
}
