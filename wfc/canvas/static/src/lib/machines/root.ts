/**
 * Module-level root actor for the canvas lifecycle.
 *
 * Exports a singleton `pipelineRunActor` started once at app mount.
 * Components import this directly (per ADR-016 Open Q #1 / D-2) rather
 * than going through Svelte context. In DEV the Stately Inspector is
 * wired so a maintainer can observe the actor tree live; the
 * `@statelyai/inspect` import is gated behind `import.meta.env.DEV`
 * so Vite tree-shakes it out of the production bundle.
 */
import { createActor, fromPromise } from 'xstate';
import {
  runState,
  setPipelineError,
  updateNodeData,
} from '../stores';
import {
  makePipelineRunMachine,
  type PipelinePayload,
} from './pipelineRun.machine';
import { makeNodeRunMachine, type NodeRunActor } from './nodeRun.machine';
import { makeStreamingMachine } from './streaming.machine';
import {
  submitPipeline,
  pollNodeStatus,
  subscribeSSE,
  stateValueToRunStatus,
  type NodeRunValue,
} from './services';
import {
  makeParamEditorAggregatorMachine,
  type ChildActor,
  type ParamEditorAggregatorActor,
} from './paramEditorAggregator.machine';

/**
 * DEV-only Stately Inspector callback.
 *
 * Captured at module load (top-level await) and passed both to the
 * pipelineRunActor below AND exported for paramEditorActor instances
 * spawned by ValueList rows (ADR-016 Phase 2). Per cycle decision D-4
 * the param-editor actors are NOT nested under pipelineRunActor — they
 * form their own inspector tree root, so each ValueList row attaches
 * the same callback to its own `createActor()` call to surface
 * transitions in the same DEV inspector tab.
 *
 * Production builds: `import.meta.env.DEV` is statically `false` and
 * Vite tree-shakes the dynamic import below, so this stays `undefined`
 * and `createActor({ inspect })` is a no-op.
 */
export let inspect: ((evt: { type: string }) => void) | undefined;

// Inspector handle kept module-local so `startInspector()` (called from
// the DevToolbar "Open Inspector" button) can defer `window.open()` to
// a real user-gesture context. Browsers block popups opened during
// module init; gating the open behind a click makes the popup land
// reliably every time. Until the button is clicked the inspect
// callback still buffers events into the adapter's deferred queue
// (capped at maxDeferredEvents below), and they flush on connect.
let _inspector: { start: () => void } | undefined;

if (import.meta.env.DEV) {
  // Top-level `await` — Vite tree-shakes the entire branch out of prod
  // when `import.meta.env.DEV` is statically `false`, so the dynamic
  // import is excluded from the production bundle. The await ensures
  // `inspect` is assigned BEFORE `createActor()` runs below — without
  // it, xstate captured `undefined` and the inspector tab never
  // connected (the bug fixed in review iteration 1).
  try {
    const mod = await import('@statelyai/inspect');
    _inspector = mod.createBrowserInspector({
      autoStart: false,
      // Default is 200 — bump generously since we hold events between
      // page-load and the user clicking "Open Inspector". Dev-only.
      maxDeferredEvents: 5000,
    });
    inspect = _inspector.inspect;
    // eslint-disable-next-line no-console
    console.info(
      '[xstate] Stately Inspector wired (deferred). ' +
        'Click DEV → "Open Inspector" to attach the popup at ' +
        'https://stately.ai/registry/inspect.',
    );
  } catch {
    /* inspector unavailable — silently skip */
  }
}

/**
 * Open (or reopen) the Stately Inspector popup. Must be called from a
 * user gesture handler — e.g., the DevToolbar button — so the browser's
 * popup blocker treats `window.open()` as user-initiated. Returns
 * `false` when the inspector wasn't wired (prod build, or the dynamic
 * import failed); the caller can surface a flash in that case.
 */
export function startInspector(): boolean {
  if (!_inspector) return false;
  _inspector.start();
  return true;
}

// Wire the streaming machine (not the raw callback) as the
// `streamingActor` slot on nodeRunActor. The streaming machine internally
// invokes `subscribeSSE` to wrap the EventSource. Routing through the
// machine gives the InspectorPanel a typed snapshot value
// (connecting/streaming/terminal/reconnecting) to read via
// `nodeRunActor.snapshot.children.streaming`, instead of the local
// EventSource $effect that previously duplicated state.
const wiredStreamingMachine = makeStreamingMachine().provide({
  actors: { subscribeSSE },
});

const wiredNodeRunMachine = makeNodeRunMachine().provide({
  actors: { streamingActor: wiredStreamingMachine },
});

// ── Param-editor aggregator singleton (ADR-016 Phase 2 expand) ────────
//
// Replaces the legacy `commitAllSignal` writable + `dirtyParams` Set.
// Every ValueList row REGISTERs its paramEditorActor / variantActor on
// mount and UNREGISTERs on destroy. The pipelineRunActor's `preflight`
// invokes a tiny `awaitAllCommitted` actor that subscribes here and
// resolves on `idle/allCommitted → committingAll → allCommitted` —
// replacing the 0.2.8 microtask race with a typed transition.
//
// Lives in its own inspector tree root per cycle decision D-4
// (paramEditor children are not nested under pipelineRunActor).
export const paramEditorAggregator: ParamEditorAggregatorActor = createActor(
  makeParamEditorAggregatorMachine(),
  { inspect },
) as ParamEditorAggregatorActor;
paramEditorAggregator.start();

// Bridge: every registered child's snapshot transitions are forwarded
// as CHILD_SETTLED events when the child leaves an editing-shaped
// state. We track subscriptions per-child (by actor reference) so a
// re-REGISTER (HMR / inspector tab swap) doesn't double-subscribe.
const childAggregatorSubs = new WeakMap<ChildActor, () => void>();
const childAggregatorIds = new WeakMap<ChildActor, string>();

function bridgeChildToAggregator(id: string, child: ChildActor): void {
  if (childAggregatorSubs.has(child)) return;
  childAggregatorIds.set(child, id);
  let lastEditing = isEditingShaped(child);
  const sub = child.subscribe(() => {
    const editingNow = isEditingShaped(child);
    if (lastEditing && !editingNow && isSettledShaped(child)) {
      paramEditorAggregator.send({ type: 'CHILD_SETTLED', id });
    }
    lastEditing = editingNow;
  });
  childAggregatorSubs.set(child, () => sub.unsubscribe());
}

function unbridgeChild(child: ChildActor): void {
  const cleanup = childAggregatorSubs.get(child);
  if (cleanup) {
    cleanup();
    childAggregatorSubs.delete(child);
    childAggregatorIds.delete(child);
  }
}

function isEditingShaped(child: ChildActor): boolean {
  const v = child.getSnapshot().value;
  if (typeof v !== 'string') return false;
  return (
    v === 'editing' ||
    v === 'committing' ||
    v === 'invalid' ||
    v === 'addingVariant' ||
    v === 'editingValue'
  );
}

function isSettledShaped(child: ChildActor): boolean {
  const v = child.getSnapshot().value;
  if (typeof v !== 'string') return false;
  if (v === 'committing') return false;
  if (v === 'editing' || v === 'addingVariant' || v === 'editingValue') {
    return false;
  }
  return true;
}

/**
 * Public hooks ValueList.svelte calls on mount/destroy. Wraps the
 * REGISTER/UNREGISTER events plus the snapshot bridge so the component
 * doesn't need to know aggregator internals.
 */
export function registerEditorChild(id: string, child: ChildActor): void {
  paramEditorAggregator.send({ type: 'REGISTER', id, actor: child });
  bridgeChildToAggregator(id, child);
}

export function unregisterEditorChild(id: string, child: ChildActor): void {
  paramEditorAggregator.send({ type: 'UNREGISTER', id });
  unbridgeChild(child);
}

/**
 * Returns true iff the aggregator currently has any registered child
 * in an editing-shaped state. Used by Toolbar / InspectorPanel for
 * Lock All button visibility — replacing `dirtyParams.size > 0`.
 */
export function hasDirtyEditors(): boolean {
  for (const child of Object.values(
    paramEditorAggregator.getSnapshot().context.children,
  )) {
    if (isEditingShaped(child)) return true;
  }
  return false;
}

/**
 * Same as hasDirtyEditors but scoped to a single nodeId. The aggregator
 * keys children by `${nodeId}::${paramName}::...` so a prefix match is
 * sufficient. Used by InspectorPanel for the per-node Lock All button.
 */
export function nodeHasDirtyEditors(nodeId: string): boolean {
  const prefix = `${nodeId}::`;
  for (const [id, child] of Object.entries(
    paramEditorAggregator.getSnapshot().context.children,
  )) {
    if (!id.startsWith(prefix)) continue;
    if (isEditingShaped(child)) return true;
  }
  return false;
}

/**
 * Returns a list of "node · param" tuples for currently-dirty children.
 * Used by Toolbar's confirm-and-lock dialog to tell the user which rows
 * they're committing. Each tuple is "{nodeId}::{paramName}" — the
 * caller is responsible for looking up the node label.
 */
export function dirtyEditorIds(): string[] {
  const out: string[] = [];
  for (const [id, child] of Object.entries(
    paramEditorAggregator.getSnapshot().context.children,
  )) {
    if (isEditingShaped(child)) out.push(id);
  }
  return out;
}

/**
 * Imperative await: send COMMIT_ALL to the aggregator and resolve when
 * it reaches `allCommitted`. The pipelineRunActor's `preflight` invokes
 * this via fromPromise; Toolbar's Run-button confirm-and-lock can also
 * call it directly when it needs to stop and check for invalid rows
 * before submitting.
 */
export function awaitAllCommitted(): Promise<void> {
  return new Promise(resolve => {
    paramEditorAggregator.send({ type: 'COMMIT_ALL' });
    if (paramEditorAggregator.getSnapshot().value === 'allCommitted') {
      resolve();
      return;
    }
    const sub = paramEditorAggregator.subscribe(snap => {
      if (snap.value === 'allCommitted') {
        sub.unsubscribe();
        resolve();
      }
    });
  });
}

const machine = makePipelineRunMachine().provide({
  actors: {
    submitPipeline,
    pollNodeStatus,
    streamingActor: wiredStreamingMachine,
    nodeRunActor: wiredNodeRunMachine,
    awaitAllCommitted: fromPromise(async () => {
      await awaitAllCommitted();
    }),
  },
});

export const pipelineRunActor = createActor(machine, {
  input: { pipeline: { nodes: [] } },
  inspect,
});
pipelineRunActor.start();

// Bridge actor state into the legacy `runState` writable so the
// existing toolbar status bar + banner code keeps working without
// touching every consumer in this cycle. This bridge is removable
// when those consumers subscribe to the actor directly.
//
// Also bridges `context.jobId` (= the pipeline_id returned by
// /api/workflow/run, see server.py:1489) into the canvas-level
// `canvasPipelineId` store so the D-10 running-block gate sees the
// new identity as soon as submit resolves. The store stays in sync:
//   - submitting.onDone assigns context.jobId → bridge writes store
//   - RESET clears context.jobId → bridge writes null
let _lastBridgedJobId: string | null = null;
pipelineRunActor.subscribe(snap => {
  const v = snap.value;
  const stateName = typeof v === 'string' ? v : Object.keys(v)[0] ?? 'idle';
  const running =
    stateName === 'preflight' ||
    stateName === 'submitting' ||
    stateName === 'polling';
  runState.update(rs => ({
    ...rs,
    running,
    jobId: snap.context.jobId,
  }));
  if (snap.context.pipelineError) {
    setPipelineError(snap.context.pipelineError);
  }
  // Mirror jobId into canvasPipelineId. Lazy import keeps the legacy
  // module import graph (root.ts ↔ stores.ts ↔ pipeline.ts) acyclic.
  const newPid = snap.context.jobId;
  if (newPid !== _lastBridgedJobId) {
    _lastBridgedJobId = newPid;
    import('../pipeline.js').then(m => {
      m.canvasPipelineId.set(newPid);
    }).catch(() => {});
  }
});

// ── Per-node bridge: actor snapshot → data.runStatus / data.runTally ──
//
// Single-source invariant (ADR-016 §"single source of truth"): the
// nodeRunActor is authoritative; CustomNode's CSS-class lookup of
// `data.runStatus` is a denormalized view of that actor, written FROM
// the actor — never bypassing it.
//
// This block subscribes to every spawned `nodeRunActor` once and
// forwards each snapshot tick into `updateNodeData`. We track which
// refs we've already subscribed to (by reference identity) so a parent
// re-render doesn't double-subscribe; on RESET the parent stops the old
// refs and the unsubscribe runs.

const childSubs = new WeakMap<NodeRunActor, () => void>();

function bridgeChildSnapshots(
  refs: Record<string, NodeRunActor>,
): void {
  // Subscribe new children.
  for (const [nodeId, ref] of Object.entries(refs)) {
    if (childSubs.has(ref)) continue;
    const sub = ref.subscribe(childSnap => {
      try {
        updateNodeData(nodeId, {
          runStatus: stateValueToRunStatus(childSnap.value as NodeRunValue),
          runTally: childSnap.context.tally ?? childSnap.context.finalTally,
        });
      } catch {
        /* node may have been deleted from the canvas mid-run */
      }
    });
    childSubs.set(ref, () => sub.unsubscribe());
  }
}

pipelineRunActor.subscribe(snap => {
  bridgeChildSnapshots(snap.context.nodeRefs);
});

/**
 * Send the RUN_CLICKED event to the singleton with a snapshot of the
 * current pipeline. The Toolbar calls this; replaces the legacy
 * `runPipeline()` body in `pipeline.ts`.
 */
export function dispatchRun(pipeline: PipelinePayload): void {
  pipelineRunActor.send({ type: 'RUN_CLICKED', pipeline });
}

/**
 * Reset the actor tree (tear down all spawned children, clear context).
 * Called by `clearRunState()` so the canvas Clear button still wipes
 * lifecycle state along with nodes/edges.
 */
export function dispatchReset(): void {
  pipelineRunActor.send({ type: 'RESET' });
}

/**
 * Stop the in-flight run. Cascades USER_STOP to every still-running
 * child nodeRunActor and moves the pipeline actor to `done`.
 *
 * ADR-015 Phase D Pass 2: also fires a real backend cancel POST so the
 * Snakemake subprocess (and its descendants) is terminated and the
 * affected run rows flip to ``cancelled`` with
 * ``error_message="Cancelled by user"``.  Fire-and-forget — the local
 * USER_STOP transition does not depend on the network call succeeding.
 */
export function dispatchUserStop(): void {
  const jobId = pipelineRunActor.getSnapshot().context.jobId;
  if (jobId) {
    fetch(`/api/workflow/cancel/${encodeURIComponent(jobId)}`, {
      method: 'POST',
    }).catch(err => {
      // eslint-disable-next-line no-console
      console.warn('[dispatchUserStop] cancel POST failed:', err);
    });
  }
  pipelineRunActor.send({ type: 'USER_STOP' });
}
