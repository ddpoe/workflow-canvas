/**
 * ADR-015 Phase D Layer 2: shared timeline fixtures.
 *
 * Each timeline is an ordered sequence of `{ delayMs, payload }`
 * entries. The Playwright route-replay helper (`route-replay.ts`)
 * intercepts `/api/workflow/status/:jobId` and serves the next entry's
 * `payload` after `delayMs` has elapsed since the previous tick.
 *
 * The payload type is the **generated** `WorkflowStatusResponse` from
 * `src/lib/types/api.ts`, so any backend rename/removal of a field
 * makes these fixtures fail tsc — the contract guarantee called out in
 * US-1 of the cycle pitch.
 */
import type { components } from '../types/api';
import type { RouteReplayOptions, SSEStreamFixture } from './route-replay';
import type { RunStatusEvent } from '../machines/services';

// ADR-015 Phase D Pass 2: recorded SSE stream fixtures.  Each JSON file
// is produced by `wfc/canvas/static/scripts/record-sse.ts` against the
// dev server.  Imported here as JSON so vitest + Playwright can both
// consume them via the shared catalog.
import streamingFixture from '../../../tests/e2e/fixtures/sse-streams/streaming.json' with { type: 'json' };
import streamingLongFixture from '../../../tests/e2e/fixtures/sse-streams/streaming-long.json' with { type: 'json' };
import faultOnStreamFixture from '../../../tests/e2e/fixtures/sse-streams/fault-on-stream.json' with { type: 'json' };
import faultMidStreamFixture from '../../../tests/e2e/fixtures/sse-streams/fault-mid-stream.json' with { type: 'json' };
import streamingCancelledFixture from '../../../tests/e2e/fixtures/sse-streams/streaming-cancelled.json' with { type: 'json' };

export type WorkflowStatusResponse =
  components['schemas']['WorkflowStatusResponse'];

export interface TimelineFrame {
  delayMs: number;
  payload: WorkflowStatusResponse;
}

export type Timeline = TimelineFrame[];

/**
 * ADR-015 Phase D Pass 1: behavior catalog row.
 *
 * One entry per polling-driven row of the bug-class table.  Both
 * test layers iterate the same catalog object — Vitest feeds the
 * timeline frames through `runStatusToNodeState` and asserts the
 * emitted event sequence matches `expectedEvents`; Playwright sets
 * up route-replay against `timeline` (with `routeOptions` overrides
 * for pre-run rejection rows), navigates with `?fixture=<fixtureKey>`,
 * and asserts on rendered DOM (per-row assertion logic stays in
 * `behaviors.spec.ts` — keeps Playwright imports out of this file).
 */
export interface BehaviorRow {
  /** Stable name; matches the catalog key.  Used as PNG filename and test id. */
  name: string;
  /**
   * Canvas seed key consumed by `App.svelte::seedFixture`.  Determines
   * how many nodes (and what kind) the canvas mounts before run.
   */
  fixtureKey: string;
  /** Polling timeline.  May be empty for pre-run rejection rows. */
  timeline: Timeline;
  /**
   * Optional per-row Playwright route overrides.  Used by `zeroJobDAG`
   * to make `/api/workflow/validate` return 200 + `{valid: false, ...}`.
   */
  routeOptions?: RouteReplayOptions;
  /**
   * Per-frame, per-node-key array of expected events.  Each frame's
   * inner array iterates that frame's `node_states` in `Object.keys`
   * order and contains the event `runStatusToNodeState` should emit
   * for each entry (or `null` for "no event").  `services.behaviors.test.ts`
   * deep-equals against this.  Omit for rows that skip the bridge layer
   * (e.g. `zeroJobDAG`).
   */
  expectedEvents?: (RunStatusEvent | null)[][];
  /**
   * When true, the parameterized Vitest test calls `it.skip` for this
   * row — used by `zeroJobDAG` (the bridge is never reached because
   * polling never starts).
   */
  skipVitest?: boolean;
  /**
   * ADR-015 Phase D Pass 2: optional recorded SSE stream replayed by
   * the e2e test against `/api/wfc/run/<runId>/stream-logs`.  When set,
   * `route-replay.ts` swaps the default Pass 1 single-frame stub for a
   * delayMs-paced replay.  Pass 1 polling-only rows omit this.
   */
  sseStream?: SSEStreamFixture;
}

// Helper: build a base response of the right shape so each frame stays
// readable.  Keeps the noise out of individual timelines.
function frame(
  overall: string,
  node_states: WorkflowStatusResponse['node_states'],
): WorkflowStatusResponse {
  return {
    job_id: 'job-fixture',
    overall_status: overall,
    steps: {},
    node_states,
    thread_alive: overall === 'running' || overall === 'pending',
    log: '',
    error: null,
  };
}

// US-5 (Bug 3): a single method node walks the happy path
// pending -> running -> completed.  The `running` frame must be
// observable in the UI; the existing 150ms setTimeout defer in
// `services.ts` covers that today.
export const normalSucceededTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: { status: 'pending' },
    }),
  },
  {
    delayMs: 80,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
    }),
  },
  {
    delayMs: 80,
    payload: frame('completed', {
      method_a: {
        status: 'completed',
        run_ids: ['run-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
    }),
  },
];

// US-6 + US-7 (Bug 4 + Bug 5): cache-hit run.  The backend reports
// `status: 'completed'` plus the cache-hit fields on the very first
// tick; the bridge must short-circuit to CACHE_HIT and the
// InspectorPanel must render the cache-hit banner with `originalRunId`
// instead of stranding on "Connecting…".
export const cacheHitTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('completed', {
      method_a: {
        status: 'completed',
        run_ids: ['run-cache-1'],
        tally: { running: 0, completed: 1, failed: 0 },
        cache_hit: true,
        original_run_id: 'run-original-42',
        cache_key: 'cache-key-abc',
      },
    }),
  },
];

// US-3 (Bug 1): two method nodes with different cadence so a single
// observable tick shows them in distinct statuses (one running, one
// still pending), then both reach distinct terminal states.
export const multiNodeTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: { status: 'pending' },
      method_b: { status: 'pending' },
    }),
  },
  {
    delayMs: 80,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-a-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
      method_b: { status: 'pending' },
    }),
  },
  {
    delayMs: 80,
    payload: frame('running', {
      method_a: {
        status: 'completed',
        run_ids: ['run-a-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
      method_b: {
        status: 'running',
        run_ids: ['run-b-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
    }),
  },
  {
    delayMs: 80,
    payload: frame('failed', {
      method_a: {
        status: 'completed',
        run_ids: ['run-a-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
      method_b: {
        status: 'failed',
        run_ids: ['run-b-1'],
        tally: { running: 0, completed: 0, failed: 1 },
        error: 'method_b crashed',
      },
    }),
  },
];

// US-4 (Bug 2): one method node + one system (input_selector) node.
// The status endpoint only includes method nodes in `node_states`, so
// the system node's silence is the absence of any status entry — the
// canvas must NOT decorate it.
export const systemNodeTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_only: { status: 'pending' },
    }),
  },
  {
    delayMs: 80,
    payload: frame('running', {
      method_only: {
        status: 'running',
        run_ids: ['run-x-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
    }),
  },
  {
    delayMs: 80,
    payload: frame('completed', {
      method_only: {
        status: 'completed',
        run_ids: ['run-x-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
    }),
  },
];

// ── ADR-015 Phase D Pass 1: behavior catalog timelines ─────────────────
//
// Eight new named behaviors covering the polling-driven rows of the
// bug-class table.  Each fixture is pinned to the actual backend shape
// (verified against `wfc/canvas/server.py::get_workflow_status`):
// per-node `error` is a single STRING, cancelled rows carry
// `upstream_node_id`/`upstream_run_id`/`cancelled_due_to_run_id`,
// `mixed` aggregate keeps the error from the failed sample.

// Row 1: cancelledByUpstreamFailure — A fails, B cancelled because of A.
// Bridge maps cancelled+upstream_node_id to UPSTREAM_FAILED;
// nodeRun.machine routes that to `cancelled.becauseUpstream`;
// InspectorPanel renders `causality-banner[data-banner-kind="upstream"]`.
export const cancelledByUpstreamFailureTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: { status: 'pending' },
      method_b: { status: 'pending' },
    }),
  },
  {
    delayMs: 80,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-a-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
      method_b: { status: 'pending' },
    }),
  },
  {
    delayMs: 120,
    payload: frame('failed', {
      method_a: {
        status: 'failed',
        run_ids: ['run-a-1'],
        tally: { running: 0, completed: 0, failed: 1 },
        error: 'method_a crashed',
      },
      method_b: {
        status: 'cancelled',
        upstream_node_id: 'method_a',
        upstream_run_id: 'run-a-1',
        cancelled_due_to_run_id: 'run-a-1',
      },
    }),
  },
];

// Row 2: failedWithTraceback — single per-node `error` STRING containing
// a traceback-shaped multi-line message (the actual shipped backend
// shape; see D-2 in the cycle decisions log).  NOT `error_message` +
// `error_traceback` per-node — those flow only via SSE.
const TRACEBACK_TEXT =
  'Traceback (most recent call last):\n' +
  '  File "wfc/methods/method_a/run.py", line 42, in main\n' +
  '    raise ValueError("bad input column")\n' +
  'ValueError: bad input column';

export const failedWithTracebackTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: { status: 'pending' },
    }),
  },
  {
    delayMs: 80,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
    }),
  },
  {
    delayMs: 120,
    payload: frame('failed', {
      method_a: {
        status: 'failed',
        run_ids: ['run-1'],
        tally: { running: 0, completed: 0, failed: 1 },
        error: TRACEBACK_TEXT,
        error_run_id: 'run-1',
        error_sample: 'sample-001',
      },
    }),
  },
];

// Row 3: tallyProgression — running with the tally counter advancing
// across three frames.  Each TALLY event carries a distinct counter
// shape; the trajectory test asserts the sequence of counts is
// observed in order rather than using last-DOM-wins.
export const tallyProgressionTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-1'],
        tally: { running: 5, completed: 0, failed: 0 },
      },
    }),
  },
  {
    delayMs: 150,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-1'],
        tally: { running: 3, completed: 2, failed: 0 },
      },
    }),
  },
  {
    delayMs: 150,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-1'],
        tally: { running: 1, completed: 4, failed: 0 },
      },
    }),
  },
  {
    delayMs: 150,
    payload: frame('completed', {
      method_a: {
        status: 'completed',
        run_ids: ['run-1'],
        tally: { running: 0, completed: 5, failed: 0 },
      },
    }),
  },
];

// Row 4: queuedBehindRunning — a single observable tick where A is
// running and B is still pending.  Distinct from the existing
// `multiNodeTimeline` in that the trajectory test asserts on the
// pending/running co-occurrence rather than terminal divergence.
export const queuedBehindRunningTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: { status: 'pending' },
      method_b: { status: 'pending' },
    }),
  },
  {
    delayMs: 100,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-a-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
      method_b: { status: 'pending' },
    }),
  },
  {
    delayMs: 200,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-a-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
      method_b: { status: 'pending' },
    }),
  },
  {
    delayMs: 200,
    payload: frame('completed', {
      method_a: {
        status: 'completed',
        run_ids: ['run-a-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
      method_b: {
        status: 'completed',
        run_ids: ['run-b-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
    }),
  },
];

// Row 5: errorMidGraph — three-node A->B->C: A completes, B fails with
// an error string, C is cancelled because of B.  Three substates
// rendering distinctly in a single timeline.
export const errorMidGraphTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: { status: 'pending' },
      method_b: { status: 'pending' },
      method_c: { status: 'pending' },
    }),
  },
  {
    delayMs: 100,
    payload: frame('running', {
      method_a: {
        status: 'completed',
        run_ids: ['run-a-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
      method_b: {
        status: 'running',
        run_ids: ['run-b-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
      method_c: { status: 'pending' },
    }),
  },
  {
    delayMs: 200,
    payload: frame('failed', {
      method_a: {
        status: 'completed',
        run_ids: ['run-a-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
      method_b: {
        status: 'failed',
        run_ids: ['run-b-1'],
        tally: { running: 0, completed: 0, failed: 1 },
        error: 'method_b raised RuntimeError',
      },
      method_c: {
        status: 'cancelled',
        upstream_node_id: 'method_b',
        upstream_run_id: 'run-b-1',
        cancelled_due_to_run_id: 'run-b-1',
      },
    }),
  },
];

// Row 6: mixedStatus — one method node lands in `mixed` aggregate
// (some completed, some failed).  Backend bridges `mixed` -> RUN_OK
// with a tally whose `failed > 0`; nodeRun.machine routes that
// through the guarded `RUN_OK` -> `completed_with_failures` branch
// (nodeRun.machine.ts#L203-208).  InspectorPanel reads
// `error_message` from context for both `failed` and
// `completed_with_failures` (InspectorPanel.svelte#L202).  Pin the
// fixture to carry the per-node `error` from the failed sample, as
// `wfc/canvas/server.py::get_workflow_status` does (L1607-1611).
export const mixedStatusTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: { status: 'pending' },
    }),
  },
  {
    delayMs: 100,
    payload: frame('running', {
      method_a: {
        status: 'running',
        run_ids: ['run-1'],
        tally: { running: 4, completed: 0, failed: 0 },
      },
    }),
  },
  {
    delayMs: 200,
    payload: frame('running', {
      method_a: {
        status: 'mixed',
        run_ids: ['run-1'],
        tally: { running: 0, completed: 3, failed: 1 },
        error: 'sample-007 raised KeyError',
        error_run_id: 'run-1',
        error_sample: 'sample-007',
      },
    }),
  },
];

// Row 7: partialCacheHit — two nodes: A completes via cache hit on
// frame 0, B walks pending->running->completed normally.  A renders
// the cache-hit banner (no streaming actor); B is a normal trajectory.
export const partialCacheHitTimeline: Timeline = [
  {
    delayMs: 0,
    payload: frame('running', {
      method_a: {
        status: 'completed',
        run_ids: ['run-cache-a'],
        tally: { running: 0, completed: 1, failed: 0 },
        cache_hit: true,
        original_run_id: 'run-original-a-7',
        cache_key: 'cache-key-a',
      },
      method_b: { status: 'pending' },
    }),
  },
  {
    delayMs: 100,
    payload: frame('running', {
      method_a: {
        status: 'completed',
        run_ids: ['run-cache-a'],
        tally: { running: 0, completed: 1, failed: 0 },
        cache_hit: true,
        original_run_id: 'run-original-a-7',
        cache_key: 'cache-key-a',
      },
      method_b: {
        status: 'running',
        run_ids: ['run-b-1'],
        tally: { running: 1, completed: 0, failed: 0 },
      },
    }),
  },
  {
    delayMs: 200,
    payload: frame('completed', {
      method_a: {
        status: 'completed',
        run_ids: ['run-cache-a'],
        tally: { running: 0, completed: 1, failed: 0 },
        cache_hit: true,
        original_run_id: 'run-original-a-7',
        cache_key: 'cache-key-a',
      },
      method_b: {
        status: 'completed',
        run_ids: ['run-b-1'],
        tally: { running: 0, completed: 1, failed: 0 },
      },
    }),
  },
];

// Row 8: zeroJobDAG — pre-run rejection.  The Playwright spec's
// `routeOptions` makes `/api/workflow/validate` return 200 with
// `{valid: false, errors: [...]}`; the canvas surfaces the validation
// error and never calls `/run`, so the polling timeline is never
// consumed.  Vitest skips this row (the bridge is never reached).
export const zeroJobDAGTimeline: Timeline = [];

// ── Catalog map ────────────────────────────────────────────────────────
//
// Single source of truth iterated by both new test files.  Per-row
// `expectedEvents` shape: outer array is one entry per timeline frame;
// inner array is one entry per `Object.keys(node_states)` entry in that
// frame, in iteration order — matching how `services.behaviors.test.ts`
// loops.

export const behaviorCatalog: Record<string, BehaviorRow> = {
  cancelledByUpstreamFailure: {
    name: 'cancelledByUpstreamFailure',
    fixtureKey: 'two-methods-cancel',
    timeline: cancelledByUpstreamFailureTimeline,
    expectedEvents: [
      // Frame 0: both pending — no events.
      [null, null],
      // Frame 1: A running w/ tally -> TALLY; B still pending -> null.
      [
        { type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } },
        null,
      ],
      // Frame 2: A failed -> RUN_FAILED; B cancelled w/ upstream -> UPSTREAM_FAILED.
      [
        { type: 'RUN_FAILED', error_message: 'method_a crashed' },
        {
          type: 'UPSTREAM_FAILED',
          upstreamNodeId: 'method_a',
          upstreamRunId: 'run-a-1',
        },
      ],
    ],
  },
  failedWithTraceback: {
    name: 'failedWithTraceback',
    fixtureKey: 'single-method',
    timeline: failedWithTracebackTimeline,
    expectedEvents: [
      [null], // pending
      [{ type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } }],
      [{ type: 'RUN_FAILED', error_message: TRACEBACK_TEXT }],
    ],
  },
  tallyProgression: {
    name: 'tallyProgression',
    fixtureKey: 'single-method',
    timeline: tallyProgressionTimeline,
    expectedEvents: [
      [{ type: 'TALLY', tally: { running: 5, completed: 0, failed: 0 } }],
      [{ type: 'TALLY', tally: { running: 3, completed: 2, failed: 0 } }],
      [{ type: 'TALLY', tally: { running: 1, completed: 4, failed: 0 } }],
      [
        {
          type: 'RUN_OK',
          tally: { running: 0, completed: 5, failed: 0 },
        },
      ],
    ],
  },
  queuedBehindRunning: {
    name: 'queuedBehindRunning',
    fixtureKey: 'two-methods',
    timeline: queuedBehindRunningTimeline,
    expectedEvents: [
      [null, null],
      [
        { type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } },
        null,
      ],
      [
        { type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } },
        null,
      ],
      [
        {
          type: 'RUN_OK',
          tally: { running: 0, completed: 1, failed: 0 },
        },
        {
          type: 'RUN_OK',
          tally: { running: 0, completed: 1, failed: 0 },
        },
      ],
    ],
  },
  errorMidGraph: {
    name: 'errorMidGraph',
    fixtureKey: 'three-methods-chain',
    timeline: errorMidGraphTimeline,
    expectedEvents: [
      [null, null, null],
      [
        {
          type: 'RUN_OK',
          tally: { running: 0, completed: 1, failed: 0 },
        },
        { type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } },
        null,
      ],
      [
        {
          type: 'RUN_OK',
          tally: { running: 0, completed: 1, failed: 0 },
        },
        { type: 'RUN_FAILED', error_message: 'method_b raised RuntimeError' },
        {
          type: 'UPSTREAM_FAILED',
          upstreamNodeId: 'method_b',
          upstreamRunId: 'run-b-1',
        },
      ],
    ],
  },
  mixedStatus: {
    name: 'mixedStatus',
    fixtureKey: 'single-method',
    timeline: mixedStatusTimeline,
    expectedEvents: [
      [null], // pending
      [{ type: 'TALLY', tally: { running: 4, completed: 0, failed: 0 } }],
      // mixed -> RUN_OK with tally.failed=1 + error_message from the
      // failed sample.  nodeRun guard routes to completed_with_failures
      // and assigns error_message into context, which the Inspector's
      // node-error-box reads via the same derivation as `failed`
      // (InspectorPanel.svelte L196-204).
      [
        {
          type: 'RUN_OK',
          tally: { running: 0, completed: 3, failed: 1 },
          error_message: 'sample-007 raised KeyError',
        },
      ],
    ],
  },
  partialCacheHit: {
    name: 'partialCacheHit',
    fixtureKey: 'two-methods',
    timeline: partialCacheHitTimeline,
    expectedEvents: [
      // Frame 0: A cache-hit short-circuit -> CACHE_HIT; B pending -> null.
      [
        {
          type: 'CACHE_HIT',
          cacheKey: 'cache-key-a',
          originalRunId: 'run-original-a-7',
        },
        null,
      ],
      // Frame 1: A still cache-hit (sticky); B running -> TALLY.
      [
        {
          type: 'CACHE_HIT',
          cacheKey: 'cache-key-a',
          originalRunId: 'run-original-a-7',
        },
        { type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } },
      ],
      // Frame 2: A still cache-hit; B completed -> RUN_OK.
      [
        {
          type: 'CACHE_HIT',
          cacheKey: 'cache-key-a',
          originalRunId: 'run-original-a-7',
        },
        {
          type: 'RUN_OK',
          tally: { running: 0, completed: 1, failed: 0 },
        },
      ],
    ],
  },
  zeroJobDAG: {
    name: 'zeroJobDAG',
    fixtureKey: 'single-method',
    timeline: zeroJobDAGTimeline,
    routeOptions: {
      validateResponse: {
        status: 200,
        body: { valid: false, errors: ['Pipeline has no nodes'] },
      },
    },
    skipVitest: true,
  },

  // ── ADR-015 Phase D Pass 2: SSE rows ───────────────────────────────────
  //
  // These rows reuse the polling timeline from the streaming/fault demos
  // but layer a recorded SSE stream on top.  The Vitest counterpart
  // verifies catalog scaffolding (the streaming-machine state-flip
  // assertions live in `streaming.test.ts`); the e2e counterpart drives
  // a real EventSource against the recorded stream and asserts on DOM
  // outcomes.

  streamingConnecting: {
    name: 'streamingConnecting',
    fixtureKey: 'single-method-streaming',
    timeline: [
      {
        delayMs: 0,
        payload: frame('running', {
          method_a: {
            status: 'running',
            run_ids: ['run-stream-1'],
            tally: { running: 1, completed: 0, failed: 0 },
          },
        }),
      },
      {
        delayMs: 200,
        payload: frame('completed', {
          method_a: {
            status: 'completed',
            run_ids: ['run-stream-1'],
            tally: { running: 0, completed: 1, failed: 0 },
          },
        }),
      },
    ],
    sseStream: streamingFixture as unknown as SSEStreamFixture,
    expectedEvents: [
      [{ type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } }],
      [
        {
          type: 'RUN_OK',
          tally: { running: 0, completed: 1, failed: 0 },
        },
      ],
    ],
  },

  liveLogLineAppend: (() => {
    // Pass 3: timeline lengthened so the polling node-pill stays in
    // `running` for ~11s while the paced SSE stream emits 5 stdout ticks
    // ~2s apart.  Without this, the node flipped to `completed` ~250ms
    // after Run was clicked and the gallery video had no progressive
    // log-append motion to capture.  The polling cadence in
    // `services.ts::pollWorkflowStatus` is 1Hz, so 11 running frames
    // covers the ~11s SSE replay window.
    const runningFrame: TimelineFrame = {
      delayMs: 0,
      payload: frame('running', {
        method_a: {
          status: 'running',
          run_ids: ['run-stream-1'],
          tally: { running: 1, completed: 0, failed: 0 },
        },
      }),
    };
    const completedFrame: TimelineFrame = {
      delayMs: 0,
      payload: frame('completed', {
        method_a: {
          status: 'completed',
          run_ids: ['run-stream-1'],
          tally: { running: 0, completed: 1, failed: 0 },
        },
      }),
    };
    // 14 running frames (1Hz polling × ~14s) gives the SSE replay's
    // terminal event (lands at ~11.1s) a comfortable margin before
    // polling flips the parent off `running` and tears down the
    // streaming child.  Without the margin, the race occasionally
    // completes polling first, the streaming machine never sees its
    // terminal frame, the InspectorPanel falls through to the
    // historical-fetch fallback, and the badge briefly flashes back
    // to "Connecting…" instead of staying on "Terminal · success".
    const N_RUNNING = 14;
    const tallyEvent: RunStatusEvent = {
      type: 'TALLY',
      tally: { running: 1, completed: 0, failed: 0 },
    };
    const runOkEvent: RunStatusEvent = {
      type: 'RUN_OK',
      tally: { running: 0, completed: 1, failed: 0 },
    };
    return {
      name: 'liveLogLineAppend',
      fixtureKey: 'single-method-streaming',
      timeline: [
        ...Array.from({ length: N_RUNNING }, () => runningFrame),
        completedFrame,
      ],
      sseStream: streamingLongFixture as unknown as SSEStreamFixture,
      expectedEvents: [
        ...Array.from({ length: N_RUNNING }, () => [tallyEvent]),
        [runOkEvent],
      ],
    };
  })(),

  faultOnStream: {
    name: 'faultOnStream',
    fixtureKey: 'single-method-streaming',
    timeline: [
      {
        delayMs: 0,
        payload: frame('running', {
          method_a: {
            status: 'running',
            run_ids: ['run-fault-1'],
            tally: { running: 1, completed: 0, failed: 0 },
          },
        }),
      },
      {
        delayMs: 250,
        payload: frame('failed', {
          method_a: {
            status: 'failed',
            run_ids: ['run-fault-1'],
            tally: { running: 0, completed: 0, failed: 1 },
            error:
              'ValueError: simulated crash in faulty fixture',
          },
        }),
      },
    ],
    sseStream: faultOnStreamFixture as unknown as SSEStreamFixture,
    expectedEvents: [
      [{ type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } }],
      [
        {
          type: 'RUN_FAILED',
          error_message: 'ValueError: simulated crash in faulty fixture',
        },
      ],
    ],
  },

  // Pass 3: streaming-then-crash gallery video.  Distinct from
  // `faultOnStream` (which crashes ~250ms after Run, no visible
  // progress).  This row keeps the run "running" for ~6s while the
  // SSE fixture emits 3 progressive stdout ticks before flipping to a
  // stderr crash + traceback + terminal:failed.  The Inspector's
  // node-error-box renders from polling's `error` string just like
  // `faultOnStream`.
  faultMidStream: (() => {
    const runningFrame: TimelineFrame = {
      delayMs: 0,
      payload: frame('running', {
        method_a: {
          status: 'running',
          run_ids: ['run-midfault-1'],
          tally: { running: 1, completed: 0, failed: 0 },
        },
      }),
    };
    const failedFrame: TimelineFrame = {
      delayMs: 0,
      payload: frame('failed', {
        method_a: {
          status: 'failed',
          run_ids: ['run-midfault-1'],
          tally: { running: 0, completed: 0, failed: 1 },
          error: 'RuntimeError: stream_fail: fatal at tick 3',
        },
      }),
    };
    const N_RUNNING = 6;
    const tallyEvent: RunStatusEvent = {
      type: 'TALLY',
      tally: { running: 1, completed: 0, failed: 0 },
    };
    const failedEvent: RunStatusEvent = {
      type: 'RUN_FAILED',
      error_message: 'RuntimeError: stream_fail: fatal at tick 3',
    };
    return {
      name: 'faultMidStream',
      fixtureKey: 'single-method-streaming',
      timeline: [
        ...Array.from({ length: N_RUNNING }, () => runningFrame),
        failedFrame,
      ],
      sseStream: faultMidStreamFixture as unknown as SSEStreamFixture,
      expectedEvents: [
        ...Array.from({ length: N_RUNNING }, () => [tallyEvent]),
        [failedEvent],
      ],
    };
  })(),

  cancelledByUserMidRun: {
    name: 'cancelledByUserMidRun',
    fixtureKey: 'single-method-streaming',
    timeline: [
      {
        delayMs: 0,
        payload: frame('running', {
          method_a: {
            status: 'running',
            run_ids: ['run-cancel-1'],
            tally: { running: 1, completed: 0, failed: 0 },
          },
        }),
      },
      {
        delayMs: 250,
        payload: frame('cancelled', {
          method_a: {
            status: 'cancelled',
            run_ids: ['run-cancel-1'],
            tally: { running: 0, completed: 0, failed: 0 },
            error: 'Cancelled by user',
          },
        }),
      },
    ],
    sseStream: streamingCancelledFixture as unknown as SSEStreamFixture,
    // No upstream_node_id — this is a user-initiated cancel, distinct
    // from the upstream-failure cancel covered by
    // `cancelledByUpstreamFailure`.  Validated against the actual bridge
    // (services.ts -> runStatusToNodeState): a `cancelled` polling status
    // with no `upstream_node_id` maps to `{ type: 'USER_STOP' }` -- a
    // distinct event from RUN_FAILED so the state machine can route to a
    // user-cancelled terminal state without surfacing the cancel string
    // through the failure-error UI.
    expectedEvents: [
      [{ type: 'TALLY', tally: { running: 1, completed: 0, failed: 0 } }],
      [{ type: 'USER_STOP' }],
    ],
  },
};
