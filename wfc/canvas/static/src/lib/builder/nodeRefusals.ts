/**
 * The server's refusals, shown on the nodes they name.
 *
 * Validate and Run go to the server, which decides whether a document may
 * run. A refusal that names a node -- a param its contract no longer
 * declares, a wrong-typed value, a link to an undeclared slot, a required
 * input nothing is connected to -- is shown on that node and in the
 * Inspector until the next Validate or Run, for the user to fix. Nothing
 * here checks the document or changes it: the messages are the server's.
 *
 * A structured refusal carries its node id (the run route's
 * ``draft_refused`` entries, Validate's ``draftRefusals``); a plain error
 * names its node the way every server refusal starts, ``Node '<id>'``.
 */
import { get } from 'svelte/store';
import { nodes, updateNodeData } from './stores.js';

/** A refusal that names its node, as the server's ``DraftRefusal`` does. */
export interface NodeRefusal {
  nodeId: string;
  message: string;
}

const NODE_PREFIX = /^Node '([^']+)'/;

/**
 * Group the server's refusals by the node each names.
 *
 * Args:
 *     errors: Plain error lines (Validate's ``errors``); a line that names
 *         no node is left out.
 *     refusals: Refusals carrying their node id.
 *
 * Returns:
 *     Each named node's messages, in the order given, without repeats.
 */
export function refusalsByNode(
  errors: readonly string[] = [],
  refusals: readonly NodeRefusal[] = [],
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const add = (id: string, message: string) => {
    const list = (out[id] ??= []);
    if (!list.includes(message)) list.push(message);
  };
  for (const r of refusals) if (r.nodeId) add(r.nodeId, r.message);
  for (const line of errors) {
    const id = NODE_PREFIX.exec(line)?.[1];
    if (id) add(id, line);
  }
  return out;
}

/** Remove every node's refusals (a new Validate or Run is starting). */
export function clearRefusals(): void {
  for (const n of get(nodes)) {
    if (n.data.refusals?.length) updateNodeData(n.id, { refusals: undefined });
  }
}

/**
 * Show the server's refusals on the nodes they name; every other node's
 * refusals are cleared.
 *
 * Args:
 *     byNode: Messages by node id, as ``refusalsByNode`` groups them.
 */
export function showRefusals(byNode: Record<string, string[]>): void {
  for (const n of get(nodes)) {
    const mine = byNode[n.id];
    if (mine?.length) updateNodeData(n.id, { refusals: [...mine] });
    else if (n.data.refusals?.length) updateNodeData(n.id, { refusals: undefined });
  }
}
