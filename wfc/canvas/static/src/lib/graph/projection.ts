/**
 * The runs-preview projection — a pure preview of the engine's schedule.
 *
 * Which method nodes collapse, which samples they bundle, which
 * (node, sample, variant) rows the engine will schedule and which of those
 * rows reuse another row's output are questions the Python package
 * `wfc.graph` owns (`propagate_collapse`, `resolve_variant_model`,
 * `expand_step_combos`, `mark_reused`); this module previews its answers
 * over the sparse canvas document so the preview can render before
 * submission. The shared shape corpus under `tests/shapes/graph/projection/`
 * is read by both test runners and keeps the two in agreement by execution.
 */
import { COLLAPSED_SAMPLE } from '../shared/types.js';

/** The slice of a canvas document node the projection reads. */
export interface ProjectionNode {
  id: string;
  type?: string | null;
  method?: string;
  params?: Record<string, unknown>;
  fan_mode?: string;
  samples?: string[];
}

export interface ProjectionLink {
  source: string;
  target: string;
}

/** The slice of the sparse document the projection reads. */
export interface ProjectionDocument {
  nodes: ProjectionNode[];
  links?: ProjectionLink[];
  samples?: string[];
  param_sets?: Record<string, Record<string, Record<string, unknown>>>;
  explicit_combos?: Array<{ sample: string; variant: string }>;
}

/** One row the engine will schedule. */
export interface ProjectedRun {
  key: string;
  nodeId: string;
  method: string;
  sample: string;
  variant: string;
  params: Record<string, unknown>;
  /** For collapsed rows, the sample list bundled into the one run. */
  bundledSamples?: string[];
  /**
   * True when an earlier row of this schedule produces the identical output
   * (same resolved params and same upstream params under that variant name).
   */
  reused: boolean;
}

/**
 * The resolved variant model: every method node's table padded to the shared
 * axis. Mirrors the package's `VariantModel`.
 */
export interface VariantModel {
  tables: Record<string, Record<string, Record<string, unknown>>>;
  axis: string[];
}

function isMethodNode(n: ProjectionNode): boolean {
  return !n.type || n.type === 'method';
}

function isFanInSelector(n: ProjectionNode): boolean {
  return n.type === 'input_selector' && n.fan_mode === 'in';
}

/**
 * Method nodes whose sample axis collapses to the collapsed-sample sentinel
 * because they sit downstream of a fan-in selector. Collapse is contagious:
 * everything reachable from a fan-in selector runs once per variant.
 */
export function collapsedNodeIds(doc: ProjectionDocument): Set<string> {
  const fanInSelectors = new Set(doc.nodes.filter(isFanInSelector).map(n => n.id));
  if (fanInSelectors.size === 0) return new Set<string>();

  const adj: Record<string, string[]> = {};
  for (const link of doc.links ?? []) {
    (adj[link.source] ??= []).push(link.target);
  }
  const visited = new Set<string>();
  const queue = [...fanInSelectors];
  while (queue.length) {
    const src = queue.shift()!;
    for (const tgt of adj[src] ?? []) {
      if (!visited.has(tgt)) {
        visited.add(tgt);
        queue.push(tgt);
      }
    }
  }
  const methodIds = new Set(doc.nodes.filter(isMethodNode).map(n => n.id));
  const result = new Set<string>();
  for (const id of visited) if (methodIds.has(id)) result.add(id);
  return result;
}

/**
 * The sample list bundled into every collapsed run, taken from the first
 * fan-in selector in the document — the same answer as the package's
 * per-consumer bundle while one selector feeds the chain.
 */
export function collapsedBundledSamples(doc: ProjectionDocument): string[] {
  for (const n of doc.nodes) {
    if (isFanInSelector(n) && Array.isArray(n.samples)) return n.samples.map(String);
  }
  return [];
}

/**
 * Resolve the variant model the engine schedules from (the package's
 * `resolve_variant_model`): each method node's table is its `param_sets`
 * entry by node id, else by method name, else `{default: params}`; the axis
 * is the sorted union of every table's names (`['default']` when no node
 * declares any — `default` is on the axis only through a node with no
 * table, so a pipeline where every node is swept has no box run); every
 * table is padded so each axis name maps to the node's base params where
 * the node declares no variant of that name.
 */
function resolveVariantModel(doc: ProjectionDocument): VariantModel {
  const paramSets = doc.param_sets ?? {};
  const methods = doc.nodes.filter(isMethodNode);
  const tables: VariantModel['tables'] = {};
  for (const node of methods) {
    const declared = paramSets[node.id]
      ?? (node.method ? paramSets[node.method] : undefined)
      ?? { default: node.params ?? {} };
    tables[node.id] = { ...declared };
  }

  const names = new Set<string>();
  for (const table of Object.values(tables)) {
    for (const name of Object.keys(table)) names.add(name);
  }
  const axis = names.size > 0 ? [...names].sort() : ['default'];

  for (const node of methods) {
    for (const name of axis) {
      if (!(name in tables[node.id])) tables[node.id][name] = node.params ?? {};
    }
  }
  return { tables, axis };
}

/** JSON with object keys sorted at every level, so equal values print equal. */
function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    return `{${keys.map(k => `${JSON.stringify(k)}:${stableJson(obj[k])}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/**
 * The method nodes upstream of `nodeId` (itself included), reached backwards
 * over the document's links through any node — the package's transitive
 * `depends_on` closure, which only ever names method steps.
 */
function upstreamMethods(
  nodeId: string,
  incoming: Record<string, string[]>,
  methodIds: Set<string>,
): string[] {
  const seen = new Set<string>();
  const frontier = [nodeId];
  while (frontier.length) {
    const nid = frontier.pop()!;
    if (seen.has(nid)) continue;
    seen.add(nid);
    frontier.push(...(incoming[nid] ?? []));
  }
  return [...seen].filter(id => methodIds.has(id)).sort();
}

/**
 * Project the run matrix: one row per (method node, sample, variant) the
 * engine will schedule, in node order, with the reuse mark.
 *
 * Cartesian mode: a collapsed node runs once per axis name at the
 * collapsed-sample sentinel; a per-sample node runs samples × axis.
 * Selective mode (`explicit_combos` present): every node runs the explicit
 * combinations, verbatim and in order. The engine refuses explicit
 * combinations on a pipeline with a collapsed node; the preview shows the
 * cartesian schedule in that case rather than nothing.
 *
 * Reuse (the package's `mark_reused`, value identity): a node's row under a
 * variant name is reused when its resolved params and every upstream method
 * node's resolved params under that name are identical to those under an
 * earlier scheduled name (first-appearance order, which is axis order in
 * cartesian mode). Names are compared only among the node's scheduled names.
 */
export function projectRuns(doc: ProjectionDocument): ProjectedRun[] {
  const samples = doc.samples ?? [];
  const explicit = doc.explicit_combos ?? [];
  const collapsed = collapsedNodeIds(doc);
  const bundle = collapsedBundledSamples(doc);
  const methods = doc.nodes.filter(isMethodNode);
  const methodIds = new Set(methods.map(n => n.id));
  const nodeById: Record<string, ProjectionNode> = {};
  for (const n of methods) nodeById[n.id] = n;
  const { tables, axis } = resolveVariantModel(doc);

  const incoming: Record<string, string[]> = {};
  for (const link of doc.links ?? []) {
    (incoming[link.target] ??= []).push(link.source);
  }

  const paramsFor = (nodeId: string, variant: string): Record<string, unknown> =>
    tables[nodeId]?.[variant] ?? nodeById[nodeId]?.params ?? {};

  // The schedule: every node's (sample, variant) combos, per node.
  const selective = explicit.length > 0 && collapsed.size === 0;
  const combos: Array<{ node: ProjectionNode; sample: string; variant: string }> = [];
  for (const node of methods) {
    if (selective) {
      for (const c of explicit) combos.push({ node, sample: c.sample, variant: c.variant });
    } else if (collapsed.has(node.id)) {
      for (const v of axis) combos.push({ node, sample: COLLAPSED_SAMPLE, variant: v });
    } else {
      for (const s of samples) for (const v of axis) combos.push({ node, sample: s, variant: v });
    }
  }

  // The reuse mark: per node, the scheduled names in first-appearance order;
  // the first name with a given chain signature is the real run.
  const scheduledNames: Record<string, string[]> = {};
  for (const c of combos) {
    const names = (scheduledNames[c.node.id] ??= []);
    if (!names.includes(c.variant)) names.push(c.variant);
  }
  const reusedByNodeName: Record<string, boolean> = {};
  for (const [nid, names] of Object.entries(scheduledNames)) {
    const chain = upstreamMethods(nid, incoming, methodIds);
    const firstBySignature: Record<string, string> = {};
    for (const name of names) {
      const sig = JSON.stringify(chain.map(id => [id, stableJson(paramsFor(id, name))]));
      reusedByNodeName[`${nid}::${name}`] = sig in firstBySignature;
      firstBySignature[sig] ??= name;
    }
  }

  return combos.map(({ node, sample, variant }) => ({
    key: `${node.id}::${sample}::${variant}`,
    nodeId: node.id,
    method: node.method ?? '',
    sample,
    variant,
    params: paramsFor(node.id, variant),
    ...(collapsed.has(node.id) && sample === COLLAPSED_SAMPLE ? { bundledSamples: bundle } : {}),
    reused: reusedByNodeName[`${node.id}::${variant}`] ?? false,
  }));
}
