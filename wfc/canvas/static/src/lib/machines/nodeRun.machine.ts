/**
 * Per-node lifecycle machine.
 *
 * Implements the richer-than-backend chart documented in ADR-016 §
 * "nodeRunActor state map". The chart distinguishes user-meaningful
 * outcomes that the backend `Run.status` enum collapses (`cancelled`
 * is one column in the DB but two states here: `cancelled.becauseUpstream`
 * and `cancelled.becauseUser`).
 *
 * Lifetime: one per canvas node. Spawned by `pipelineRunActor` on
 * entry to `preflight`/`polling`; torn down when the node is deleted
 * from the canvas.
 *
 * Tests in `__tests__/nodeRun.test.ts` cover every transition.
 */
import { setup, assign, fromCallback, type ActorRefFrom } from 'xstate';
import type { RunTally } from '../types';

// ── Context ────────────────────────────────────────────────────────────
//
// Every payload from the state map sits here. Fields are populated as
// the actor enters the relevant state; the UI reads `value` + the
// matching context field to render banners (failed → error_message,
// cancelled.becauseUpstream → upstreamNodeId + upstreamRunId, etc.).

export interface NodeRunContext {
  nodeId: string;
  // Populated on entry to `queued` (RUN event). Identifies the
  // pipeline submission this node was queued under.
  jobId?: string;
  // Populated on first heartbeat from the polling service.
  runId?: string;
  // Failure payload — ADR-015 Phase A.1 surfaces both fields.
  error_message?: string;
  error_traceback?: string;
  // Cancellation cause payload (becauseUpstream branch only).
  upstreamNodeId?: string;
  upstreamRunId?: string;
  // Orphaned payload — pipeline-level stderr tail (ADR-015 Phase A.3).
  pipelineStderrTail?: string;
  // Mid-run tally (running). The latest tally received from polling.
  tally?: RunTally;
  // Final tally (succeeded / completed_with_failures). Frozen at exit
  // from `running`.
  finalTally?: RunTally;
  // Cache hit payload.
  cacheKey?: string;
  originalRunId?: string;
  // Stale payload — what changed upstream that invalidated this node.
  dependencyChange?: string;
}

// ── Events ─────────────────────────────────────────────────────────────

export type NodeRunEvent =
  | { type: 'RUN'; jobId: string }
  | { type: 'HEARTBEAT'; runId: string; tally?: RunTally }
  | { type: 'TALLY'; tally: RunTally }
  // `error_message` is optional and only carried when the polling
  // bridge maps a backend `mixed` status (some samples failed) — used
  // to populate `completed_with_failures.context.error_message` so the
  // Inspector can render the failed-sample error string. The plain
  // succeeded path (`status: 'completed'`) leaves it undefined.
  | { type: 'RUN_OK'; tally: RunTally; error_message?: string }
  | { type: 'RUN_FAILED'; error_message: string; error_traceback?: string }
  | { type: 'UPSTREAM_FAILED'; upstreamNodeId: string; upstreamRunId: string }
  | { type: 'USER_STOP' }
  | { type: 'ORPHAN_DETECTED'; pipelineStderrTail?: string }
  | { type: 'CACHE_HIT'; cacheKey: string; originalRunId?: string }
  | { type: 'STALE'; dependencyChange?: string }
  | { type: 'RESET' };

// ── Streaming child stub ───────────────────────────────────────────────
//
// The real `streamingActor` lives in `streaming.machine.ts`. We declare
// a no-op callback *slot* here; tests inject a callback stub via
// `.provide`, and `root.ts` injects the real streaming machine via
// `.provide`. xstate v5's setup actor map is structurally typed — the
// slot is replaced wholesale at provide time, so the slot type just
// needs to be *some* actor logic; the runtime substitution can be a
// machine, callback, promise, etc. Invocation is scoped to `running`,
// so the child is torn down when the parent leaves that state.
//
// The InspectorPanel reads the streaming machine's snapshot via
// `nodeRunSnap.children.streaming`. Because the slot is statically a
// `fromCallback`, TypeScript can't see that the runtime value is a
// machine actor — consumers cast through `unknown` and call
// `getSnapshot().value` directly.

const streamingActorSlot = fromCallback(() => {
  return () => {};
});

// ── Machine factory ────────────────────────────────────────────────────
//
// `makeNodeRunMachine` returns a fresh machine each call so tests don't
// share invocation state. The factory accepts an initial nodeId so the
// parent (`pipelineRunActor`) can spawn one per canvas node.

export interface NodeRunInput {
  nodeId?: string;
}

export function makeNodeRunMachine(defaultNodeId: string = 'unknown') {
  return setup({
    types: {} as {
      context: NodeRunContext;
      events: NodeRunEvent;
      input: NodeRunInput;
    },
    actors: {
      streamingActor: streamingActorSlot,
    },
  }).createMachine({
    id: 'nodeRun',
    initial: 'idle',
    // Read nodeId from spawn input. The factory arg is the fallback only
    // for tests that instantiate the machine directly. Reading from the
    // factory arg here would freeze nodeId at machine-creation time
    // (one shared value across every spawned child) — the bug visible in
    // the Stately Inspector as `Context: {nodeId: "unknown"}`.
    context: ({ input }) => ({ nodeId: input?.nodeId ?? defaultNodeId }),
    states: {
      idle: {
        on: {
          RUN: {
            target: 'queued',
            actions: assign({ jobId: ({ event }) => event.jobId }),
          },
          // Upstream may fail before this node even queues — e.g. the
          // user clicks Run and the first node fails before subsequent
          // nodes are queued by Snakemake.
          UPSTREAM_FAILED: {
            target: 'cancelled.becauseUpstream',
            actions: assign({
              upstreamNodeId: ({ event }) => event.upstreamNodeId,
              upstreamRunId: ({ event }) => event.upstreamRunId,
            }),
          },
          USER_STOP: { target: 'cancelled.becauseUser' },
          STALE: {
            target: 'stale',
            actions: assign({
              dependencyChange: ({ event }) => event.dependencyChange,
            }),
          },
          CACHE_HIT: {
            target: 'cached',
            actions: assign({
              cacheKey: ({ event }) => event.cacheKey,
              originalRunId: ({ event }) => event.originalRunId,
            }),
          },
        },
      },

      queued: {
        on: {
          HEARTBEAT: {
            target: 'running',
            actions: assign({
              runId: ({ event }) => event.runId,
              tally: ({ event, context }) => event.tally ?? context.tally,
            }),
          },
          // Upstream can fail before this node ever receives a heartbeat.
          UPSTREAM_FAILED: {
            target: 'cancelled.becauseUpstream',
            actions: assign({
              upstreamNodeId: ({ event }) => event.upstreamNodeId,
              upstreamRunId: ({ event }) => event.upstreamRunId,
            }),
          },
          USER_STOP: { target: 'cancelled.becauseUser' },
          CACHE_HIT: {
            target: 'cached',
            actions: assign({
              cacheKey: ({ event }) => event.cacheKey,
              originalRunId: ({ event }) => event.originalRunId,
            }),
          },
        },
      },

      running: {
        // Spawning the streaming child is scoped to this state — leaving
        // `running` (to terminal/cancelled/orphaned) tears it down. The
        // streaming machine consumes `runId` from input and auto-connects;
        // InspectorPanel reads its snapshot via `parentSnap.children.streaming`.
        invoke: {
          id: 'streaming',
          src: 'streamingActor',
          input: ({ context }) => ({
            runId: context.runId ?? '',
            fullMode: false,
          }),
        },
        on: {
          TALLY: {
            actions: assign({ tally: ({ event }) => event.tally }),
          },
          HEARTBEAT: {
            // Late heartbeats just refresh tally; runId is already pinned.
            actions: assign({
              tally: ({ event, context }) => event.tally ?? context.tally,
            }),
          },
          RUN_OK: [
            {
              target: 'completed_with_failures',
              guard: ({ event }) => (event.tally?.failed ?? 0) > 0,
              actions: assign({
                finalTally: ({ event }) => event.tally,
                error_message: ({ event }) => event.error_message,
              }),
            },
            {
              target: 'succeeded',
              actions: assign({ finalTally: ({ event }) => event.tally }),
            },
          ],
          RUN_FAILED: {
            target: 'failed',
            actions: assign({
              error_message: ({ event }) => event.error_message,
              error_traceback: ({ event }) => event.error_traceback,
            }),
          },
          UPSTREAM_FAILED: {
            target: 'cancelled.becauseUpstream',
            actions: assign({
              upstreamNodeId: ({ event }) => event.upstreamNodeId,
              upstreamRunId: ({ event }) => event.upstreamRunId,
            }),
          },
          USER_STOP: { target: 'cancelled.becauseUser' },
          ORPHAN_DETECTED: {
            target: 'orphaned',
            actions: assign({
              pipelineStderrTail: ({ event }) => event.pipelineStderrTail,
            }),
          },
          // ADR-015 Phase D Bug 4 Path B: defense-in-depth.  If a
          // future polling-service refactor lets a HEARTBEAT slip
          // through before the cache-hit signal arrives, this still
          // routes the node to `cached` and tears down the spawned
          // streaming child via xstate v5 invoke exit semantics.
          CACHE_HIT: {
            target: 'cached',
            actions: assign({
              cacheKey: ({ event }) => event.cacheKey,
              originalRunId: ({ event }) => event.originalRunId,
            }),
          },
        },
      },

      succeeded: {
        type: 'final',
      },
      completed_with_failures: {
        type: 'final',
      },
      failed: {
        type: 'final',
      },
      cancelled: {
        // xstate v5 requires `initial` on every compound state, even
        // if every transition targets a specific substate explicitly
        // (no path enters bare `cancelled`). becauseUser is the
        // defensive default.
        initial: 'becauseUser',
        states: {
          becauseUpstream: { type: 'final' },
          becauseUser: { type: 'final' },
        },
      },
      orphaned: {
        type: 'final',
      },
      cached: {
        type: 'final',
      },
      stale: {
        type: 'final',
      },
    },
  });
}

export type NodeRunActor = ActorRefFrom<ReturnType<typeof makeNodeRunMachine>>;
