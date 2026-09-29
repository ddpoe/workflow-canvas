/**
 * Wiring legality — the connect-time preview of the server's structural
 * validation.
 *
 * The Python package `wfc.graph` is the one authority for every graph
 * question; this predicate previews one of its rules at drop time so the
 * canvas can refuse a link before submission. It is kept in agreement with
 * the package by the shared shape corpus under `tests/shapes/graph/legality/`,
 * which both test runners read.
 *
 * The rule: one link per input slot, unless every link on that slot — the
 * candidate included — comes from a run_reference (a reference fan-in, which
 * the engine delivers as N artifacts under the one slot name). The predicate
 * quantifies with *all*, not *any*: a method edge joining a reference stays
 * refused, and an id that names no node counts as a non-reference.
 */

/** A link as the canvas stores it: source, target and the target slot handle. */
export interface SlotLink {
  source: string | null | undefined;
  target: string | null | undefined;
  targetHandle?: string | null;
}

/**
 * Whether dropping `candidate` would put a second non-reference edge on an
 * occupied `(target, targetHandle)` slot.
 *
 * @param candidate The connection being dropped.
 * @param existing  The edges already on the canvas.
 * @param isReference Whether a node id names a run_reference system node;
 *   an unknown or empty id answers false.
 * @returns True when the drop must be refused.
 */
export function isDuplicateSlotEdge(
  candidate: SlotLink,
  existing: readonly SlotLink[],
  isReference: (nodeId: string | null | undefined) => boolean,
): boolean {
  const handle = candidate.targetHandle ?? null;
  const occupied = existing.filter(
    e => e.target === candidate.target && (e.targetHandle ?? null) === handle,
  );
  if (occupied.length === 0) return false;
  const allReferences =
    isReference(candidate.source) && occupied.every(e => isReference(e.source));
  return !allReferences;
}
