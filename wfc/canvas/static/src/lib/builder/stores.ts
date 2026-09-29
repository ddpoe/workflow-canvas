/**
 * Svelte stores for canvas state: nodes, edges, run state, selection.
 */
import { writable, derived, get } from 'svelte/store';
// `undo.ts` imports this module's stores in turn — a cycle inside `builder/`,
// the third in this unit beside the two lazy-import ones (`clearRunState` →
// `machines/root.ts`, and `root.ts` → `pipeline.ts`). It is safe because
// neither module reads the other while it loads: `deleteNodes` calls
// `pushState` when it runs, not at import time.
import { pushState } from './undo.js';
import { fetchSamples } from './api.js';
import type { Node, Edge } from '@xyflow/svelte';
import type {
  CanvasNodeData,
  WorkflowRunState,
  ModuleDef,
  SampleInfo,
  PipelineVariable,
  PipelineVariables,
} from '../shared/types.js';

// ---------- Pipeline Variables ----------
//
// The Pipeline Variables panel (a collapsible Sidebar section in the
// Builder tab) reads/writes this store. Bind/unbind on a row is owned by
// the per-row paramEditorActor — the store deliberately exposes
// NO bindParam/unbindParam helpers. Only the variable dictionary lives
// here; the actor's `boundVariable` field is the per-row source of truth.
// `+ Add variable` in the panel is the SOLE creation surface.
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
    const list = await fetchSamples();
    if (list) samples.set(list);
  } catch { /* noop */ }
}

// ---------- Run state ----------
export const runState = writable<WorkflowRunState>({
  jobId: null,
  running: false,
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

// Per-node run lifecycle lives in the spawned `nodeRunActor` (see
// machines/root.ts), not in this module; the polling service in
// `machines/services.ts` sends typed NODE_* events into the actor tree.

/**
 * Delete nodes, every edge touching them, and the selection if it was one of
 * them.
 *
 * This is the one delete path. Svelte Flow's own delete, the canvas key
 * handler, the node's delete button, the Inspector's and the context menu all
 * hand their ids here, and the undo snapshot is recorded here rather than by
 * each caller, so one delete is one step back however it was asked for. A
 * call that removes nothing — the second handler firing for one key press —
 * records nothing.
 */
export function deleteNodes(nodeIds: string[]): void {
  const ids = new Set(nodeIds);
  if (!get(nodes).some(n => ids.has(n.id))) return;
  pushState();
  nodes.update(ns => ns.filter(n => !ids.has(n.id)));
  edges.update(es => es.filter(e => !ids.has(e.source) && !ids.has(e.target)));
  selectedNodeId.update(id => id && ids.has(id) ? null : id);
}

export function clearRunState(): void {
  runState.set({ jobId: null, running: false, pipelineError: null });
  nodes.update($nodes => {
    for (const n of $nodes) {
      n.data.runStatus = 'idle';
      n.data.runTally = undefined;
    }
    return [...$nodes];
  });
  // Reset the spawned actor tree alongside the node-data run fields. Lazy import to
  // avoid a load-time cycle with `machines/root.ts`, which imports
  // `runState`, `setPipelineError` and `updateNodeData` from this module.
  import('../machines/root.js').then(m => m.dispatchReset()).catch(() => {});
}

export function setPipelineError(err: import('../shared/types.js').PipelineError | null): void {
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
// Param edit lifecycle lives in spawned `paramEditorActor` / `variantActor`
// instances under the `paramEditorAggregator` singleton (see
// `machines/root.ts`), not in this module. Lock All / Run-button preflight
// call `awaitAllCommitted()` from `machines/root.ts`.
