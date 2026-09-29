/**
 * Undo/redo history for canvas state.
 * Stores snapshots of nodes + edges as plain JSON.
 */
import { get } from 'svelte/store';
import { nodes, edges } from './stores.js';
import type { Node, Edge } from '@xyflow/svelte';

interface Snapshot {
  nodes: string;  // JSON-serialized
  edges: string;
}

const undoStack: Snapshot[] = [];
const redoStack: Snapshot[] = [];
const MAX_HISTORY = 50;
let paused = false;

function takeSnapshot(): Snapshot {
  return {
    nodes: JSON.stringify(get(nodes)),
    edges: JSON.stringify(get(edges)),
  };
}

function restoreSnapshot(snap: Snapshot): void {
  paused = true;
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
