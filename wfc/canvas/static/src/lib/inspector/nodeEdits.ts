/**
 * The Inspector's non-markup logic.
 *
 * What the Inspector panels compute or write without rendering it: the
 * per-sample override and variant edits, the orphan-override scan and strip
 * behind the Input Selector's confirmation dialog, the parameter constraint
 * hint, and the `column_of_input` lookup that calls `./api.ts`.
 *
 * Every function takes the selected node (or the store snapshots a
 * derivation already read) as an argument rather than closing over a
 * component's state. Nothing here is reactive: the `$derived` and `$effect`
 * that drive the column lookup live in `MethodPanel.svelte`, where their
 * dependencies are read.
 */
import { get } from 'svelte/store';
import type { Edge, Node } from '@xyflow/svelte';
import { nodes, edges, updateNodeData } from '../builder/stores.js';
import type { CanvasNodeData, ParamDef } from '../shared/types.js';
import {
  fetchRunReferenceOutputColumns,
  fetchMethodOutputColumns,
  type ColumnOptionsResp,
} from './api.js';

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

// ── column_of_input resolution ────────────────────────────────────────────

/**
 * Fingerprint the current "what needs column resolution?" set.
 *
 * Includes each upstream's identity and its current params, so a sweep on an
 * upstream's chmap re-resolves downstream. The stores are passed in rather
 * than read here: the caller reads them inside a `$derived`, which is what
 * makes the key recompute.
 *
 * Args:
 *     node: The selected node.
 *     data: The selected node's data.
 *     edgeList: The canvas edges.
 *     nodeList: The canvas nodes.
 *
 * Returns:
 *     A stable key, or ``''`` when nothing needs resolving.
 */
export function columnResolutionKey(
  node: CanvasNode | undefined,
  data: CanvasNodeData | undefined,
  edgeList: Edge[],
  nodeList: CanvasNode[]
): string {
  if (!node || data?.nodeType !== 'method') return '';
  const params = data?.params ?? [];
  const parts: string[] = [];
  for (const p of params) {
    if (!p.column_of_input || p.new_column) continue;
    const slot = p.column_of_input;
    const edge = edgeList.find(e => e.target === node.id && e.targetHandle === slot);
    if (!edge) { parts.push(`${p.name}:NONE`); continue; }
    const upstream = nodeList.find(n => n.id === edge.source);
    if (!upstream) { parts.push(`${p.name}:GONE`); continue; }
    const ud = upstream.data as CanvasNodeData;
    // Run-reference upstream → key by run_id.
    if (ud.nodeType === 'run_reference') {
      parts.push(`${p.name}:RR:${ud.selectedRunId ?? ''}:${edge.sourceHandle ?? ''}`);
      continue;
    }
    // Method upstream → key by module/method/source slot/params hash.
    const paramHash = JSON.stringify(ud.paramValues ?? {});
    parts.push(`${p.name}:M:${ud.module}/${ud.method}:${edge.sourceHandle ?? ''}:${paramHash}`);
  }
  return parts.join('|');
}

/**
 * Resolve one parameter's column options against its upstream node.
 *
 * Args:
 *     node: The selected node.
 *     p: The parameter carrying `column_of_input`.
 *
 * Returns:
 *     The options, or ``null`` for a failed lookup, no upstream or no
 *     contract — which the caller renders as a plain input.
 */
export async function resolveColumnsFor(
  node: CanvasNode,
  p: ParamDef
): Promise<ColumnOptionsResp | null> {
  if (!p.column_of_input || p.new_column) return null;
  const slot = p.column_of_input;
  const edge = get(edges).find(e => e.target === node.id && e.targetHandle === slot);
  if (!edge) return null;
  const upstream = get(nodes).find(n => n.id === edge.source);
  if (!upstream) return null;
  const ud = upstream.data as CanvasNodeData;
  const sourceSlot = edge.sourceHandle ?? '';

  if (ud.nodeType === 'run_reference') {
    if (!ud.selectedRunId) return null;
    return fetchRunReferenceOutputColumns(sourceSlot, ud.selectedRunId);
  }
  if (!ud.module || !ud.method) return null;
  return fetchMethodOutputColumns(
    `${ud.module}.${ud.method}`,
    sourceSlot,
    ud.paramValues ?? {}
  );
}

/**
 * Resolve every column-bound parameter on a node, in declaration order.
 *
 * Args:
 *     node: The selected node.
 *     targets: The parameters carrying `column_of_input`.
 *
 * Returns:
 *     One entry per target, `null` where the lookup did not resolve.
 */
export async function resolveColumnOptions(
  node: CanvasNode,
  targets: ParamDef[]
): Promise<Record<string, ColumnOptionsResp | null>> {
  const next: Record<string, ColumnOptionsResp | null> = {};
  for (const p of targets) {
    next[p.name] = await resolveColumnsFor(node, p);
  }
  return next;
}
