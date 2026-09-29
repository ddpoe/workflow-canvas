/**
 * Shared pipeline export and import logic.
 *
 * The run/polling lifecycle lives in `machines/`. This module holds the
 * export / load helpers, the Load-in-Canvas gates and thin re-exports of
 * the pure compile functions.
 */
import { get, writable, type Writable } from 'svelte/store';
import { nodes, edges, pipelineName, clearRunState, resetNodeCounter, modules, pipelineVariables } from './stores.js';
import { dispatchRun, paramEditorAggregator } from '../machines/root.js';
import { methodNode, inputSelectorNode, runReferenceNode } from './nodeData.js';
import { layoutByDepth } from './layout.js';
import type { PipelineJSON, CanvasNodeData, MethodDef } from '../shared/types.js';
import type { Node, Edge } from '@xyflow/svelte';
import type { BoundVariablesMap } from './compile.js';

// ---------- Canvas-level pipeline identity ----------
//
// The "what pipeline did the user just submit (or load) into THIS canvas?"
// source of truth.  Used by the running-block gate — the gate is
// scoped to the current canvas's pipelineId, NOT to whatever historical
// run the user happens to be inspecting in RunDetailPanel.
//
// Population rules:
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

// Callers elsewhere in the canvas import these from `pipeline.ts`.  The
// implementations live in `./compile.ts` so a Node-based regression test
// can import them without the svelte-store runtime.
export type AuthoringState = _AuthoringState;
export const compilePipelineToJSON = _compilePipelineToJSON;
export const parsePipelineJSON = _parsePipelineJSON;

// ---------- Export (svelte-store glue) ----------

/**
 * Walk the paramEditorAggregator's children and collect any
 * `boundVariable` markers. Compile uses these to emit `{$var: name}`
 * refs in `node.params` / `param_sets[node][variant][param]`.
 *
 * The child id format (set by inspector/rowActors.ts::aggregatorIdFor) is:
 *   `${nodeId}::${paramName}${dirtyKeySuffix}::${rowId}`
 * where `rowId` is `'base'` for base rows and `v:${variantName}` for
 * variant rows. We translate to compile's key shape:
 *   - base:    `${nodeId}::${paramName}`
 *   - variant: `${nodeId}::${paramName}::${variantName}`
 */
function collectBoundVariables(): BoundVariablesMap {
  const out: BoundVariablesMap = {};
  // Aggregator children is `Record<string, ChildActor>` — actors stored
  // directly, not wrapped in `{ actor }` — so iterate with `Object.entries`.
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
  // Hydrate the pipeline variables store and stash
  // per-row binding markers so freshly-spawned actors land in `bound`.
  pipelineVariables.set(parsedVars);
  pendingBoundVariables.set(parsedBindings);
  // A document that places no node (a synthesized lineage) is laid out by
  // depth; a placed node keeps the position the document gives it.
  const laidOut = layoutByDepth(pipeline.nodes, pipeline.links);
  const newNodes: Node<CanvasNodeData>[] = pipeline.nodes.map(pn => {
    const numId = parseInt(pn.id.replace('node_', ''));
    if (!isNaN(numId) && numId > maxId) maxId = numId;
    const nodeType = pn.type || 'method';
    const position = pn.position ?? laidOut[pn.id];

    if (nodeType === 'input_selector') {
      const fanMode = (pn as { fan_mode?: string }).fan_mode === 'in' ? 'in' : 'out';
      const declared = (pn as { keep_going?: boolean }).keep_going;
      return inputSelectorNode(pn.id, position, {
        selectedSamples: pn.samples ?? [],
        fanMode,
        // Only what the document states, so the toggle round-trips. A
        // document that names no `keep_going` loads a node with no flag,
        // exactly like a dropped one, and `compile.ts` derives it from the
        // fan mode (true for fan-out, false for fan-in, where the flag is a
        // no-op).
        keepGoing: declared !== undefined ? !!declared : undefined,
      });
    } else if (nodeType === 'run_reference') {
      // A reference node is the run it names; which outputs it offers comes
      // from the run. Seed one placeholder handle so the node renders before
      // the Inspector re-fetches the run and populates its output slots as
      // individual handles.
      return runReferenceNode(pn.id, position, pn.run_id, {
        outputs: [{ name: 'output', type: 'csv' }],
      });
    } else {
      // Method node — look up real slot definitions from the modules store,
      // falling back to one generic input and output when the method is not
      // in the registry.
      const methodDef = findMethodDef(pn.module ?? '', pn.method) ?? {
        name: pn.method,
        module: pn.module ?? '',
        inputs: [{ name: 'data', type: 'csv' }],
        outputs: [{ name: 'output', type: 'csv' }],
        params: [],
      };
      return methodNode(pn.id, position, methodDef, {
        label: pn.method || pn.id,
        paramValues: pn.params ?? {},
        datasource: pipeline.samples?.[0],
        variants: nodeVariants[pn.id],
        sampleOverrides: nodeSampleOverrides[pn.id],
        sampleVariants: nodeSampleVariants[pn.id],
      });
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
import { runningPipelineId } from '../history/historyStore.js';
import { confirmDialogState } from '../shared/uiState.js';

/**
 * Confirm-on-dirty gate: if the canvas has any nodes, ask the user
 * to confirm before replacing. Resolves to true to proceed, false to cancel.
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
 * Running-pipeline block gate: if the canvas's current pipeline
 * has any running/pending runs, refuse the replace/graft and show the
 * Cancel-only block dialog.
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
  // Show the Cancel-only block dialog and resolve to true (BLOCKED)
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
 * ``loadPipeline`` — a graft adds to the canvas rather than replacing it,
 * so existing run state is preserved.
 *
 * Returns the new node's id so the caller (RunDetailPanel) can wire the
 * GraftToast's "Jump to node" action.
 */
export function graftRunReference(runId: string): string {
  const $nodes = get(nodes);
  const selected = $nodes.find(n => n.selected);
  const basePos = selected?.position ?? { x: 80, y: 80 };
  // Right-of-selected with a small horizontal offset.
  const position = { x: basePos.x + 220, y: basePos.y };

  const newId = `node_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const newNode = runReferenceNode(newId, position, runId);

  nodes.update(ns => [...ns, newNode]);
  return newId;
}

// ---------- Run (thin façade — delegates to pipelineRunActor) ----------

/**
 * Thin façade for `DevToolbar` and any other call site that wants a
 * single function to kick off a run. The
 * actual lifecycle (validate, submit, poll, fan-out events to per-node
 * actors) lives in `machines/` — see `services.ts::submitPipeline` and
 * `services.ts::pollNodeStatus`.
 */
export function runPipeline(): void {
  const pipeline = exportPipeline();
  dispatchRun(pipeline);
}
