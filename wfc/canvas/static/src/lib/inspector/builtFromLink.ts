/**
 * Where a Built-from `param ↗` link in the options picker goes.
 *
 * A method upstream's link selects that node, asks the canvas to centre it
 * (`centerOnNodeRequest`) and asks its Inspector to land on the parameter
 * (`inspectParamRequest`). A Run Reference's link opens the run in History
 * on its Params tab. Neither touches the row the link sits in: its typed
 * value is kept across the switch by `rowActors.ts`, unlocked and never
 * committed.
 */
import { nodes, selectedNodeId } from '../builder/stores.js';
import { openRunOn } from '../history/historyStore.js';
import { centerOnNodeRequest, inspectParamRequest, tabRequest } from '../shared/uiState.js';
import type { OptionSection } from './options.js';

/**
 * Follow a Built-from link to the parameter that built the names.
 *
 * Args:
 *     section: The picker section the pill sits in.
 *     param: The upstream parameter the pill names.
 */
export function followBuiltFrom(section: OptionSection, param: string): void {
  if (section.kind === 'run_reference') {
    const runId = section.runId;
    if (!runId) return;
    tabRequest.set('history');
    // History reloads its runs as it shows, and the detail panel remounts
    // when they arrive; a run opened before that reload starts would have
    // its Params tab taken by the panel that is about to go. Opening it on
    // the next task lands after the reload has begun.
    setTimeout(() => openRunOn(runId, 'params'), 0);
    return;
  }
  const id = section.nodeId;
  nodes.update(ns => ns.map(n => (n.selected ?? false) === (n.id === id) ? n : { ...n, selected: n.id === id }));
  selectedNodeId.set(id);
  centerOnNodeRequest.set(id);
  inspectParamRequest.set({ nodeId: id, param });
}
