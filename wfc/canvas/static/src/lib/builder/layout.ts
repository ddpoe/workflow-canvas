/**
 * Where a loaded node goes when its document gives no position.
 *
 * A document that places its nodes — anything the canvas saved — loads where
 * it was put. One that does not — a synthesized lineage, a hand-written
 * pipeline — is laid out left to right in pipeline order: each node one
 * column right of its deepest parent, the nodes of a column stacked in
 * document order. Pure: takes the document's nodes and links, returns
 * positions, reads no store.
 */

/** Horizontal distance between columns, wide enough for an expanded node. */
export const COLUMN_GAP = 320;
/** Vertical distance between the nodes of one column. */
export const ROW_GAP = 200;
const ORIGIN = { x: 40, y: 40 };

export interface LayoutNode {
  id: string;
  position?: { x: number; y: number };
}

export interface LayoutLink {
  source: string;
  target: string;
}

/**
 * Positions for the nodes that have none, by depth.
 *
 * A node's depth is the length of the longest link path reaching it, so a
 * node with two parents sits one column right of the deeper one; a node no
 * link reaches is in the first column. Depth counts every node, placed or
 * not, so an unplaced node still lands to the right of a placed parent's
 * column. A cycle (never produced by a valid pipeline) stops deepening after
 * one pass per node rather than looping.
 *
 * Args:
 *     nodes: The document's nodes, in document order.
 *     links: The document's links.
 *
 * Returns:
 *     A position per node id that had no position; placed nodes are absent.
 */
export function layoutByDepth(
  nodes: LayoutNode[],
  links: LayoutLink[],
): Record<string, { x: number; y: number }> {
  const ids = new Set(nodes.map(n => n.id));
  const depth: Record<string, number> = {};
  for (const n of nodes) depth[n.id] = 0;
  // Longest path by relaxation: at most one pass per node settles any DAG.
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const l of links) {
      if (!ids.has(l.source) || !ids.has(l.target)) continue;
      if (depth[l.target] < depth[l.source] + 1) {
        depth[l.target] = depth[l.source] + 1;
        changed = true;
      }
    }
    if (!changed) break;
  }

  const out: Record<string, { x: number; y: number }> = {};
  const rowsUsed: Record<number, number> = {};
  for (const n of nodes) {
    if (n.position) continue;
    const col = depth[n.id];
    const row = rowsUsed[col] ?? 0;
    rowsUsed[col] = row + 1;
    out[n.id] = { x: ORIGIN.x + col * COLUMN_GAP, y: ORIGIN.y + row * ROW_GAP };
  }
  return out;
}
