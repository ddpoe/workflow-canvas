/**
 * Shared pipeline export and import logic.
 *
 * Run/polling lifecycle has moved to `machines/` (ADR-016). This module
 * keeps only the pure export / load helpers plus thin re-exports of the
 * pure compile functions.
 */
import { get, writable, type Writable } from 'svelte/store';
import { nodes, edges, pipelineName, clearRunState, resetNodeCounter, modules, pipelineVariables } from './stores.js';
import { dispatchRun, paramEditorAggregator } from './machines/root.js';
import type { PipelineJSON, CanvasNodeData, MethodDef } from './types.js';
import type { Node, Edge } from '@xyflow/svelte';
import type { BoundVariablesMap } from './compile.js';

// ---------- Canvas-level pipeline identity ----------
//
// The "what pipeline did the user just submit (or load) into THIS canvas?"
// source of truth.  Used by the running-block gate (D-10) — the gate is
// scoped to the current canvas's pipelineId, NOT to whatever historical
// run the user happens to be inspecting in RunDetailPanel.
//
// Population rules (review iter 1, root-cause fix):
//   - On submit success: written by the pipelineRun machine's
//     `submitting.onDone` to the new pipeline_id (= job_id from the
//     /api/workflow/run response).
//   - On Action 1 (Open pipeline in Canvas): callers set this to the
//     loaded pipeline's id immediately after `loadPipeline(json)`.
//   - On Action 2 (Open lineage in Canvas): callers clear to null —
//     the synthesized lineage is not yet a submitted pipeline.
//   - On Action 3 (Reference in Canvas / graft): unchanged (graft does
//     not change the canvas's pipeline identity).
//   - On Toolbar JSON upload, Clear, dispatchReset: cleared to null —
//     uploaded JSONs / fresh canvas have no submitted pipeline_id.
//
// The store lives here (next to the gate functions that read it) rather
// than in `historyStore.ts` because it is *canvas* state, not history
// state — historyStore.ts owns the history list, this file owns the
// canvas's authoring identity.
export const canvasPipelineId: Writable<string | null> = writable(null);

/**
 * Per-row binding markers from the most recent `loadPipeline()` call.
 * ValueList reads this on spawn so each row's `paramEditorActor` lands
 * in `bound` when its key is present. Cleared on every `loadPipeline`
 * invocation (set to the new pipeline's markers, or {} if none).
 *
 * Key shape matches `BoundVariablesMap`:
 *   - base:    `${nodeId}::${paramName}`
 *   - variant: `${nodeId}::${paramName}::${variantName}`
 */
export const pendingBoundVariables: Writable<BoundVariablesMap> = writable({});
// The pure compile/parse functions live in `compile.ts` so Node-based
// regression tests (tests/test_canvas_compile_ts.py) can import them
// without pulling in the svelte-store runtime.
import {
  compilePipelineToJSON as _compilePipelineToJSON,
  parsePipelineJSON as _parsePipelineJSON,
  type AuthoringState as _AuthoringState,
} from './compile.js';

// ---------- Re-exports from the pure compile module ----------

// Preserve the historical `pipeline.ts` import surface used elsewhere in
// the canvas.  The implementations live in `./compile.ts` so a Node-based
// regression test can import them without the svelte-store runtime.
export type AuthoringState = _AuthoringState;
export const compilePipelineToJSON = _compilePipelineToJSON;
export const parsePipelineJSON = _parsePipelineJSON;

// ---------- Export (svelte-store glue) ----------

/**
 * Walk the paramEditorAggregator's children and collect any
 * `boundVariable` markers. Compile uses these to emit `{$var: name}`
 * refs in `node.params` / `param_sets[node][variant][param]`.
 *
 * The child id format (set by ValueList.svelte::rowAggregatorId) is:
 *   `${nodeId}::${paramName}${dirtyKeySuffix}::${rowId}`
 * where `rowId` is `'base'` for base rows and `v:${variantName}` for
 * variant rows. We translate to compile's key shape:
 *   - base:    `${nodeId}::${paramName}`
 *   - variant: `${nodeId}::${paramName}::${variantName}`
 */
function collectBoundVariables(): BoundVariablesMap {
  const out: BoundVariablesMap = {};
  // Aggregator children is `Record<string, ChildActor>` — actors stored
  // directly, not wrapped in `{ actor }`. The earlier `Map<...>` cast was
  // a bug: `.entries()` would have crashed at runtime, but no
  // production path called this with a non-empty registry until
  // incarnation 4 added round-trip + UI tests.
  const children = paramEditorAggregator.getSnapshot().context.children as
    | Record<string, { getSnapshot: () => { context: { boundVariable?: string | null } } }>
    | undefined;
  if (!children) return out;
  for (const [id, actor] of Object.entries(children)) {
    const ctx = actor.getSnapshot().context;
    const bv = ctx.boundVariable;
    if (!bv) continue;
    // id shape: `${nodeId}::${paramName}${dirtyKeySuffix}::${rowId}`.
    // Split on `::` from the right: rowId is the trailing segment.
    const idx = id.lastIndexOf('::');
    if (idx < 0) continue;
    const head = id.slice(0, idx);
    const rowId = id.slice(idx + 2);
    // `head` may carry a dirtyKeySuffix; split nodeId/paramName off the
    // first two segments. ValueList suffix shapes (e.g. per-sample tab)
    // contain `::` themselves, so reconstruct: nodeId = first segment,
    // paramName = second segment (suffix is appended to paramName, but
    // for round-trip we want the canonical paramName). We take
    // segment[0] as nodeId and segment[1] (without suffix) as paramName.
    const segs = head.split('::');
    if (segs.length < 2) continue;
    const nodeId = segs[0];
    // The paramName portion may have suffix appended. dirtyKeySuffix
    // for the per-sample-overrides tab carries `::sample::<name>` —
    // rather than parse all variants, use the simple convention that
    // segs[1] (without any further `::` decomposition) is paramName.
    const paramName = segs[1];
    if (rowId === 'base') {
      out[`${nodeId}::${paramName}`] = bv;
    } else if (rowId.startsWith('v:')) {
      const variantName = rowId.slice(2);
      out[`${nodeId}::${paramName}::${variantName}`] = bv;
    }
  }
  return out;
}

export function exportPipeline(): PipelineJSON {
  const $nodes = get(nodes);
  const $edges = get(edges);
  const $name = get(pipelineName);
  const $vars = get(pipelineVariables);
  const inputSelectorSamples = $nodes
    .filter(n => n.data.nodeType === 'input_selector')
    .flatMap(n => n.data.selectedSamples ?? []);
  const datasourceNode = $nodes.find(n => n.data.datasource);
  const samples = inputSelectorSamples.length > 0
    ? inputSelectorSamples
    : datasourceNode?.data.datasource ? [datasourceNode.data.datasource] : [];
  return compilePipelineToJSON({
    name: $name,
    nodes: $nodes,
    edges: $edges,
    samples,
    pipelineVariables: $vars,
    boundVariables: collectBoundVariables(),
  });
}

// ---------- Import / Load ----------

/** Look up a method's slot definitions from the modules store. */
function findMethodDef(moduleName: string, methodName: string): MethodDef | undefined {
  const $modules = get(modules);
  const mod = $modules.find(m => m.name === moduleName);
  return mod?.methods.find(m => m.name === methodName);
}

export function loadPipeline(pipeline: PipelineJSON): void {
  if (pipeline.name) pipelineName.set(pipeline.name);
  let maxId = 0;
  const {
    nodeVariants,
    nodeSampleOverrides,
    nodeSampleVariants,
    boundVariables: parsedBindings,
    pipelineVariables: parsedVars,
  } = parsePipelineJSON(pipeline);
  // Track 2 (ADR-017): hydrate the pipeline variables store and stash
  // per-row binding markers so freshly-spawned actors land in `bound`.
  pipelineVariables.set(parsedVars);
  pendingBoundVariables.set(parsedBindings);
  const newNodes: Node<CanvasNodeData>[] = pipeline.nodes.map(pn => {
    const numId = parseInt(pn.id.replace('node_', ''));
    if (!isNaN(numId) && numId > maxId) maxId = numId;
    const nodeType = pn.type || 'method';

    if (nodeType === 'input_selector') {
      return {
        id: pn.id,
        type: 'custom',
        position: pn.position ?? { x: Math.random() * 600, y: Math.random() * 400 },
        data: {
          label: 'Input Selector',
          method: '',
          module: '',
          color: '#1ABC9C',
          inputs: [],
          outputs: [{ name: 'output', type: 'csv' }],
          params: [],
          paramValues: {},
          runStatus: 'idle' as const,
          expanded: false,
          nodeType: 'input_selector' as const,
          selectedSamples: pn.samples ?? [],
          fanMode: ((pn as { fan_mode?: string }).fan_mode === 'in' ? 'in' : 'out') as 'in' | 'out',
          // Persist the per-selector keep-going toggle. Default true for
          // fan-out (per-sample independence), false for fan-in (single
          // bundled job — flag is a no-op). Missing value on the incoming
          // JSON is interpreted the same as "defaults to true for fan-out".
          keepGoing:
            (pn as { keep_going?: boolean }).keep_going !== undefined
              ? !!(pn as { keep_going?: boolean }).keep_going
              : ((pn as { fan_mode?: string }).fan_mode !== 'in'),
          inputCollapsed: false,
        },
      };
    } else if (nodeType === 'run_reference') {
      // Seed outputs with a placeholder single slot from the legacy
      // output_slot field so old pipelines render sensibly before the
      // Inspector re-fetches the run and populates all output_slots as
      // individual handles. New pipelines carry no output_slot; they
      // render with one placeholder until the run is loaded.
      const seedSlot = pn.output_slot || 'output';
      return {
        id: pn.id,
        type: 'custom',
        position: pn.position ?? { x: Math.random() * 600, y: Math.random() * 400 },
        data: {
          label: 'Run Reference',
          method: '',
          module: '',
          color: '#1ABC9C',
          inputs: [],
          outputs: [{ name: seedSlot, type: 'csv' }],
          params: [],
          paramValues: {},
          runStatus: 'idle' as const,
          expanded: false,
          nodeType: 'run_reference' as const,
          selectedRunId: pn.run_id,
          selectedOutputSlot: pn.output_slot,
        },
      };
    } else {
      // Method node — look up real slot definitions from modules store
      const methodDef = findMethodDef(pn.module ?? '', pn.method);
      return {
        id: pn.id,
        type: 'custom',
        position: pn.position ?? { x: Math.random() * 600, y: Math.random() * 400 },
        data: {
          label: pn.method || pn.id,
          method: pn.method,
          module: pn.module ?? '',
          color: methodDef?.color ?? '#2ecc71',
          inputs: methodDef?.inputs ?? [{ name: 'data', type: 'csv' }],
          outputs: methodDef?.outputs ?? [{ name: 'output', type: 'csv' }],
          params: methodDef?.params ?? [],
          paramValues: pn.params ?? {},
          runStatus: 'idle' as const,
          expanded: false,
          nodeType: 'method' as const,
          datasource: pipeline.samples?.[0],
          variants: nodeVariants[pn.id],
          sampleOverrides: nodeSampleOverrides[pn.id],
          sampleVariants: nodeSampleVariants[pn.id],
        },
      };
    }
  });
  const newEdges: Edge[] = pipeline.links.map((ln, i) => ({
    id: `e_${i}`,
    type: 'deletable',
    source: ln.source,
    target: ln.target,
    sourceHandle: ln.sourceHandle ?? null,
    targetHandle: ln.targetHandle ?? null,
  }));
  resetNodeCounter(maxId);
  nodes.set(newNodes);
  edges.set(newEdges);
  clearRunState();
}

// ---------- Load-in-Canvas gates (Actions 1, 2, 3) ----------

import type { Node as XYNode } from '@xyflow/svelte';
import { runningPipelineId } from './historyStore.js';
import { confirmDialogState } from './uiState.js';

/**
 * Confirm-on-dirty gate (D-4): if the canvas has any nodes, ask the user
 * to confirm before replacing. SPEC §"Confirm-on-dirty dialog" defines
 * the copy verbatim. Resolves to true to proceed, false to cancel.
 *
 * Drives the styled ``ConfirmDialog.svelte`` via the ``confirmDialogState``
 * singleton hosted in ``App.svelte``. The promise resolves when the user
 * clicks Cancel or Discard-and-load.
 */
export function confirmReplaceIfDirty(targetName: string): Promise<boolean> {
  const $nodes = get(nodes);
  if ($nodes.length === 0) return Promise.resolve(true);
  const $name = get(pipelineName);
  return new Promise<boolean>(resolve => {
    confirmDialogState.set({
      variant: 'dirty-confirm',
      currentName: $name || 'your canvas',
      targetName,
      resolve: (proceed: boolean) => {
        confirmDialogState.set(null);
        resolve(proceed);
      },
    });
  });
}

/**
 * Running-pipeline block gate (D-10): if the canvas's current pipeline
 * has any running/pending runs, refuse the replace/graft and show the
 * SPEC's Cancel-only block dialog.
 *
 * Returns true if BLOCKED (caller must abort), false if OK to proceed.
 *
 * Caller passes the current canvas's pipelineId (or null when the canvas
 * has never been submitted). The hook checks ``runningPipelineId`` from
 * historyStore.
 */
export function checkRunningBlock(currentPipelineId: string | null): Promise<boolean> {
  if (!currentPipelineId) return Promise.resolve(false);
  const blocked: string | null = runningPipelineId(currentPipelineId);
  if (!blocked) return Promise.resolve(false);
  // Show the SPEC's Cancel-only block dialog and resolve to true (BLOCKED)
  // once the user dismisses it.
  return new Promise<boolean>(resolve => {
    confirmDialogState.set({
      variant: 'running-block',
      runningPipelineLabel: blocked,
      resolve: () => {
        confirmDialogState.set(null);
        resolve(true);
      },
    });
  });
}

/**
 * Action 3: graft a ``run_reference`` node onto the current canvas at
 * right-of-selected (or origin if no selection). Does NOT call
 * ``loadPipeline`` — preserves existing run state per the SPEC's "graft
 * vs replace" semantics.
 *
 * Returns the new node's id so the caller (RunDetailPanel) can wire the
 * GraftToast's "Jump to node" action.
 */
export function graftRunReference(runId: string, opts?: { method?: string }): string {
  const $nodes = get(nodes);
  const selected = $nodes.find(n => n.selected);
  const basePos = selected?.position ?? { x: 80, y: 80 };
  // Right-of-selected with a small horizontal offset (SPEC OQ-1 resolution).
  const position = { x: basePos.x + 220, y: basePos.y };

  const newId = `node_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const newNode: XYNode<CanvasNodeData> = {
    id: newId,
    type: 'custom',
    position,
    data: {
      label: 'Run Reference',
      method: opts?.method ?? '',
      module: '',
      color: '#1ABC9C',
      inputs: [],
      outputs: [{ name: 'output', type: 'csv' }],
      params: [],
      paramValues: {},
      runStatus: 'idle',
      expanded: false,
      nodeType: 'run_reference',
      selectedRunId: runId,
    },
  };

  nodes.update(ns => [...ns, newNode]);
  return newId;
}

// ---------- Run (thin façade — delegates to pipelineRunActor) ----------

/**
 * Thin façade kept for backwards-compatibility with `DevToolbar` and any
 * other call site that wants a single function to kick off a run. The
 * actual lifecycle (validate, submit, poll, fan-out events to per-node
 * actors) lives in `machines/` — see `services.ts::submitPipeline` and
 * `services.ts::pollNodeStatus`.
 */
export function runPipeline(): void {
  const pipeline = exportPipeline();
  dispatchRun(pipeline);
}
