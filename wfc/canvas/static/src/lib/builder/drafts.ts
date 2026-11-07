/**
 * Drafts on the Builder canvas: which nodes run in draft mode, the wording
 * every view uses for them, and the folder check made when a draft node is
 * dropped and before Run or Validate, which gives each draft node the
 * contract its `method.yaml` declares now.
 *
 * A draft node runs its method's draft folder; every node downstream of one
 * runs as a draft-mode step, owned by the nearest draft upstream (the first
 * in node order where two drafts meet). Nothing here decides what runs: the
 * server reads every draft again when Run or Validate posts the document and
 * refuses what cannot run, naming the node (`nodeRefusals.ts` shows it).
 */
import { writable, get } from 'svelte/store';
import type { Edge, Node } from '@xyflow/svelte';
import type { CanvasNodeData, DraftDef } from '../shared/types.js';
import { modules, nodes, updateNodeData } from './stores.js';
import { checkDraftSnapshots, refreshPalette, type SnapshotCheckEntry } from './api.js';
import { isDraftNode, nodeFromDragPayload, type NodePosition } from './nodeData.js';

// ---------- Wording (SOP canvas-colours, Drafts) ----------

export const REVIEW_IN_REGISTRY = 'Review in Registry ↗';
export const DRAFT_MODE_PILL = 'Only the latest draft results are kept; the next run of this draft replaces them.';
export const DRAFT_NODE_HELP = "Draft mode: this step runs your method's draft. Its latest results are kept so you can check them, but they aren't recorded: the next run of this draft replaces them. Register the method to record future runs.";

/** A downstream node's "?" text, naming the draft whose results it uses. */
export function downstreamHelp(draft: string): string {
  return `Draft mode: this step uses results from the ${draft} draft. Its latest results are kept but not recorded; the next run of this draft replaces them. Once that draft is registered, future runs are recorded.`;
}

/** The Inspector's banner on a draft node with `after` draft-mode steps after it. */
export function draftBanner(after: number): string {
  const these = after === 0 ? 'this step' : `this step and the ${after} after it`;
  return `Draft mode: the latest results from ${these} are kept but not recorded; the next run of this draft replaces them. Register the method in the Registry to record future runs.`;
}

/** The footer suffix of a draft-mode node with a status. */
export const LATEST_KEPT = '· latest kept';

// ---------- Draft mode ----------

/** Where one node stands in draft mode. */
export interface DraftModeEntry {
  /** True on the draft node itself; false on a downstream draft-mode node. */
  isDraft: boolean;
  /** The id of the draft node that owns this step. */
  ownerId: string;
  /** The owning draft's method name, for the downstream wording. */
  owner: string;
}

/**
 * Every node in draft mode: the draft nodes and everything downstream of
 * them. A downstream node belongs to the nearest draft upstream; where two
 * drafts meet, to the first in node order.
 *
 * Args:
 *     canvasNodes: The canvas nodes.
 *     canvasEdges: The canvas edges.
 *
 * Returns:
 *     A map from node id to its draft-mode entry; nodes not in draft mode
 *     are absent.
 */
export function draftModeOf(
  canvasNodes: Node<CanvasNodeData>[],
  canvasEdges: Edge[],
): Map<string, DraftModeEntry> {
  const out = new Map<string, DraftModeEntry>();
  const children = new Map<string, string[]>();
  for (const e of canvasEdges) {
    if (!children.has(e.source)) children.set(e.source, []);
    children.get(e.source)!.push(e.target);
  }
  // Breadth first from every draft at once, in node order: a node is
  // claimed by the draft that reaches it in the fewest steps.
  const queue: string[] = [];
  for (const n of canvasNodes) {
    if (!isDraftNode(n.data)) continue;
    out.set(n.id, { isDraft: true, ownerId: n.id, owner: n.data.method });
    queue.push(n.id);
  }
  while (queue.length > 0) {
    const id = queue.shift()!;
    const entry = out.get(id)!;
    for (const child of children.get(id) ?? []) {
      if (out.has(child)) continue;
      out.set(child, { isDraft: false, ownerId: entry.ownerId, owner: entry.owner });
      queue.push(child);
    }
  }
  return out;
}

/** How many draft-mode steps a draft owns besides its own. */
export function stepsAfter(mode: Map<string, DraftModeEntry>, draftId: string): number {
  let n = 0;
  for (const e of mode.values()) if (!e.isDraft && e.ownerId === draftId) n++;
  return n;
}

// ---------- The folder check ----------

/** The popup shown while a check takes longer than `POPUP_DELAY_MS`. */
export const draftBusy = writable<string | null>(null);
export const POPUP_DELAY_MS = 300;
export const CREATING_DRAFT_NODE = 'Creating draft node…';

async function withPopup<T>(text: string, work: () => Promise<T>): Promise<T> {
  const timer = setTimeout(() => draftBusy.set(text), POPUP_DELAY_MS);
  try {
    return await work();
  } finally {
    clearTimeout(timer);
    draftBusy.set(null);
  }
}

/** What a check entry says of a folder that cannot run, or ``''`` for a current one. */
function entryMessage(entry: SnapshotCheckEntry): string {
  if (entry.state === 'current') return '';
  if (entry.message) return entry.message;
  if (entry.state === 'missing') return `${entry.method}: this draft or its folder is gone.`;
  return `${entry.method}: this draft's Registration check fails.`;
}

/** Write a check's answer onto every canvas node of the draft it names. */
function stampNodes(entries: SnapshotCheckEntry[]): void {
  for (const n of get(nodes)) {
    if (!isDraftNode(n.data)) continue;
    const entry = entries.find(e => e.module === n.data.module && e.method === n.data.method);
    if (!entry) continue;
    updateNodeData(n.id, { draftState: entry.state, draftMessage: entryMessage(entry) || undefined });
  }
}

/** The palette's draft of one method, or ``undefined`` when it lists none. */
function paletteDraft(module: string, method: string): DraftDef | undefined {
  for (const mod of get(modules)) {
    if (mod.name !== module) continue;
    const found = [...mod.methods, ...(mod.drafts ?? [])].find(m => m.name === method && m.draft);
    if (found) return found.draft;
  }
  return undefined;
}

/**
 * Give every canvas node the palette's draft of its method: the ● DRAFT
 * entry of every node of the method, and the contract (ports and declared
 * params) of every node that runs the draft. The palette shows each draft's
 * `method.yaml` as the server reads it now. Parameter values, variants and
 * links are left as they are: one the new contract does not declare is the
 * server's to refuse, naming the node and the field.
 */
export function applyDraftContracts(): void {
  for (const n of get(nodes)) {
    if (n.data.nodeType && n.data.nodeType !== 'method') continue;
    const draft = paletteDraft(n.data.module, n.data.method);
    if (!draft) continue;
    const update: Partial<CanvasNodeData> = { draft };
    if (isDraftNode(n.data)) Object.assign(update, { inputs: draft.inputs, outputs: draft.outputs, params: draft.params });
    const now = { draft: n.data.draft, inputs: n.data.inputs, outputs: n.data.outputs, params: n.data.params };
    if (JSON.stringify({ ...now, ...update }) !== JSON.stringify(now)) updateNodeData(n.id, update);
  }
}

/**
 * Check the folders of the given drafts, stamp the answer on their nodes and,
 * when any draft's `method.yaml` changed or its folder cannot run, refetch
 * the palette; then every node takes its draft's contract from the palette.
 *
 * A failed request answers nothing: the server reads every draft again when
 * the document reaches it.
 *
 * Returns:
 *     The check's entries, or ``null`` when the request failed.
 */
async function checkAndStamp(
  drafts: { module: string; method: string }[],
  popup: string,
): Promise<SnapshotCheckEntry[] | null> {
  if (drafts.length === 0) return [];
  let entries: SnapshotCheckEntry[];
  try {
    entries = await withPopup(popup, () => checkDraftSnapshots(drafts));
  } catch {
    return null;
  }
  stampNodes(entries);
  if (entries.some(e => e.contractChanged || e.state !== 'current')) await refreshPalette();
  applyDraftContracts();
  return entries;
}

/**
 * Before Run or Validate: check every draft node's folder, so each node shows
 * the contract its draft's `method.yaml` declares now. Nothing is refused
 * here: Run and Validate post the document, and the server reads every
 * draft again and refuses what cannot run, naming the node.
 */
export async function refreshDrafts(): Promise<void> {
  const drafts = new Map<string, { module: string; method: string }>();
  for (const n of get(nodes)) {
    if (isDraftNode(n.data)) drafts.set(`${n.data.module}.${n.data.method}`, { module: n.data.module, method: n.data.method });
  }
  await checkAndStamp([...drafts.values()], 'Checking drafts…');
}

/**
 * Add a dropped palette item to the canvas. A draft is checked against its
 * folder first (the "Creating draft node…" popup shows if that takes over
 * 300 ms); a draft that is gone adds no node and says why.
 *
 * Args:
 *     id: The new node's id.
 *     position: Where it lands.
 *     payload: The palette's drag payload.
 *
 * Returns:
 *     Why no node was added, or why the added draft cannot run now (its
 *     Registration check), else ``''``.
 */
export async function dropPaletteItem(id: string, position: NodePosition, payload: unknown): Promise<string> {
  const node = nodeFromDragPayload(id, position, payload);
  if (!isDraftNode(node.data)) {
    nodes.update(n => [...n, node]);
    return '';
  }
  const ref = { module: node.data.module, method: node.data.method };
  const entries = await checkAndStamp([ref], CREATING_DRAFT_NODE);
  const entry = entries?.find(e => e.module === ref.module && e.method === ref.method);
  if (entry?.state === 'missing') return entryMessage(entry);
  const draft = paletteDraft(ref.module, ref.method);
  if (draft) Object.assign(node.data, { draft, inputs: draft.inputs, outputs: draft.outputs, params: draft.params });
  if (entry && entry.state !== 'current') {
    node.data.draftState = entry.state;
    node.data.draftMessage = entryMessage(entry);
  }
  nodes.update(n => [...n, node]);
  return entry ? entryMessage(entry) : '';
}
