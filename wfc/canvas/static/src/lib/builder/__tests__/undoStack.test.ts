/**
 * The undo stack replays canvas snapshots
 * (canvas-ui case `catalog.builder-undo-stack`), and
 * every delete path ends in the same state
 * (canvas-ui case `catalog.builder-delete-nodes`).
 *
 * No other Vitest module and no Playwright spec touches the undo stack or
 * `deleteNodes`.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import type { Node, Edge } from '@xyflow/svelte';
import { nodes, edges, selectedNodeId, deleteNodes } from '../stores';
import { pushState, undo, redo, canUndo, canRedo } from '../undo';
import type { CanvasNodeData } from '../../shared/types';

function mkNode(id: string): Node<CanvasNodeData> {
  return {
    id,
    type: 'custom',
    position: { x: 0, y: 0 },
    data: {
      label: id,
      method: 'm',
      module: 'mod',
      color: '#ccc',
      inputs: [],
      outputs: [{ name: 'out', type: 'csv' }],
      params: [],
      paramValues: {},
      runStatus: 'idle',
      expanded: false,
      nodeType: 'method',
    },
  };
}

function mkEdge(id: string, source: string, target: string): Edge {
  return { id, type: 'deletable', source, target };
}

/**
 * The undo/redo stacks are module-level and have no reset export. Drain
 * the undo side so each test counts from zero; the first `pushState` in a
 * test then clears the redo side ("clear redo on new action").
 */
function drainUndo(): void {
  let guard = 0;
  while (canUndo() && guard < 100) { undo(); guard += 1; }
}

/**
 * A delete the way every delete path performs it: hand the ids to the store,
 * which records the undo snapshot.
 */
function deleteViaPath(ids: string[]): void {
  deleteNodes(ids);
}

beforeEach(() => {
  nodes.set([]);
  edges.set([]);
  selectedNodeId.set(null);
  drainUndo();
});

describe('the undo stack replays canvas snapshots', () => {
  it('a push records the state before the mutation; undo restores it and redo returns', () => {
    nodes.set([mkNode('node_1')]);
    edges.set([]);

    // Mutation 1: add a node and an edge to it.
    pushState();
    nodes.set([mkNode('node_1'), mkNode('node_2')]);
    edges.set([mkEdge('e_1', 'node_1', 'node_2')]);

    expect(canUndo()).toBe(true);
    expect(canRedo()).toBe(false);

    undo();
    // Back to the one-node, no-edge canvas the push captured.
    expect(get(nodes).map(n => n.id)).toEqual(['node_1']);
    expect(get(edges)).toEqual([]);
    expect(canRedo()).toBe(true);

    redo();
    expect(get(nodes).map(n => n.id)).toEqual(['node_1', 'node_2']);
    expect(get(edges).map(e => e.id)).toEqual(['e_1']);
  });

  it('undo with an empty stack and redo with nothing undone are no-ops, not exceptions', () => {
    nodes.set([mkNode('node_1')]);

    // Undo side: the stack was drained, so there is nothing to go back to.
    expect(canUndo()).toBe(false);
    expect(() => undo()).not.toThrow();
    expect(get(nodes).map(n => n.id)).toEqual(['node_1']);

    // Redo side: a fresh push clears the redo stack ("clear redo on new
    // action"), so at this point nothing has been undone.
    pushState();
    expect(canRedo()).toBe(false);
    expect(() => redo()).not.toThrow();
    expect(get(nodes).map(n => n.id)).toEqual(['node_1']);
  });

  it('restoring a snapshot does not itself push, so one undo is one step back', () => {
    nodes.set([mkNode('node_1')]);
    pushState();
    nodes.set([mkNode('node_1'), mkNode('node_2')]);

    undo();
    // The restore ran with pushes paused: the undo stack is empty again
    // rather than holding a snapshot of the restore.
    expect(canUndo()).toBe(false);
    expect(get(nodes).map(n => n.id)).toEqual(['node_1']);
  });
});

describe('every delete path ends in the same state', () => {
  it('deleting the selected node drops it and every edge touching it, and clears the selection', () => {
    nodes.set([mkNode('node_1'), mkNode('node_2'), mkNode('node_3')]);
    edges.set([
      mkEdge('e_in', 'node_1', 'node_2'),   // incoming to the doomed node
      mkEdge('e_out', 'node_2', 'node_3'),  // outgoing from it
      mkEdge('e_far', 'node_1', 'node_3'),  // untouched
    ]);
    selectedNodeId.set('node_2');

    deleteViaPath(['node_2']);

    expect(get(nodes).map(n => n.id)).toEqual(['node_1', 'node_3']);
    expect(get(edges).map(e => e.id)).toEqual(['e_far']);
    // The Inspector must not keep drawing a node that no longer exists.
    expect(get(selectedNodeId)).toBeNull();
  });

  it('a delete of unselected nodes leaves the selection alone', () => {
    nodes.set([mkNode('node_1'), mkNode('node_2')]);
    selectedNodeId.set('node_1');

    deleteViaPath(['node_2']);

    expect(get(selectedNodeId)).toBe('node_1');
    expect(get(nodes).map(n => n.id)).toEqual(['node_1']);
  });

  it('one delete leaves exactly one undo snapshot, so one key press is one undo step', () => {
    nodes.set([mkNode('node_1'), mkNode('node_2')]);
    edges.set([mkEdge('e_1', 'node_1', 'node_2')]);
    selectedNodeId.set('node_2');

    // No push by the caller: the snapshot belongs to the delete, so every
    // path that hands its ids to the store records the same one step.
    deleteNodes(['node_2']);
    expect(canUndo()).toBe(true);

    undo();
    // One undo restores the whole canvas, and there is no second snapshot
    // behind it.
    expect(get(nodes).map(n => n.id)).toEqual(['node_1', 'node_2']);
    expect(get(edges).map(e => e.id)).toEqual(['e_1']);
    expect(canUndo()).toBe(false);
  });

  it('two paths firing on one key press still leave one undo step', () => {
    nodes.set([mkNode('node_1'), mkNode('node_2')]);
    selectedNodeId.set('node_2');

    // Svelte Flow's own delete and the canvas key handler can both run for
    // one press; the second call finds the node already gone. A delete that
    // removes nothing is not a step to undo.
    deleteNodes(['node_2']);
    deleteNodes(['node_2']);

    undo();
    expect(get(nodes).map(n => n.id)).toEqual(['node_1', 'node_2']);
    expect(canUndo()).toBe(false);
  });
});
