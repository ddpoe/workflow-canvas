/**
 * Svelte stores for canvas state: nodes, edges, run state, selection.
 */
import { writable, derived, get } from 'svelte/store';
import type { Node, Edge } from '@xyflow/svelte';
import type {
  CanvasNodeData,
  WorkflowRunState,
  ModuleDef,
  SampleInfo,
  PipelineVariable,
  PipelineVariables,
} from './types.js';

// ---------- Pipeline Variables (Track 2, ADR-017) ----------
//
// The Pipeline Variables panel (collapsible sibling of Samples in the
// Builder tab) reads/writes this store. Bind/unbind on a row is owned by
// the per-row paramEditorActor (D-4) — the store deliberately exposes
// NO bindParam/unbindParam helpers. Only the variable dictionary lives
// here; the actor's `boundVariable` field is the per-row source of truth.
// `+ Add variable` in the panel is the SOLE creation surface (D-6).
export const pipelineVariables = writable<PipelineVariables>({});

/** Add or replace a variable. Used by the panel's `+ Add variable` button. */
export function createVariable(name: string, type: string, value: unknown): void {
  pipelineVariables.update($v => ({ ...$v, [name]: { type, value } }));
}

/** Delete a variable. Caller should confirm if any rows are bound. */
export function deleteVariable(name: string): void {
  pipelineVariables.update($v => {
    const next = { ...$v };
    delete next[name];
    return next;
  });
}

/** Look up a variable by name. Returns undefined if absent. */
export function lookupVariable(name: string): PipelineVariable | undefined {
  return get(pipelineVariables)[name];
}

// ---------- Core graph stores ----------
export const nodes = writable<Node<CanvasNodeData>[]>([]);
export const edges = writable<Edge[]>([]);

// ---------- Selection ----------
export const selectedNodeId = writable<string | null>(null);
export const selectedNode = derived(
  [nodes, selectedNodeId],
  ([$nodes, $id]) => $id ? $nodes.find(n => n.id === $id) ?? null : null
);

// ---------- Module registry ----------
export const modules = writable<ModuleDef[]>([]);

// ---------- Registered samples ----------
// Loaded once at app start; refreshed on demand. CustomNode reads this to
// show file_type per sample on Input Selector nodes.
export const samples = writable<SampleInfo[]>([]);

export async function loadSamples(): Promise<void> {
  try {
    const resp = await fetch('/api/wfc/samples');
    if (resp.ok) samples.set(await resp.json());
  } catch { /* noop */ }
}

// ---------- Run state ----------
//
// `nodeStates` is no longer stored here — per-node lifecycle now lives
// in the spawned `nodeRunActor` children of `pipelineRunActor`
// (see machines/root.ts). This store keeps just the pipeline-level
// fields (jobId, running, pipelineError) that the canvas chrome reads.
export const runState = writable<WorkflowRunState>({
  jobId: null,
  running: false,
  nodeStates: {},
  pipelineError: null,
});

// ---------- Pipeline name ----------
export const pipelineName = writable<string>('My Pipeline');

// ---------- Helpers ----------
let nodeCounter = 0;
export function nextNodeId(): string {
  nodeCounter += 1;
  return `node_${nodeCounter}`;
}

export function resetNodeCounter(max: number = 0): void {
  nodeCounter = max;
}

export function updateNodeData(nodeId: string, partial: Partial<CanvasNodeData>): void {
  nodes.update($nodes => {
    const node = $nodes.find(n => n.id === nodeId);
    if (node) {
      // Mutate data in place — spreading the node object creates a new
      // reference that makes SvelteFlow lose its internally tracked
      // drag position, causing the node to jump.
      Object.assign(node.data, partial);
    }
    return [...$nodes];
  });
}

// `setNodeRunStatus` was removed in ADR-016 Step 8. Per-node lifecycle
// now lives in the spawned `nodeRunActor` (see machines/root.ts); the
// polling service in `machines/services.ts` sends typed NODE_* events
// into the actor tree, replacing the legacy direct store write.

export function deleteNodes(nodeIds: string[]): void {
  const ids = new Set(nodeIds);
  nodes.update(ns => ns.filter(n => !ids.has(n.id)));
  edges.update(es => es.filter(e => !ids.has(e.source) && !ids.has(e.target)));
  selectedNodeId.update(id => id && ids.has(id) ? null : id);
}

export function clearRunState(): void {
  runState.set({ jobId: null, running: false, nodeStates: {}, pipelineError: null });
  nodes.update($nodes => {
    for (const n of $nodes) {
      n.data.runStatus = 'idle';
      n.data.runTally = undefined;
    }
    return [...$nodes];
  });
  // Reset the spawned actor tree alongside the legacy fields. Lazy
  // import to avoid circular dependency between stores.ts and
  // machines/root.ts (root.ts imports requestCommitAll from stores.ts).
  import('./machines/root.js').then(m => m.dispatchReset()).catch(() => {});
}

export function setPipelineError(err: import('./types.js').PipelineError | null): void {
  runState.update(rs => ({ ...rs, pipelineError: err }));
}

/**
 * Non-blocking transient toast for confirmations and minor dev-toolbar
 * failures — the kind of message that doesn't deserve the persistent
 * error banner but shouldn't be an `alert()` either.
 *
 * Distinguished from ``pipelineError`` by lifetime: toasts auto-dismiss,
 * the banner stays until the user acts. Use this for successes ("Workflow
 * is valid!") and low-stakes failures ("Load demo failed: …"). Use
 * ``setPipelineError`` for anything the user needs to fix before the next
 * Run can succeed.
 */
export type FlashKind = 'success' | 'error' | 'info';
export interface FlashToast {
  message: string;
  kind: FlashKind;
  id: number;
}

export const flashToast = writable<FlashToast | null>(null);

let _flashCounter = 0;
let _flashTimer: ReturnType<typeof setTimeout> | null = null;
export function showFlash(
  message: string,
  kind: FlashKind = 'success',
  durationMs = 2500,
): void {
  const id = ++_flashCounter;
  flashToast.set({ message, kind, id });
  if (_flashTimer) clearTimeout(_flashTimer);
  _flashTimer = setTimeout(() => {
    flashToast.update(t => (t && t.id === id ? null : t));
    _flashTimer = null;
  }, durationMs);
}

export function dismissFlash(): void {
  if (_flashTimer) {
    clearTimeout(_flashTimer);
    _flashTimer = null;
  }
  flashToast.set(null);
}

// ---------- Param editing state ----------
// (ADR-016 Phase 2 expand) `commitAllSignal` / `dirtyParams` /
// `markDirty` / `requestCommitAll` are gone. Param edit lifecycle now
// lives in spawned `paramEditorActor` / `variantActor` instances under
// the `paramEditorAggregator` singleton (see `machines/root.ts`).
// Lock All / Run-button preflight call `awaitAllCommitted()` from
// `machines/root.ts` instead of bumping a writable counter.
