/**
 * Singleton UI state stores for the load-in-canvas dialog and graft
 * toast. Hosted at the canvas-app root so any caller can
 * trigger a styled modal/toast without local Svelte state plumbing.
 *
 *   - confirmDialogState: when non-null, App.svelte renders ConfirmDialog
 *     bound to this object. The Promise resolves on Cancel or Confirm.
 *   - graftToastState: when non-null, App.svelte renders GraftToast.
 */
import { writable } from 'svelte/store';

// 'switch-watch': the canvas is watching one running pipeline and the user
// asked to watch another; `currentName` keeps running, `targetName` is shown.
export type ConfirmDialogVariant = 'dirty-confirm' | 'running-block' | 'switch-watch';

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
 * Center-on-node request store. Components that don't have direct
 * access to the SvelteFlow `fitView` helper (which is bound at the App.svelte
 * root via `<FlowHelper>`) can request canvas centering on a specific node
 * by setting this writable to a node id. App.svelte subscribes and calls
 * `flowFitView({ nodes: [{ id }], duration, padding })` then resets the
 * store to null. Used by the GraftToast `[Jump to node]` action so a
 * grafted node is both selected and centered.
 */
export const centerOnNodeRequest = writable<string | null>(null);

/** A request to open a node's Inspector at one of its parameters. */
export interface InspectParamRequest {
  nodeId: string;
  param: string;
}

/**
 * Open-the-Inspector-at-a-parameter request store. A Built-from `param ↗`
 * link sets it beside selecting and centring the node; the method node's
 * Inspector body takes it when it shows that node, scrolls the parameter's
 * row into view, highlights it briefly, and resets the store to null.
 */
export const inspectParamRequest = writable<InspectParamRequest | null>(null);

/** The app's top-level tabs. */
export type AppTab = 'builder' | 'registry' | 'history';

/**
 * Tab-switch request store. A link from inside one tab to another (the
 * Inspector's Run Reference `param ↗` link to History, the Registry's Draft
 * run tab opening History) sets it; App.svelte switches to that tab and
 * resets the store to null.
 */
export const tabRequest = writable<AppTab | null>(null);
