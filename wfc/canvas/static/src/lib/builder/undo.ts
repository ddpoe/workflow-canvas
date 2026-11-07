/**
 * Undo/redo history for canvas state.
 * Stores snapshots of nodes + edges as plain JSON.
 */
import { get } from 'svelte/store';
import { nodes, edges, canvasGeneration, newCanvasGeneration } from './stores.js';
import type { Node, Edge } from '@xyflow/svelte';

interface Snapshot {
  nodes: string;  // JSON-serialized
  edges: string;
  /** The canvas generation the snapshot was taken on. */
  generation: number;
}

const undoStack: Snapshot[] = [];
const redoStack: Snapshot[] = [];
const MAX_HISTORY = 50;
let paused = false;

function takeSnapshot(): Snapshot {
  return {
    nodes: JSON.stringify(get(nodes)),
    edges: JSON.stringify(get(edges)),
    generation: get(canvasGeneration),
  };
}

/**
 * Put a snapshot back on the canvas.
 *
 * A snapshot from another canvas generation (an undo or redo across a
 * pipeline load or Clear) replaces the canvas, so it starts a new
 * generation, as the load did: state kept for the rows of the canvas being
 * left, such as an unlocked row's draft, is dropped. The other snapshots of
 * the restored canvas move to the new generation with it, so a later undo
 * within that canvas keeps its kept drafts.
 *
 * Args:
 *     snap: The snapshot to restore.
 */
function restoreSnapshot(snap: Snapshot): void {
  paused = true;
  if (snap.generation !== get(canvasGeneration)) {
    const before = snap.generation;
    newCanvasGeneration();
    const now = get(canvasGeneration);
    for (const s of [...undoStack, ...redoStack]) if (s.generation === before) s.generation = now;
  }
  nodes.set(JSON.parse(snap.nodes));
  edges.set(JSON.parse(snap.edges));
  paused = false;
}

/** Call before a mutation to push the current state onto the undo stack. */
export function pushState(): void {
  if (paused) return;
  undoStack.push(takeSnapshot());
  if (undoStack.length > MAX_HISTORY) undoStack.shift();
  redoStack.length = 0;  // clear redo on new action
}

export function undo(): void {
  if (undoStack.length === 0) return;
  redoStack.push(takeSnapshot());
  const prev = undoStack.pop()!;
  restoreSnapshot(prev);
}

export function redo(): void {
  if (redoStack.length === 0) return;
  undoStack.push(takeSnapshot());
  const next = redoStack.pop()!;
  restoreSnapshot(next);
}

export function canUndo(): boolean { return undoStack.length > 0; }
export function canRedo(): boolean { return redoStack.length > 0; }

/** Set up keyboard shortcuts for undo/redo. */
export function initKeyboardShortcuts(): void {
  window.addEventListener('keydown', (e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
      e.preventDefault();
      undo();
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
      e.preventDefault();
      redo();
    }
  });
}
