/**
 * The Inspector's non-markup logic.
 *
 * What the Inspector panels compute or write without rendering it: the
 * per-sample override and variant edits, the orphan-override scan and strip
 * behind the Input Selector's confirmation dialog, the parameter constraint
 * hint, and the `options_from` lookup that calls `./api.ts`.
 *
 * Every function takes the selected node (or the store snapshots a
 * derivation already read) as an argument rather than closing over a
 * component's state. Nothing here is reactive: the `$derived` and `$effect`
 * that drive the options lookup live in `MethodPanel.svelte`, where their
 * dependencies are read.
 */
import { get } from 'svelte/store';
import type { Edge, Node } from '@xyflow/svelte';
import { nodes, updateNodeData } from '../builder/stores.js';
import { compilePipelineToJSON, isVarRef } from '../builder/compile.js';
import { collectBoundVariables, pendingBoundVariables } from '../builder/pipeline.js';
import { isDraftNode } from '../builder/nodeData.js';
import type {
  CanvasNodeData, ParamDef, PipelineJSON, PipelineVariables,
} from '../shared/types.js';
import {
  fetchOutputOptions, RUN_REFERENCE_METHOD, type OutputOptionsRequest,
} from './api.js';
import {
  optionsFromInputs, type OutputOptions, type ParamOptions, type UpstreamOptions,
} from './options.js';

/** A canvas node as the Inspector receives it. */
export type CanvasNode = Node<CanvasNodeData>;

/** One sample's orphaned-override tally, as the confirmation dialog lists it. */
export type OrphanInfo = { sample: string; nodeCount: number; nodeIds: string[] };

/**
 * An accept the Input Selector could not commit on its own.
 *
 * The panel raises this when removing samples would orphan overrides; the
 * Inspector renders the confirmation dialog and resolves it. `revert` returns
 * the panel's staged selection to the node's committed one, which is what
 * cancelling the removal means.
 */
export type OrphanRequest = {
  info: OrphanInfo[];
  pending: string[];
  revert: () => void;
};

// ── per-parameter edits ───────────────────────────────────────────────────

/**
 * Write a parameter's sweep variants onto the node.
 *
 * Args:
 *     node: The selected node.
 *     paramName: The parameter whose variants changed.
 *     newVariants: The new variant map; empty removes the parameter's entry.
 */
export function updateVariants(
  node: CanvasNode,
  paramName: string,
  newVariants: Record<string, unknown>
): void {
  const current = node.data.variants ?? {};
  const next = { ...current };
  if (Object.keys(newVariants).length === 0) {
    delete next[paramName];
  } else {
    next[paramName] = newVariants;
  }
  updateNodeData(node.id, { variants: next });
}

/**
 * Write one sample's override of one parameter.
 *
 * An empty value clears the override, and clearing the last override for a
 * sample drops the sample's entry entirely, so an untouched sample leaves no
 * empty map behind.
 *
 * Args:
 *     node: The selected node.
 *     sample: The sample the override applies to.
 *     paramName: The overridden parameter.
 *     value: The override; ``undefined``, ``''`` or ``null`` clears it.
 */
export function updateSampleOverride(
  node: CanvasNode,
  sample: string,
  paramName: string,
  value: unknown
): void {
  const current = node.data.sampleOverrides ?? {};
  const sampleMap = { ...(current[sample] ?? {}) };
  if (value === undefined || value === '' || value === null) {
    delete sampleMap[paramName];
  } else {
    sampleMap[paramName] = value;
  }
  const next = { ...current };
  if (Object.keys(sampleMap).length === 0) {
    delete next[sample];
  } else {
    next[sample] = sampleMap;
  }
  updateNodeData(node.id, { sampleOverrides: next });
}

/**
 * Write one sample's sweep variants for one parameter.
 *
 * Args:
 *     node: The selected node.
 *     sample: The sample the variants apply to.
 *     paramName: The parameter being swept.
 *     newVariants: The new variant map; empty removes the parameter's entry.
 */
export function updateSampleVariants(
  node: CanvasNode,
  sample: string,
  paramName: string,
  newVariants: Record<string, unknown>
): void {
  const current = node.data.sampleVariants ?? {};
  const sampleMap = { ...(current[sample] ?? {}) };
  if (Object.keys(newVariants).length === 0) {
    delete sampleMap[paramName];
  } else {
    sampleMap[paramName] = newVariants;
  }
  const next = { ...current };
  if (Object.keys(sampleMap).length === 0) {
    delete next[sample];
  } else {
    next[sample] = sampleMap;
  }
  updateNodeData(node.id, { sampleVariants: next });
}

/**
 * Drop both the override and the variants a sample holds for one parameter.
 *
 * Args:
 *     node: The selected node.
 *     sample: The sample to clear.
 *     paramName: The parameter to clear.
 */
export function clearSampleOverrideAndVariants(
  node: CanvasNode,
  sample: string,
  paramName: string
): void {
  updateSampleOverride(node, sample, paramName, '');
  updateSampleVariants(node, sample, paramName, {});
}

/**
 * Render a parameter's constraints as the hint shown beside its name.
 *
 * Args:
 *     param: The declared parameter.
 *
 * Returns:
 *     A range, a bound, or ``''`` when the parameter is unconstrained.
 */
export function constraintHint(param: ParamDef): string {
  if (!param.constraints) return '';
  const c = param.constraints;
  if (c.min !== undefined && c.max !== undefined) return `${c.min}–${c.max}`;
  if (c.min !== undefined) return `≥ ${c.min}`;
  if (c.max !== undefined) return `≤ ${c.max}`;
  return '';
}

// ── orphaned overrides ────────────────────────────────────────────────────

/**
 * Find the nodes that would be left holding overrides for removed samples.
 *
 * Scans every canvas node's `sampleOverrides` and `sampleVariants` for
 * entries keyed on any of `removedSamples`.
 *
 * Args:
 *     removedSamples: The samples about to leave the Input Selector.
 *
 * Returns:
 *     One entry per sample that any node still overrides; samples nothing
 *     references are omitted, so an empty result means the removal is safe.
 */
export function findOrphanOverrides(removedSamples: string[]): OrphanInfo[] {
  const all = get(nodes);
  const result: OrphanInfo[] = [];
  for (const sample of removedSamples) {
    const nodeIds: string[] = [];
    for (const n of all) {
      const d = n.data as CanvasNodeData;
      const override = (d.sampleOverrides ?? {})[sample];
      const svar = (d.sampleVariants ?? {})[sample];
      const hasOverride = !!(override && Object.keys(override).length > 0);
      const hasVariants = !!(
        svar && Object.values(svar).some(vd => Object.keys(vd ?? {}).length > 0)
      );
      if (hasOverride || hasVariants) {
        nodeIds.push(n.id);
      }
    }
    if (nodeIds.length > 0) {
      result.push({ sample, nodeCount: nodeIds.length, nodeIds });
    }
  }
  return result;
}

/**
 * Strip orphaned overrides and per-sample variants across every canvas node.
 *
 * The "Yes, discard" half of the confirmation dialog. Mutates each node's
 * maps in place and re-emits the array, so SvelteFlow keeps
 * its per-node bookkeeping.
 *
 * Args:
 *     removedSamples: The samples whose overrides should be discarded.
 */
export function stripOrphanOverrides(removedSamples: string[]): void {
  const removedSet = new Set(removedSamples);
  nodes.update(allNodes => {
    for (const n of allNodes) {
      const d = n.data as CanvasNodeData;
      const overrides = d.sampleOverrides;
      if (overrides) {
        let changed = false;
        for (const key of Object.keys(overrides)) {
          if (removedSet.has(key)) {
            delete overrides[key];
            changed = true;
          }
        }
        if (changed) d.sampleOverrides = { ...overrides };
      }
      const svars = d.sampleVariants;
      if (svars) {
        let changed = false;
        for (const key of Object.keys(svars)) {
          if (removedSet.has(key)) {
            delete svars[key];
            changed = true;
          }
        }
        if (changed) d.sampleVariants = { ...svars };
      }
    }
    return [...allNodes];
  });
}

// ── options_from lookup ───────────────────────────────────────────────────

/** One upstream the lookup asks about, and the request it sends (none: not asked). */
export interface UpstreamLookup extends Omit<UpstreamOptions, 'options'> {
  /** The route's path method: `module.method`, or the Run Reference placeholder. */
  methodFull: string;
  request: OutputOptionsRequest | null;
}

/** One `options_from` param's lookup: the inputs it lists and every upstream wired to them. */
export interface ParamLookup {
  inputs: string[];
  /** In `options_from` order, then edge order within an input. */
  upstreams: UpstreamLookup[];
}

/** Every `options_from` param's lookup on one node, by param name. */
export type OptionsLookupPlan = Record<string, ParamLookup>;

/**
 * Compile the canvas the way a run would, for the lookup to read the
 * upstreams' value sets from.
 *
 * The rows bound to a pipeline variable are the ones the param editors
 * hold, plus those a loaded document marked that no editor has picked up
 * yet. The stores are passed in so the caller's `$derived` tracks them.
 *
 * Args:
 *     nodeList: The canvas nodes.
 *     edgeList: The canvas edges.
 *     vars: The pipeline variables.
 *
 * Returns:
 *     The compiled document: each node's base params and its `param_sets`.
 */
export function lookupPipeline(
  nodeList: CanvasNode[],
  edgeList: Edge[],
  vars: PipelineVariables,
): PipelineJSON {
  return compilePipelineToJSON({
    name: '',
    nodes: nodeList,
    edges: edgeList,
    samples: [],
    pipelineVariables: vars,
    boundVariables: { ...get(pendingBoundVariables), ...collectBoundVariables() },
  });
}

/**
 * Every value set an upstream node would run with: its base values first,
 * then each sweep variant's and per-sample override's, in the order compile
 * emits them. A pipeline variable's ref is replaced by the variable's value;
 * repeated sets are sent once.
 *
 * Args:
 *     pipeline: The compiled canvas (`lookupPipeline`).
 *     nodeId: The upstream node.
 *
 * Returns:
 *     The value sets; empty for a node the document does not hold.
 */
export function upstreamParamSets(
  pipeline: PipelineJSON,
  nodeId: string,
): Record<string, unknown>[] {
  const node = pipeline.nodes.find(n => n.id === nodeId);
  if (!node) return [];
  const vars = pipeline.variables ?? {};
  const resolve = (set: Record<string, unknown>): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(set)) {
      if (!isVarRef(v)) { out[k] = v; continue; }
      const value = vars[v.$var]?.value;
      if (value !== undefined && value !== null && value !== '') out[k] = value;
    }
    return out;
  };
  const sets = [node.params ?? {}, ...Object.values(pipeline.param_sets?.[nodeId] ?? {})];
  const seen = new Set<string>();
  const result: Record<string, unknown>[] = [];
  for (const set of sets.map(resolve)) {
    const fp = JSON.stringify(Object.keys(set).sort().map(k => [k, set[k]]));
    if (seen.has(fp)) continue;
    seen.add(fp);
    result.push(set);
  }
  return result;
}

/** The lookup for one edge into an `options_from` input. */
function upstreamLookup(
  input: string,
  edge: Edge,
  upstream: CanvasNode,
  pipeline: PipelineJSON,
): UpstreamLookup {
  const ud = upstream.data;
  const slot = edge.sourceHandle ?? '';
  const base = {
    input, nodeId: upstream.id, slot,
    nodeLabel: ud.label || ud.method || upstream.id,
    module: '', method: '',
  };
  if (ud.nodeType === 'run_reference') {
    const runId = ud.selectedRunId;
    return {
      ...base, kind: 'run_reference', runId, methodFull: RUN_REFERENCE_METHOD,
      request: runId ? { slot, run_id: runId } : null,
    };
  }
  if ((ud.nodeType && ud.nodeType !== 'method') || !ud.module || !ud.method) {
    return { ...base, kind: 'system', methodFull: '', request: null };
  }
  const method = { kind: 'method' as const, module: ud.module, method: ud.method,
    methodFull: `${ud.module}.${ud.method}` };
  // A draft (pinned to the draft, or a method with no registered version
  // yet) is not asked: the route answers registered versions only, so its
  // options appear once it is registered. The draft-only test reads
  // `versions` and `draft` off the node together, so it relies on
  // `methodNodeData` (builder/nodeData.ts) copying both from the method:
  // node data carrying `draft` without `versions` would read as draft-only
  // here and never be asked.
  if (isDraftNode(ud) || (!ud.versions?.length && !!ud.draft)) {
    return { ...base, ...method, draft: true, request: null };
  }
  const request: OutputOptionsRequest = { slot, param_sets: upstreamParamSets(pipeline, upstream.id) };
  // A pinned upstream offers its own version's options.
  if (ud.version) request.version = ud.version;
  return { ...base, ...method, request };
}

/**
 * Plan the lookup for every `options_from` param on a node.
 *
 * For each such param, every input it lists in order, and every edge into
 * that input in edge order, names one upstream: a method upstream is asked
 * with its pin and its value sets, a Run Reference with its run id, and a
 * system node, a run reference with no run, or a draft method (marked
 * `draft`) is listed but not asked.
 *
 * Args:
 *     node: The selected node.
 *     data: The selected node's data.
 *     edgeList: The canvas edges.
 *     nodeList: The canvas nodes.
 *     pipeline: The compiled canvas (`lookupPipeline`).
 *
 * Returns:
 *     One entry per `options_from` param; empty for a non-method node.
 */
export function optionsLookupPlan(
  node: CanvasNode | null | undefined,
  data: CanvasNodeData | undefined,
  edgeList: Edge[],
  nodeList: CanvasNode[],
  pipeline: PipelineJSON,
): OptionsLookupPlan {
  if (!node || data?.nodeType !== 'method') return {};
  const plan: OptionsLookupPlan = {};
  for (const p of data.params ?? []) {
    const inputs = optionsFromInputs(p.options_from);
    if (inputs.length === 0) continue;
    const upstreams: UpstreamLookup[] = [];
    for (const input of inputs) {
      for (const edge of edgeList) {
        if (edge.target !== node.id || edge.targetHandle !== input) continue;
        const upstream = nodeList.find(n => n.id === edge.source);
        if (upstream) upstreams.push(upstreamLookup(input, edge, upstream, pipeline));
      }
    }
    plan[p.name] = { inputs, upstreams };
  }
  return plan;
}

/**
 * Fingerprint a lookup plan: the wiring and every request it would send.
 *
 * It changes when an edge into a listed input, an upstream's values,
 * variants, overrides or pin, or a referenced run changes, and not when the
 * selected node's own values do.
 *
 * Args:
 *     plan: The node's lookup plan.
 *
 * Returns:
 *     A stable key, or ``''`` when the node has no `options_from` param.
 */
export function optionsLookupKey(plan: OptionsLookupPlan): string {
  const entries = Object.entries(plan);
  if (entries.length === 0) return '';
  return JSON.stringify(entries.map(([name, l]) => [
    name, l.inputs, l.upstreams.map(u => [u.input, u.nodeId, u.slot, u.methodFull, u.request]),
  ]));
}

/**
 * Send a plan's requests and pair each upstream with its answer.
 *
 * A request two upstreams share is sent once. A failed lookup, or an
 * upstream not asked, answers ``null``.
 *
 * Args:
 *     plan: The node's lookup plan.
 *
 * Returns:
 *     Each param's inputs and its upstreams with their options.
 */
export async function resolveOptionsLookup(
  plan: OptionsLookupPlan,
): Promise<Record<string, ParamOptions>> {
  const asked = new Map<string, Promise<OutputOptions | null>>();
  const ask = (u: UpstreamLookup): Promise<OutputOptions | null> => {
    if (!u.request) return Promise.resolve(null);
    const key = JSON.stringify([u.methodFull, u.request]);
    let pending = asked.get(key);
    if (!pending) {
      pending = fetchOutputOptions(u.methodFull, u.request);
      asked.set(key, pending);
    }
    return pending;
  };
  const result: Record<string, ParamOptions> = {};
  for (const [name, lookup] of Object.entries(plan)) {
    const upstreams = await Promise.all(lookup.upstreams.map(async u => {
      const { methodFull: _path, request: _request, ...upstream } = u;
      return { ...upstream, options: await ask(u) };
    }));
    result[name] = { inputs: lookup.inputs, upstreams };
  }
  return result;
}

