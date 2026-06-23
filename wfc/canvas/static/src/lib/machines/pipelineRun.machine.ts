/**
 * Top-level pipeline run machine.
 *
 *   idle → preflight → submitting → polling → done
 *
 * Owns the Run button, spawns one `nodeRunActor` per canvas node, and
 * dispatches typed events received from the polling service into the
 * right child. The `RUN_CLICKED` event only has a transition out of
 * `idle`, which gives us free double-click protection (the second
 * click is silently dropped — visible in the Stately Inspector as a
 * rejected event).
 *
 * Backend integration lives in `services.ts`:
 *   - `submitPipeline` (fromPromise: validate + run)
 *   - `pollNodeStatus` (fromCallback: emits typed NODE_* events)
 */
import {
  setup,
  assign,
  fromCallback,
  fromPromise,
  type ActorRefFrom,
} from 'xstate';
import { makeNodeRunMachine, type NodeRunActor } from './nodeRun.machine';
import type { RunTally, PipelineError } from '../types';

// ── Input / Context ────────────────────────────────────────────────────

export interface PipelineNodeRef {
  id: string;
  // System nodes (input_selector, run_reference) have no per-node
  // lifecycle — the backend never emits HEARTBEAT/RUN_OK for them.
  // Spawning a nodeRunActor for one would strand it in `queued` forever.
  type?: string;
}

// Pipeline shape carried on RUN_CLICKED. Mirrors `PipelineJSON` enough
// for the actor to spawn one child per node and pass the full payload
// to `submitPipeline`. Untyped tail so callers can include
// validate-time fields without fighting the type system.
export type PipelinePayload = {
  nodes: PipelineNodeRef[];
  [k: string]: unknown;
};

export interface PipelineRunInput {
  pipeline: PipelinePayload;
}

export interface PipelineRunContext {
  pipeline: PipelinePayload;
  jobId: string | null;
  pipelineError: PipelineError | null;
  // Spawned per-node children, keyed by nodeId. Components subscribe
  // to these directly (CustomNode, InspectorPanel).
  nodeRefs: Record<string, NodeRunActor>;
  // Bumped on each entry to `preflight`. Suffixed onto child IDs so
  // multiple runs in one session show as distinct actors in the Stately
  // Inspector instead of all collapsing under `node:foo`.
  runCount: number;
}

// ── Events ─────────────────────────────────────────────────────────────
//
// Two "shapes" of events:
//
//   1. UI events (RUN_CLICKED, USER_STOP, RESET) — sent by Toolbar etc.
//   2. NODE_* events from the polling service — fanned out to children.

export type PipelineRunEvent =
  | { type: 'RUN_CLICKED'; pipeline?: PipelinePayload }
  | { type: 'USER_STOP' }
  | { type: 'RESET' }
  | { type: 'PIPELINE_ERROR'; error: PipelineError }
  | { type: 'PIPELINE_DONE' }
  | {
      type: 'NODE_HEARTBEAT';
      nodeId: string;
      runId: string;
      tally?: RunTally;
    }
  | { type: 'NODE_TALLY'; nodeId: string; tally: RunTally }
  // `error_message` only set when the polling bridge maps a `mixed`
  // backend status (some samples failed). Forwarded to the child so
  // `completed_with_failures.context.error_message` is populated.
  | {
      type: 'NODE_RUN_OK';
      nodeId: string;
      tally: RunTally;
      error_message?: string;
    }
  | {
      type: 'NODE_RUN_FAILED';
      nodeId: string;
      runId: string;
      error_message: string;
      error_traceback?: string;
    }
  | {
      type: 'NODE_UPSTREAM_FAILED';
      nodeId: string;
      upstreamNodeId: string;
      upstreamRunId: string;
    }
  | { type: 'NODE_USER_STOP'; nodeId: string }
  | {
      type: 'NODE_ORPHAN_DETECTED';
      nodeId: string;
      pipelineStderrTail?: string;
    }
  | {
      type: 'NODE_CACHE_HIT';
      nodeId: string;
      cacheKey: string;
      originalRunId?: string;
    }
  | { type: 'NODE_STALE'; nodeId: string; dependencyChange?: string };

// ── Service slots ──────────────────────────────────────────────────────
//
// Real implementations live in `services.ts`. Tests provide stubs that
// resolve immediately or are driven by a sendBack handle.

const submitPipelineSlot = fromPromise<{ jobId: string }, unknown>(async () => ({
  jobId: 'unset',
}));

const pollNodeStatusSlot = fromCallback(() => () => {});

// `streamingActor` slot — the spawned `nodeRunActor` invokes this
// inside its `running` state. We forward the slot here so `.provide`
// at this level cascades to children created via `spawnChild`.
const streamingActorSlot = fromCallback(() => () => {});

// `awaitAllCommitted` slot — the preflight state invokes this to fan
// COMMIT out to every registered paramEditorActor / variantActor and
// await the aggregator's `allCommitted` final-shaped state. Real
// implementation lives in `root.ts` (wraps the singleton aggregator
// + a Promise that resolves on transition). Default is a no-op so
// tests don't need a stub when they don't care about the gate.
const awaitAllCommittedSlot = fromPromise<void, unknown>(async () => {});

// ── Machine ────────────────────────────────────────────────────────────

export function makePipelineRunMachine() {
  return setup({
    types: {} as {
      context: PipelineRunContext;
      events: PipelineRunEvent;
      input: PipelineRunInput;
    },
    actors: {
      submitPipeline: submitPipelineSlot,
      pollNodeStatus: pollNodeStatusSlot,
      streamingActor: streamingActorSlot,
      // Re-declared so spawnChild can resolve it. The factory is the
      // src; .provide cascades the streamingActor override to children.
      nodeRunActor: makeNodeRunMachine(),
      awaitAllCommitted: awaitAllCommittedSlot,
    },
    actions: {
      forwardToNode: ({ context, event }) => {
        // Pure dispatch helper — translates polling events into the
        // child's event shape and sends. Children that have already
        // moved to a terminal state silently drop unknown transitions.
        const e = event as PipelineRunEvent;
        const nodeId = (e as { nodeId?: string }).nodeId;
        if (!nodeId) return;
        const child = context.nodeRefs[nodeId];
        if (!child) return;
        switch (e.type) {
          case 'NODE_HEARTBEAT':
            child.send({ type: 'HEARTBEAT', runId: e.runId, tally: e.tally });
            break;
          case 'NODE_TALLY':
            child.send({ type: 'TALLY', tally: e.tally });
            break;
          case 'NODE_RUN_OK':
            child.send({
              type: 'RUN_OK',
              tally: e.tally,
              error_message: e.error_message,
            });
            break;
          case 'NODE_RUN_FAILED':
            child.send({
              type: 'RUN_FAILED',
              error_message: e.error_message,
              error_traceback: e.error_traceback,
            });
            break;
          case 'NODE_UPSTREAM_FAILED':
            child.send({
              type: 'UPSTREAM_FAILED',
              upstreamNodeId: e.upstreamNodeId,
              upstreamRunId: e.upstreamRunId,
            });
            break;
          case 'NODE_USER_STOP':
            child.send({ type: 'USER_STOP' });
            break;
          case 'NODE_ORPHAN_DETECTED':
            child.send({
              type: 'ORPHAN_DETECTED',
              pipelineStderrTail: e.pipelineStderrTail,
            });
            break;
          case 'NODE_CACHE_HIT':
            child.send({
              type: 'CACHE_HIT',
              cacheKey: e.cacheKey,
              originalRunId: e.originalRunId,
            });
            break;
          case 'NODE_STALE':
            child.send({
              type: 'STALE',
              dependencyChange: e.dependencyChange,
            });
            break;
        }
      },
    },
  }).createMachine({
    id: 'pipelineRun',
    initial: 'idle',
    context: ({ input }) => ({
      pipeline: input?.pipeline ?? { nodes: [] },
      jobId: null,
      pipelineError: null,
      nodeRefs: {},
      runCount: 0,
    }),
    on: {
      // RESET tears down children and goes back to idle. Used when the
      // user clicks Clear or imports a new pipeline.
      RESET: {
        target: '.idle',
        actions: assign({
          jobId: () => null,
          pipelineError: () => null,
          // Drop references — xstate v5 auto-stops orphaned spawned
          // children when they fall out of context. Calling `.stop()`
          // on a non-root spawned actor throws ("A non-root actor
          // cannot be stopped directly") which would silently revert
          // this transition mid-action.
          nodeRefs: () => ({}),
        }),
      },
      // Top-level USER_STOP so a click during preflight/submitting (the
      // sub-second window before polling starts) reaches `done` instead
      // of being silently dropped. The Toolbar only renders Stop while
      // `runState.running` is true, so USER_STOP from idle/done is not
      // a real path — the harmless self-transition costs nothing.
      USER_STOP: {
        target: '.done',
        actions: ({ context }) => {
          for (const ref of Object.values(context.nodeRefs)) {
            ref.send({ type: 'USER_STOP' });
          }
        },
      },
    },
    states: {
      idle: {
        on: {
          RUN_CLICKED: {
            target: 'preflight',
            actions: assign({
              pipeline: ({ context, event }) =>
                event.pipeline ?? context.pipeline,
            }),
          },
        },
      },

      preflight: {
        // Spawn a per-node actor for every canvas node, then await the
        // param-editor aggregator's `allCommitted` transition. The
        // invoke replaces the legacy `requestCommitAll() + setTimeout(0)`
        // microtask race (0.2.8) with a typed transition: the
        // pipelineRunActor stays in `preflight` until every editing
        // paramEditor / variant child has reached a settled state.
        entry: [
          assign({
            runCount: ({ context }) => context.runCount + 1,
          }),
          assign({
            nodeRefs: ({ context, spawn }) => {
              // Drop the prior-run refs — xstate v5 auto-stops spawned
              // children once they're no longer referenced in context.
              // Calling `.stop()` on a non-root spawned actor throws
              // ("A non-root actor cannot be stopped directly") and
              // would abort this entry action mid-flight, leaving the
              // machine wedged in `done` and the RUN_CLICKED transition
              // visibly dropped (no preflight snapshot in the inspector).
              void context;
              const next: Record<string, NodeRunActor> = {};
              for (const n of context.pipeline.nodes) {
                // System nodes (input_selector, run_reference) get no
                // lifecycle actor — they have no backend run, so the
                // actor would never leave `queued`.
                if (n.type !== undefined && n.type !== 'method') continue;
                const child = spawn('nodeRunActor', {
                  id: `node:${n.id}#${context.runCount}`,
                  input: { nodeId: n.id },
                }) as unknown as NodeRunActor;
                next[n.id] = child;
              }
              return next;
            },
          }),
        ],
        invoke: {
          id: 'awaitCommit',
          src: 'awaitAllCommitted',
          onDone: { target: 'submitting' },
          // If the aggregator stub rejects (shouldn't happen — the real
          // one resolves via subscription), fall through to submitting
          // anyway. The rest of preflight is structural; the worst case
          // is a row submits with stale value, which is no worse than
          // the legacy behavior. Log a diagnostic so future debugging
          // has a trace when the await stub rejects (matches the
          // console.warn convention used elsewhere — historyApi.ts).
          onError: {
            target: 'submitting',
            actions: ({ event }) => {
              const err = (event as { error?: unknown }).error;
              // eslint-disable-next-line no-console
              console.warn(
                '[pipelineRun] preflight awaitAllCommitted rejected; ' +
                  'falling through to submitting with possibly-stale ' +
                  'param values:',
                err,
              );
            },
          },
        },
      },

      submitting: {
        invoke: {
          id: 'submit',
          src: 'submitPipeline',
          input: ({ context }) => ({ pipeline: context.pipeline }),
          onDone: {
            target: 'polling',
            actions: [
              assign({
                jobId: ({ event }) => event.output.jobId,
              }),
              // Move every spawned child out of `idle` into `queued` by
              // sending RUN with the resolved jobId. Without this, the
              // children stay in `idle` for the entire run — `idle` does
              // not handle HEARTBEAT/RUN_OK/RUN_FAILED/TALLY, so polling
              // events would silently drop. The single-source bridge in
              // `root.ts` writes `data.runStatus` from the actor's value;
              // moving children to `queued` is what makes the bridge
              // surface real lifecycle states (pending/running/etc.)
              // instead of a permanent `idle`.
              ({ context, event }) => {
                const jobId = event.output.jobId;
                for (const ref of Object.values(context.nodeRefs)) {
                  ref.send({ type: 'RUN', jobId });
                }
              },
            ],
          },
          onError: {
            target: 'idle',
            actions: assign({
              pipelineError: ({ event }) => {
                // SubmitError (services.ts) carries a populated `.message`
                // — preserve it verbatim so the pipeline-error-banner
                // shows the validate error string instead of the
                // `[object Object]` produced by `String(err)` on a plain
                // object.
                const err = (event as { error?: unknown }).error;
                const message =
                  err && typeof err === 'object' && 'message' in err &&
                  typeof (err as { message: unknown }).message === 'string'
                    ? (err as { message: string }).message
                    : String(err ?? 'submit failed');
                return { message, kind: 'unknown' };
              },
            }),
          },
        },
      },

      polling: {
        invoke: {
          id: 'poll',
          src: 'pollNodeStatus',
          input: ({ context }) => ({
            jobId: context.jobId,
            nodeIds: context.pipeline.nodes.map(n => n.id),
          }),
        },
        on: {
          NODE_HEARTBEAT: { actions: { type: 'forwardToNode' } },
          NODE_TALLY: { actions: { type: 'forwardToNode' } },
          NODE_RUN_OK: { actions: { type: 'forwardToNode' } },
          NODE_RUN_FAILED: { actions: { type: 'forwardToNode' } },
          NODE_UPSTREAM_FAILED: { actions: { type: 'forwardToNode' } },
          NODE_USER_STOP: { actions: { type: 'forwardToNode' } },
          NODE_ORPHAN_DETECTED: { actions: { type: 'forwardToNode' } },
          NODE_CACHE_HIT: { actions: { type: 'forwardToNode' } },
          NODE_STALE: { actions: { type: 'forwardToNode' } },
          PIPELINE_ERROR: {
            actions: assign({
              pipelineError: ({ event }) => event.error,
            }),
          },
          PIPELINE_DONE: { target: 'done' },
          // USER_STOP is handled at top-level — see machine-level `on:`.
        },
      },

      done: {
        on: {
          RUN_CLICKED: {
            target: 'preflight',
            actions: assign({
              pipeline: ({ context, event }) =>
                event.pipeline ?? context.pipeline,
            }),
          },
        },
      },
    },
  });
}

export type PipelineRunActor = ActorRefFrom<ReturnType<typeof makePipelineRunMachine>>;
