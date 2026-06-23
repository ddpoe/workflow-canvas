/**
 * Backend-wiring services for the actor tree.
 *
 * Three invokables, all xstate v5 actor logic:
 *
 *   - `submitPipeline`  — `fromPromise`. Wraps `/api/workflow/validate`
 *                          + `/api/workflow/run`. Returns `{ jobId }`.
 *   - `pollNodeStatus`  — `fromCallback`. Replaces `pipeline.ts::startPolling`.
 *                          Polls `/api/workflow/status/:jobId` every 2.5s,
 *                          translates per-node `Run.status` into typed
 *                          NODE_* events fed into the parent
 *                          `pipelineRunActor`, and POSTs `/api/wfc/refresh`
 *                          on terminal status.
 *   - `subscribeSSE`    — `fromCallback`. Wraps `EventSource` against
 *                          `/api/wfc/run/:runId/stream-logs`. Forwards
 *                          typed SSE_LINE / SSE_TERMINAL / SSE_ERROR
 *                          events to its parent `streamingActor`.
 *
 * Also exports `runStatusToNodeState` — the explicit single-source
 * mapping from backend `Run.status` strings to the richer-than-backend
 * nodeRunActor state. ADR-016 §Decision: "the mapping `Run.status` →
 * machine state becomes one explicit function in the polling service."
 */
import { fromCallback, fromPromise } from 'xstate';
import { get } from 'svelte/store';
import { nodes } from '../stores';
import type { PipelineJSON, RunTally, PipelineError, RunStatus } from '../types';
import type { components } from '../types/api';

// ── Types ──────────────────────────────────────────────────────────────

export interface SubmitInput {
  pipeline: PipelineJSON;
}
export interface SubmitOutput {
  jobId: string;
}
export interface SubmitError {
  message: string;
  validationErrors?: string[];
  status?: number;
}

// Output state shape pulled from the JSON returned by `/api/workflow/status`.
// ADR-015 Phase D Layer 1: this used to be a hand-rolled local
// interface; it is now the generated `NodeRunState` component, so
// renaming/removing a field in `wfc/canvas/server.py::NodeRunState`
// surfaces as a TS compile error here.  `tally` from openapi-typescript
// is `{ [k: string]: number } | null`, but the rest of the codebase
// uses the richer `RunTally` shape — we narrow at the boundary.
type ApiNodeRunState = components['schemas']['NodeRunState'];
export type BackendNodeState = Omit<ApiNodeRunState, 'tally'> & {
  tally?: RunTally | null;
};

// ── submitPipeline ─────────────────────────────────────────────────────

export const submitPipeline = fromPromise<SubmitOutput, SubmitInput>(
  async ({ input }) => {
    const { pipeline } = input;
    // Validation comes first — preserved from the legacy runPipeline().
    try {
      const vResp = await fetch('/api/workflow/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pipeline),
      });
      const vResult = await vResp.json();
      if (!vResult.valid) {
        const err: SubmitError = {
          message:
            'Cannot run — validation errors:\n' +
            (vResult.errors ?? []).join('\n'),
          validationErrors: vResult.errors ?? [],
        };
        throw err;
      }
    } catch (err) {
      // If the validate endpoint is unavailable, fall through to run.
      // A real validation failure was already thrown above.
      if ((err as SubmitError).validationErrors !== undefined) throw err;
    }

    // Single-source invariant: the per-node `data.runStatus` field is
    // a denormalized view of the spawned `nodeRunActor`'s state, written
    // exclusively by the bridge in `root.ts::bridgeChildSnapshots`.
    // The pipeline machine sends `RUN` to every spawned child on
    // `submitting.onDone`, which transitions them `idle → queued`; the
    // bridge then maps `queued → 'pending'` for the CustomNode CSS
    // class. No direct write here.

    // keep_going — same logic as legacy runPipeline. Read once at submit.
    let keepGoing = false;
    for (const n of get(nodes)) {
      if (n.data.nodeType !== 'input_selector') continue;
      const fanOut = (n.data.fanMode ?? 'out') === 'out';
      if (fanOut) {
        keepGoing = n.data.keepGoing ?? true;
        break;
      }
    }
    const body = { ...pipeline, keep_going: keepGoing };

    const resp = await fetch('/api/workflow/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const result = await resp.json();
    if (!resp.ok) {
      const err: SubmitError = {
        message:
          typeof result?.detail === 'string'
            ? result.detail
            : `Run request failed (HTTP ${resp.status})`,
        status: resp.status,
      };
      throw err;
    }
    return { jobId: result.job_id };
  },
);

// ── Run.status → nodeRunActor state mapping ────────────────────────────
//
// The richer-than-backend invariant lives here. The nodeRunActor knows
// `cancelled.becauseUpstream` vs `cancelled.becauseUser`; the backend
// only stores `cancelled`. Without a hint about *why*, treat plain
// `cancelled` as `becauseUser` (the default cancellation cause when no
// upstream info is present); when the polling service has detected an
// upstream failure, it sends UPSTREAM_FAILED instead.

export type RunStatusEvent =
  | { type: 'RUN_OK'; tally: RunTally; error_message?: string }
  | { type: 'RUN_FAILED'; error_message: string; error_traceback?: string }
  | { type: 'UPSTREAM_FAILED'; upstreamNodeId: string; upstreamRunId: string }
  | { type: 'USER_STOP' }
  | { type: 'ORPHAN_DETECTED'; pipelineStderrTail?: string }
  | { type: 'CACHE_HIT'; cacheKey: string; originalRunId?: string }
  | { type: 'STALE'; dependencyChange?: string }
  | { type: 'HEARTBEAT'; runId: string; tally?: RunTally }
  | { type: 'TALLY'; tally: RunTally }
  | null;

/**
 * Translate one backend per-node state row into the matching
 * nodeRunActor event. `null` means "no transition needed" (e.g. the
 * status is `pending` and the actor is already in `queued`).
 */
export function runStatusToNodeState(
  state: BackendNodeState,
): RunStatusEvent {
  // Cache-hit wins over `state.status` (ADR-015 Phase D Bug 4 Path A +
  // Bug 5).  The backend reports a successful cache reuse with
  // `status: 'completed'`, but we want the node state machine to land
  // in `cached` rather than `succeeded` so the streaming actor never
  // spawns (no logs to stream) and the InspectorPanel can render the
  // cache-hit banner.
  if (state.cache_hit) {
    return {
      type: 'CACHE_HIT',
      cacheKey: state.cache_key ?? '',
      originalRunId: state.original_run_id ?? undefined,
    };
  }
  const tally = state.tally ?? undefined;
  switch (state.status) {
    case 'pending':
      // No event — the parent moves every spawned child to `queued`
      // by sending `RUN` on `submitting.onDone` (with the resolved
      // jobId). Polling-emitted HEARTBEAT/RUN_OK/RUN_FAILED then drive
      // the rest of the lifecycle. By the time the polling service
      // sees a `pending` row, the child is already in `queued`.
      return null;
    case 'running':
      // The polling service uses HEARTBEAT (with runId) to flip the
      // node into `running`. Fan-out tally updates use TALLY.
      if (tally) return { type: 'TALLY', tally };
      return null;
    case 'completed':
    case 'mixed': {
      const t = tally ?? { running: 0, completed: 1, failed: 0 };
      // `mixed` aggregate carries the failed-sample `error` string
      // (server.py:1607). Forward it so nodeRunActor.completed_with_failures
      // has something to render in the Inspector's node-error-box.
      // `completed` (no failures) never has `state.error`, so the field
      // stays undefined for the happy path.
      if (state.status === 'mixed' && state.error) {
        return { type: 'RUN_OK', tally: t, error_message: state.error };
      }
      return { type: 'RUN_OK', tally: t };
    }
    case 'failed':
      return {
        type: 'RUN_FAILED',
        error_message: state.error ?? 'failed',
      };
    case 'cancelled':
      // Default to becauseUser — UPSTREAM_FAILED is sent separately by
      // the polling service when it detects sibling failure.
      if (state.upstream_node_id || state.cancelled_due_to_run_id) {
        return {
          type: 'UPSTREAM_FAILED',
          upstreamNodeId: state.upstream_node_id ?? '?',
          upstreamRunId:
            state.upstream_run_id ?? state.cancelled_due_to_run_id ?? '?',
        };
      }
      return { type: 'USER_STOP' };
    default:
      return null;
  }
}

// ── nodeRunActor state → legacy RunStatus mapping ─────────────────────
//
// The reverse direction of `runStatusToNodeState`. The actor knows the
// rich state value (e.g. `cancelled.becauseUpstream`); SvelteFlow's
// CustomNode only needs the coarse class for CSS selection (border
// color, icon, etc). This collapses the richer chart back to the same
// strings the backend emits — `data.runStatus` therefore stays the
// denormalized view of the actor, written FROM the actor.
//
// `value` shape from xstate v5: a string for top-level states (e.g.
// `'running'`) or an object `{ parent: 'child' }` for nested states
// (e.g. `{ cancelled: 'becauseUpstream' }`).

export type NodeRunValue = string | Record<string, string | object>;

/**
 * Map a nodeRunActor snapshot value to the coarse legacy RunStatus
 * string. Returns ``'idle'`` when the value is unrecognised (defensive
 * default; preserves the canvas's "blank slate" colour).
 */
export function stateValueToRunStatus(value: NodeRunValue): RunStatus {
  // Top-level: string state.
  if (typeof value === 'string') {
    switch (value) {
      case 'idle':
        return 'idle';
      case 'queued':
        return 'pending';
      case 'running':
        return 'running';
      case 'succeeded':
      case 'cached':
        return 'completed';
      case 'completed_with_failures':
        return 'mixed';
      case 'failed':
      case 'orphaned':
        return 'failed';
      case 'stale':
        // No legacy equivalent — render as idle so it doesn't masquerade
        // as a successful run.
        return 'idle';
      default:
        return 'idle';
    }
  }
  // Nested: object {parent: child}. Only `cancelled` has substates in
  // the current chart; both substates collapse to `cancelled` for the
  // CSS layer.
  const key = Object.keys(value)[0];
  if (key === 'cancelled') return 'cancelled';
  // Unknown nested shape — fall through to idle.
  return 'idle';
}

// ── pollNodeStatus ─────────────────────────────────────────────────────
//
// Replaces `pipeline.ts::startPolling`. The body is the same — fetch
// every 2.5s, translate node_states into events, POST `/api/wfc/refresh`
// on terminal, stop the timer on terminal or fetch failure. The only
// structural change is that node-state writes become typed events sent
// into the parent (`pipelineRunActor`) which fans out to children.

// Terminal overall_status values that should fire PIPELINE_DONE and
// stop the polling actor. `cancelled` belongs here: after the cancel
// endpoint flips a job's running rows to `cancelled`, the backend's
// status response correctly reports `overall_status: "cancelled"` and
// the polling loop must terminate — without this, the actor would
// keep polling until the page is refreshed.
const TERMINAL_OVERALL_STATUSES = new Set<string | undefined>([
  'completed',
  'failed',
  'completed_with_failures',
  'cancelled',
]);

export function isTerminalOverallStatus(s: string | undefined): boolean {
  return TERMINAL_OVERALL_STATUSES.has(s);
}

export interface PollInput {
  jobId: string | null;
  nodeIds: string[];
}

export const pollNodeStatus = fromCallback<{ type: string }, PollInput>(
  ({ input, sendBack }) => {
    const { jobId } = input;
    if (!jobId) return () => {};

    let stopped = false;
    // Track which nodes have already received their first HEARTBEAT so
    // we don't keep flipping them through `running` on every tick.
    const heartbeatSent = new Set<string>();

    const tick = async () => {
      if (stopped) return;
      try {
        const resp = await fetch(`/api/workflow/status/${jobId}`);
        const result = await resp.json();

        if (result.node_states) {
          for (const [nodeId, raw] of Object.entries(result.node_states)) {
            const s = raw as BackendNodeState;
            const runIds = s.run_ids ?? [];
            // The polling service ONLY emits typed events into the
            // parent actor — it never writes `data.runStatus` directly.
            // The bridge that mirrors the actor's snapshot back into
            // `data.runStatus` (for SvelteFlow's coarse CSS class
            // selection) lives in `root.ts::bridgeChildSnapshots`. This
            // is the single-source invariant called out in ADR-016 §
            // "single source of truth": the actor is authoritative;
            // `data.runStatus` is a denormalized view of it, written
            // FROM the actor, never bypassing it.
            // First time we see a non-pending status, send HEARTBEAT
            // with the first run_id so the child flips from queued to
            // running. Subsequent ticks just update tally.
            // Cache-hit short-circuit (ADR-015 Phase D Bug 4 Path A).
            // Skip HEARTBEAT entirely so the child never enters
            // `running` and the streaming actor is never spawned.
            // Treat the cache-hit row as already-handled for the
            // heartbeat bookkeeping below.
            if (s.cache_hit) {
              heartbeatSent.add(nodeId);
            }
            const firstNonPending =
              !heartbeatSent.has(nodeId) &&
              (s.status === 'running' ||
                s.status === 'completed' ||
                s.status === 'failed' ||
                s.status === 'mixed') &&
              runIds.length > 0;
            if (firstNonPending) {
              heartbeatSent.add(nodeId);
              sendBack({
                type: 'NODE_HEARTBEAT',
                nodeId,
                runId: runIds[0],
                tally: s.tally,
              } as never);
            }
            // If this same tick observed the node as already terminal,
            // remember to defer the terminal event by an animation frame
            // so Svelte paints `running` before the child transitions to
            // succeeded/failed. Without this, both child transitions
            // batch into one paint and the user sees pending → completed
            // with no visible `running` flash.
            const deferTerminal = firstNonPending && s.status !== 'running';

            const evt = runStatusToNodeState(s);
            if (!evt) continue;
            // Wrap node-event in a NODE_* event the parent dispatches.
            const eventForParent =
              evt.type === 'RUN_OK'
                ? {
                    type: 'NODE_RUN_OK',
                    nodeId,
                    tally: evt.tally,
                    error_message: evt.error_message,
                  }
                : evt.type === 'RUN_FAILED'
                  ? {
                      type: 'NODE_RUN_FAILED',
                      nodeId,
                      runId: runIds[0] ?? '?',
                      error_message: evt.error_message,
                      error_traceback: evt.error_traceback,
                    }
                  : evt.type === 'UPSTREAM_FAILED'
                    ? {
                        type: 'NODE_UPSTREAM_FAILED',
                        nodeId,
                        upstreamNodeId: evt.upstreamNodeId,
                        upstreamRunId: evt.upstreamRunId,
                      }
                    : evt.type === 'USER_STOP'
                      ? { type: 'NODE_USER_STOP', nodeId }
                      : evt.type === 'TALLY'
                        ? { type: 'NODE_TALLY', nodeId, tally: evt.tally }
                        : evt.type === 'HEARTBEAT'
                          ? {
                              type: 'NODE_HEARTBEAT',
                              nodeId,
                              runId: evt.runId,
                              tally: evt.tally,
                            }
                          : evt.type === 'CACHE_HIT'
                            ? {
                                type: 'NODE_CACHE_HIT',
                                nodeId,
                                cacheKey: evt.cacheKey,
                                originalRunId: evt.originalRunId,
                              }
                            : null;
            if (eventForParent) {
              if (deferTerminal) {
                // 150ms is long enough for the user to perceive the
                // `running` state before terminal arrives — anything
                // shorter and Svelte's batched paint can collapse the
                // two transitions into one frame.
                const evtToSend = eventForParent;
                setTimeout(() => {
                  if (!stopped) sendBack(evtToSend as never);
                }, 150);
              } else {
                sendBack(eventForParent as never);
              }
            }
          }
        }

        if (result.error) {
          const raw = result.error;
          const err: PipelineError =
            typeof raw === 'string'
              ? { message: raw, kind: 'unknown' }
              : { message: raw.message ?? String(raw), kind: raw.kind, hint: raw.hint };
          sendBack({ type: 'PIPELINE_ERROR', error: err } as never);
        }

        const overall = result.overall_status;
        if (isTerminalOverallStatus(overall)) {
          // Fire-and-forget /api/wfc/refresh so we don't await — awaiting
          // would let `stopped` get set before the deferred per-node
          // setTimeouts fire.
          fetch('/api/wfc/refresh', { method: 'POST' }).catch(() => {});
          // Defer PIPELINE_DONE so it lands AFTER any per-node terminal
          // events deferred earlier in this tick (each at 150ms). 250ms
          // here gives those a 100ms safety margin before pipelineRun
          // transitions to `done` (which stops the polling actor and
          // sets `stopped = true`, short-circuiting any not-yet-fired
          // per-node deferrals).
          setTimeout(() => {
            stopped = true;
            sendBack({ type: 'PIPELINE_DONE' } as never);
          }, 250);
        }
      } catch {
        stopped = true;
        sendBack({ type: 'PIPELINE_DONE' } as never);
      }
    };

    // Drive the first tick immediately so the user sees movement; then
    // every 1s. The legacy cadence was 2.5s, but Snakemake's startup
    // latency already eats several ticks before any node has a runId —
    // 1s gets the user out of the visual dead zone faster without
    // meaningfully increasing backend load for a dev-grade tool.
    void tick();
    const timer = setInterval(tick, 1000);

    return () => {
      stopped = true;
      clearInterval(timer);
    };
  },
);

// ── subscribeSSE ───────────────────────────────────────────────────────

export interface SSEInput {
  runId: string;
  fullMode?: boolean;
}

// Grace window for transient EventSource errors. The browser's built-in
// auto-reconnect (~3s default) handles momentary blips on its own; we
// only escalate to the machine if the failure persists past this window
// without a successful message. Keeps "user shouldn't see flicker on
// transient wifi hiccup" honest without resurrecting the reconnecting
// state in the machine.
const SSE_ERROR_GRACE_MS = 5000;

export const subscribeSSE = fromCallback<{ type: string }, SSEInput>(
  ({ input, sendBack }) => {
    const { runId, fullMode } = input;
    if (!runId) return () => {};

    const qs = fullMode ? '?full=1' : '';
    const url = `/api/wfc/run/${encodeURIComponent(runId)}/stream-logs${qs}`;
    const es = new EventSource(url);
    let errorTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const clearGrace = () => {
      if (errorTimer !== null) {
        clearTimeout(errorTimer);
        errorTimer = null;
      }
    };

    es.onmessage = ev => {
      // Any message clears the grace window — connection is alive.
      clearGrace();
      try {
        const p = JSON.parse(ev.data);
        if (p.type === 'stdout' || p.type === 'stderr') {
          sendBack({
            type: 'SSE_LINE',
            kind: p.type,
            line: p.data ?? '',
          } as never);
        } else if (p.type === 'terminal') {
          sendBack({
            type: 'SSE_TERMINAL',
            status: p.status ?? null,
            error_message: p.error_message ?? null,
            error_traceback: p.error_traceback ?? null,
          } as never);
          closed = true;
          es.close();
        }
      } catch {
        /* malformed frame — ignore */
      }
    };
    es.onerror = () => {
      // Don't close the EventSource — let the browser's built-in
      // auto-reconnect try to recover. We escalate to the machine only
      // if the failure persists past the grace window.
      if (closed || errorTimer !== null) return;
      errorTimer = setTimeout(() => {
        errorTimer = null;
        if (closed) return;
        closed = true;
        sendBack({ type: 'SSE_ERROR' } as never);
        es.close();
      }, SSE_ERROR_GRACE_MS);
    };

    return () => {
      closed = true;
      clearGrace();
      es.close();
    };
  },
);
