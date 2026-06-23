/**
 * Singleton UI state stores for the load-in-canvas dialog and graft
 * toast (D-1 / D-3). Hosted at the canvas-app root so any caller can
 * trigger a styled modal/toast without local Svelte state plumbing.
 *
 *   - confirmDialogState: when non-null, App.svelte renders ConfirmDialog
 *     bound to this object. The Promise resolves on Cancel or Confirm.
 *   - graftToastState: when non-null, App.svelte renders GraftToast.
 */
import { writable } from 'svelte/store';

export type ConfirmDialogVariant = 'dirty-confirm' | 'running-block';

export interface ConfirmDialogRequest {
  variant: ConfirmDialogVariant;
  currentName?: string;
  targetName?: string;
  runningPipelineLabel?: string;
  /** Resolved with `true` for Confirm, `false` for Cancel. */
  resolve: (proceed: boolean) => void;
}

export const confirmDialogState = writable<ConfirmDialogRequest | null>(null);

export interface GraftToastRequest {
  message: string;
  detail?: string;
  /** Optional jump action — selects the just-grafted node on the canvas. */
  onJump?: () => void;
}

export const graftToastState = writable<GraftToastRequest | null>(null);

/**
 * Center-on-node request store (D-13). Components that don't have direct
 * access to the SvelteFlow `fitView` helper (which is bound at the App.svelte
 * root via `<FlowHelper>`) can request canvas centering on a specific node
 * by setting this writable to a node id. App.svelte subscribes and calls
 * `flowFitView({ nodes: [{ id }], duration, padding })` then resets the
 * store to null. Used by the GraftToast `[Jump to node]` action so a
 * grafted node is both selected and centered.
 */
export const centerOnNodeRequest = writable<string | null>(null);
