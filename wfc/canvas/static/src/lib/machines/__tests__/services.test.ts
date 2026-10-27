/**
 * Vitest unit tests for the explicit Run.status → nodeRunActor state
 * mapping (`runStatusToNodeState`) and its reverse, the
 * actor → coarse RunStatus collapse (`stateValueToRunStatus`).
 *
 * These two helpers enforce the richer-chart-than-backend invariant:
 * the actor knows
 * `cancelled.becauseUpstream` vs `cancelled.becauseUser`; the backend
 * only stores `cancelled`. Tests pin the mapping explicitly.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createActor, setup, type EventObject } from 'xstate';
import {
  isTerminalOverallStatus,
  POLL_RETRY_BOUND_MS,
  pollNodeStatus,
  runStatusToNodeState,
  stateValueToRunStatus,
  subscribeSSE,
} from '../services';

describe('runStatusToNodeState', () => {
  it('pending → null (parent already moved node to queued)', () => {
    expect(runStatusToNodeState({ status: 'pending' })).toBe(null);
  });

  it('running with no tally → null (heartbeat sent separately)', () => {
    expect(runStatusToNodeState({ status: 'running' })).toBe(null);
  });

  it('running with tally → TALLY event', () => {
    const evt = runStatusToNodeState({
      status: 'running',
      tally: { running: 2, completed: 1, failed: 0 },
    });
    expect(evt).toEqual({
      type: 'TALLY',
      tally: { running: 2, completed: 1, failed: 0 },
    });
  });

  it('completed → RUN_OK with tally', () => {
    const evt = runStatusToNodeState({
      status: 'completed',
      tally: { running: 0, completed: 4, failed: 0 },
    });
    expect(evt).toEqual({
      type: 'RUN_OK',
      tally: { running: 0, completed: 4, failed: 0 },
    });
  });

  it('mixed → RUN_OK; nodeRunActor decides completed_with_failures from tally', () => {
    // The mapper does NOT collapse mixed → completed_with_failures —
    // that decision lives in the actor (see nodeRun.machine.ts running
    // → RUN_OK guarded transition). The mapper just forwards the tally.
    const evt = runStatusToNodeState({
      status: 'mixed',
      tally: { running: 0, completed: 3, failed: 1 },
    });
    expect(evt?.type).toBe('RUN_OK');
    if (evt?.type === 'RUN_OK') {
      expect(evt.tally.failed).toBe(1);
    }
  });

  it('failed → RUN_FAILED with error_message', () => {
    const evt = runStatusToNodeState({
      status: 'failed',
      error: 'segfault',
    });
    expect(evt).toEqual({ type: 'RUN_FAILED', error_message: 'segfault' });
  });

  it('cancelled upstream_failed with cancelled_due_to_run_id only → UPSTREAM_FAILED', () => {
    // Backend may only have the upstream run id (not the node id) —
    // mapper still treats this as upstream-caused cancellation.
    const evt = runStatusToNodeState({
      status: 'cancelled',
      cancel_reason: 'upstream_failed',
      cancelled_due_to_run_id: 'run-99',
    });
    expect(evt).toEqual({
      type: 'UPSTREAM_FAILED',
      upstreamNodeId: '?',
      upstreamRunId: 'run-99',
    });
  });

  it('a cancelled node is read by its reason, not by whether it names a cause', () => {
    // After a user cancel the walk links a failed ancestor where there is
    // one, but the node was still stopped by the user.
    expect(runStatusToNodeState({
      status: 'cancelled',
      cancel_reason: 'user',
      cancelled_due_to_run_id: 'run-7',
      upstream_run_id: 'run-7',
      upstream_node_id: 'parent_node',
    })).toEqual({ type: 'USER_STOP' });
    expect(runStatusToNodeState({
      status: 'cancelled',
      cancel_reason: 'upstream_failed',
      cancelled_due_to_run_id: 'run-7',
      upstream_run_id: 'run-7',
      upstream_node_id: 'parent_node',
    })).toEqual({
      type: 'UPSTREAM_FAILED', upstreamNodeId: 'parent_node', upstreamRunId: 'run-7',
    });
  });

  it('a node the pipeline ended before it started never reads as stopped by the user', () => {
    // No cause, reason not_run: the node is left as it is (no outcome)
    // rather than reported as the user's stop.
    expect(runStatusToNodeState({ status: 'cancelled', cancel_reason: 'not_run' })).toBeNull();
  });

  // Cache-hit wins over status.
  it('cache_hit=true → CACHE_HIT regardless of status (completed)', () => {
    const evt = runStatusToNodeState({
      status: 'completed',
      cache_hit: true,
      original_run_id: 'run-original-42',
      cache_key: 'cache-key-abc',
      run_ids: ['audit-1'],
      tally: { running: 0, completed: 1, failed: 0 },
    });
    expect(evt).toEqual({
      type: 'CACHE_HIT',
      cacheKey: 'cache-key-abc',
      originalRunId: 'run-original-42',
    });
  });

  it('cache_hit=true with status=running still emits CACHE_HIT (invariant)', () => {
    // Defends against a polling tick where the cache
    // signal arrives before the audit row's terminal status.  The
    // bridge MUST short-circuit so the streaming actor never spawns.
    const evt = runStatusToNodeState({
      status: 'running',
      cache_hit: true,
      original_run_id: 'run-original-7',
      cache_key: 'k',
    });
    expect(evt).toEqual({
      type: 'CACHE_HIT',
      cacheKey: 'k',
      originalRunId: 'run-original-7',
    });
  });
});

describe('stateValueToRunStatus', () => {
  it('idle → idle', () => {
    expect(stateValueToRunStatus('idle')).toBe('idle');
  });

  it('queued → pending', () => {
    expect(stateValueToRunStatus('queued')).toBe('pending');
  });

  it('running → running', () => {
    expect(stateValueToRunStatus('running')).toBe('running');
  });

  it('succeeded → completed', () => {
    expect(stateValueToRunStatus('succeeded')).toBe('completed');
  });

  it('completed_with_failures → mixed', () => {
    expect(stateValueToRunStatus('completed_with_failures')).toBe('mixed');
  });

  it('failed → failed', () => {
    expect(stateValueToRunStatus('failed')).toBe('failed');
  });

  it('orphaned → failed (collapses to failed for CSS)', () => {
    expect(stateValueToRunStatus('orphaned')).toBe('failed');
  });

  it('cached → cached (a reused run is drawn apart from one that ran)', () => {
    expect(stateValueToRunStatus('cached')).toBe('cached');
  });

  it('cancelled.becauseUser → cancelled (collapses subroots)', () => {
    expect(stateValueToRunStatus({ cancelled: 'becauseUser' })).toBe('cancelled');
  });

  it('cancelled.becauseUpstream → cancelled (collapses subroots)', () => {
    expect(stateValueToRunStatus({ cancelled: 'becauseUpstream' })).toBe('cancelled');
  });
});

describe('isTerminalOverallStatus', () => {
  // The polling actor stops the loop and emits PIPELINE_DONE iff the
  // backend's overall_status is terminal. `cancelled` must count as
  // terminal — without it, a fully-cancelled pipeline polls forever and
  // the canvas Run/Stop button never recovers without a refresh.
  it('cancelled is terminal, so polling stops and the Run/Stop button recovers', () => {
    expect(isTerminalOverallStatus('cancelled')).toBe(true);
  });

  it('completed / failed / completed_with_failures still terminal', () => {
    expect(isTerminalOverallStatus('completed')).toBe(true);
    expect(isTerminalOverallStatus('failed')).toBe(true);
    expect(isTerminalOverallStatus('completed_with_failures')).toBe(true);
  });

  it('running and pending are not terminal', () => {
    expect(isTerminalOverallStatus('running')).toBe(false);
    expect(isTerminalOverallStatus('pending')).toBe(false);
    expect(isTerminalOverallStatus(undefined)).toBe(false);
  });
});

// ── subscribeSSE grace window ──────────────────────────────────────────
//
// The streaming machine's invariant ("transient EventSource blips are
// invisible to the user") is upheld by services.ts, not the machine.
// We assert the contract directly here: errors followed by a successful
// message within the poller's retry bound (POLL_RETRY_BOUND_MS, so a
// server restart is survived) must NOT propagate SSE_ERROR; an error that
// survives the grace window must propagate exactly once.

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
}

function startSubscribeSSE(): {
  events: EventObject[];
  stop: () => void;
} {
  // Wrap subscribeSSE in a parent harness that captures every event the
  // service sends via `sendBack`. Same invoke shape `nodeRunActor` uses.
  const events: EventObject[] = [];
  const harness = setup({
    types: {
      events: {} as EventObject,
    },
    actors: { subscribeSSE: subscribeSSE as never },
  }).createMachine({
    id: 'harness',
    initial: 'live',
    states: {
      live: {
        invoke: {
          src: 'subscribeSSE',
          input: { runId: 'r1', fullMode: false },
        },
        on: {
          '*': {
            actions: ({ event }) => {
              events.push(event);
            },
          },
        },
      },
    },
  });
  const actor = createActor(harness);
  actor.start();
  return { events, stop: () => actor.stop() };
}

describe('subscribeSSE — transient-error grace window', () => {
  let originalES: typeof EventSource;
  beforeEach(() => {
    vi.useFakeTimers();
    originalES = globalThis.EventSource;
    (globalThis as unknown as { EventSource: typeof EventSource }).EventSource =
      FakeEventSource as unknown as typeof EventSource;
    FakeEventSource.instances = [];
  });
  afterEach(() => {
    (globalThis as unknown as { EventSource: typeof EventSource }).EventSource = originalES;
    vi.useRealTimers();
  });

  it('errors lasting under the poller\'s retry bound, then a message, keep the stream alive', () => {
    const { events, stop } = startSubscribeSSE();
    const es = FakeEventSource.instances[0];
    expect(es).toBeDefined();

    // The server goes away (a restart) — grace window starts; the browser
    // keeps reconnecting, failing each time.
    es.onerror?.(new Event('error'));
    expect(es.closed).toBe(false); // browser auto-reconnect kept alive
    vi.advanceTimersByTime(30_000);
    es.onerror?.(new Event('error'));
    vi.advanceTimersByTime(POLL_RETRY_BOUND_MS - 31_000);
    expect(events.filter(e => e.type === 'SSE_ERROR')).toHaveLength(0);
    expect(es.closed).toBe(false);

    // The restarted server answers before the bound.
    es.onmessage?.(
      new MessageEvent('message', {
        data: JSON.stringify({ type: 'stdout', data: 'recovered' }),
      }),
    );

    // Push well past the bound — no SSE_ERROR should fire.
    vi.advanceTimersByTime(POLL_RETRY_BOUND_MS * 2);
    const errors = events.filter(e => e.type === 'SSE_ERROR');
    expect(errors).toHaveLength(0);
    const lines = events.filter(e => e.type === 'SSE_LINE');
    expect(lines).toHaveLength(1);
    expect(es.closed).toBe(false);

    stop();
  });

  it('a reconnect that opens keeps a quiet stream alive past the retry bound', () => {
    const { events, stop } = startSubscribeSSE();
    const es = FakeEventSource.instances[0];

    // The server goes away; the browser reconnects and the restarted
    // server answers, but the running step prints nothing more.
    es.onerror?.(new Event('error'));
    vi.advanceTimersByTime(5_000);
    es.onopen?.(new Event('open'));

    // Silence well past the bound: the reconnect stands, no SSE_ERROR.
    vi.advanceTimersByTime(POLL_RETRY_BOUND_MS * 2);
    expect(events.filter(e => e.type === 'SSE_ERROR')).toHaveLength(0);
    expect(es.closed).toBe(false);

    // A later failure starts a fresh grace window of its own.
    es.onerror?.(new Event('error'));
    vi.advanceTimersByTime(POLL_RETRY_BOUND_MS);
    expect(events.filter(e => e.type === 'SSE_ERROR')).toHaveLength(1);

    stop();
  });

  it('errors persisting past the poller\'s retry bound propagate SSE_ERROR exactly once', () => {
    const { events, stop } = startSubscribeSSE();
    const es = FakeEventSource.instances[0];

    es.onerror?.(new Event('error'));
    // Subsequent onerror calls during the grace window must not stack timers.
    es.onerror?.(new Event('error'));
    es.onerror?.(new Event('error'));

    vi.advanceTimersByTime(POLL_RETRY_BOUND_MS - 1);
    expect(events.filter(e => e.type === 'SSE_ERROR')).toHaveLength(0);
    vi.advanceTimersByTime(1);
    const errors = events.filter(e => e.type === 'SSE_ERROR');
    expect(errors).toHaveLength(1);
    expect(es.closed).toBe(true);

    // A late error from the closed source sends nothing more.
    es.onerror?.(new Event('error'));
    vi.advanceTimersByTime(POLL_RETRY_BOUND_MS * 2);
    expect(events.filter(e => e.type === 'SSE_ERROR')).toHaveLength(1);

    stop();
  });
});

// ── pollNodeStatus — terminal gating on thread_alive ───────────────────
//
// A failure/partial run is only "done" once the backend run thread has
// exited (`thread_alive === false`) — that is when the pipeline-end
// cancelled-row walk has committed. Stopping the moment `overall_status`
// first reads `failed` would freeze downstream nodes in `queued` →
// `pending`, because their `cancelled` rows aren't written yet. These tests drive
// the real `pollNodeStatus` callback through a mocked `fetch` + fake
// timers and assert the loop keeps polling until the thread is dead.

interface StatusFrame {
  job_id: string;
  overall_status: string;
  node_states: Record<string, unknown>;
  thread_alive: boolean;
  log: string;
  error: unknown;
}

function statusFrame(
  overall: string,
  nodeStates: Record<string, unknown>,
  threadAlive: boolean,
): StatusFrame {
  return {
    job_id: 'poll-job',
    overall_status: overall,
    node_states: nodeStates,
    thread_alive: threadAlive,
    log: '',
    error: null,
  };
}

// What one status request meets: a frame, a lost connection (fetch
// rejects), the server's 404 for a pipeline it does not know, a 503 from
// a server still starting, or a 200 whose body is not JSON.
type StatusReply = StatusFrame | 'offline' | 404 | 503 | 'unparseable';

// Install a fetch stub: the Nth GET to `/api/workflow/status/:id` gets
// the Nth reply (the final reply sticks for later polls); every other
// request (e.g. the terminal `/api/wfc/refresh` POST) resolves empty.
function installFetchMock(frames: StatusReply[]): { restore: () => void } {
  const orig = globalThis.fetch;
  let statusCalls = 0;
  globalThis.fetch = vi.fn(async (url: unknown) => {
    const u = String(url);
    if (u.includes('/api/workflow/status/')) {
      const idx = Math.min(statusCalls, frames.length - 1);
      statusCalls += 1;
      const reply = frames[idx];
      if (reply === 'offline') throw new TypeError('Failed to fetch');
      if (reply === 404) {
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: 'Job not found' }),
        } as unknown as Response;
      }
      if (reply === 503) {
        return {
          ok: false,
          status: 503,
          json: async () => ({ detail: 'Service Unavailable' }),
        } as unknown as Response;
      }
      if (reply === 'unparseable') {
        return {
          ok: true,
          status: 200,
          json: async () => {
            throw new SyntaxError('Unexpected token < in JSON');
          },
        } as unknown as Response;
      }
      return { ok: true, status: 200, json: async () => reply } as unknown as Response;
    }
    return { ok: true, status: 200, json: async () => ({}) } as unknown as Response;
  }) as unknown as typeof fetch;
  return {
    restore: () => {
      globalThis.fetch = orig;
    },
  };
}

function startPollNodeStatus(
  jobId: string,
  nodeIds: string[],
): { events: EventObject[]; stop: () => void } {
  const events: EventObject[] = [];
  const harness = setup({
    types: { events: {} as EventObject },
    actors: { pollNodeStatus: pollNodeStatus as never },
  }).createMachine({
    id: 'pollHarness',
    initial: 'live',
    states: {
      live: {
        invoke: { src: 'pollNodeStatus', input: { jobId, nodeIds } },
        on: {
          '*': {
            actions: ({ event }) => {
              events.push(event);
            },
          },
        },
      },
    },
  });
  const actor = createActor(harness);
  actor.start();
  return { events, stop: () => actor.stop() };
}

describe('pollNodeStatus — terminal gating on thread_alive', () => {
  let fetchMock: { restore: () => void } | undefined;
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    fetchMock?.restore();
    fetchMock = undefined;
    vi.useRealTimers();
  });

  it('overall=failed keeps polling while the thread is alive, then flips downstream to cancelled and fires PIPELINE_DONE once the thread is dead', async () => {
    const frames = [
      // First poll: the upstream already failed, but the run thread is
      // still alive — the downstream node has no row yet (pending).
      statusFrame(
        'failed',
        { mid: { status: 'failed', run_ids: ['10'] }, down: { status: 'pending' } },
        true,
      ),
      // Later poll: thread has exited and the cancelled-row walk has run.
      statusFrame(
        'failed',
        {
          mid: { status: 'failed', run_ids: ['10'] },
          down: {
            status: 'cancelled',
            run_ids: ['11'],
            cancel_reason: 'upstream_failed',
            cancelled_due_to_run_id: '10',
            upstream_run_id: '10',
            upstream_node_id: 'mid',
          },
        },
        false,
      ),
    ];
    fetchMock = installFetchMock(frames);
    const { events, stop } = startPollNodeStatus('poll-job', ['mid', 'down']);

    // First tick: `overall` is already `failed`, but `thread_alive` is
    // true → must NOT terminate, and the downstream node stays untouched.
    await vi.advanceTimersByTimeAsync(0);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(0);
    expect(events.some(e => e.type === 'NODE_UPSTREAM_FAILED')).toBe(false);

    // Next tick observes `thread_alive: false` + the downstream cancelled
    // row → the poller emits the downstream cancel, still not yet done.
    await vi.advanceTimersByTimeAsync(1000);
    expect(
      events.some(
        e => e.type === 'NODE_UPSTREAM_FAILED' && (e as { nodeId?: string }).nodeId === 'down',
      ),
    ).toBe(true);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(0);

    // PIPELINE_DONE is deferred ~250ms after the terminal tick.
    await vi.advanceTimersByTimeAsync(300);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(1);
    stop();
  });

  it('overall=completed_with_failures (mixed) also waits for thread_alive=false before PIPELINE_DONE', async () => {
    const mixedNode = {
      status: 'mixed',
      tally: { running: 0, completed: 3, failed: 1 },
      run_ids: ['20'],
    };
    const frames = [
      statusFrame('completed_with_failures', { node: mixedNode }, true),
      statusFrame('completed_with_failures', { node: mixedNode }, false),
    ];
    fetchMock = installFetchMock(frames);
    const { events, stop } = startPollNodeStatus('poll-job', ['node']);

    // A partial-failure run is terminal-ish, but while the thread is alive
    // the cancelled-row walk hasn't run — must keep polling.
    await vi.advanceTimersByTimeAsync(0);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(0);

    // Thread dead on the next tick → now it may finish.
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(300);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(1);
    stop();
  });
});

// ── pollNodeStatus — a lost connection is retried, a 404 ends it ───────
//
// An open tab whose server restarts must carry on once the restarted
// server answers for the pipeline from its record: failed requests keep
// the nodes as they are and are retried, bounded by POLL_RETRY_BOUND_MS.

describe('pollNodeStatus — a lost connection is retried, a 404 ends it', () => {
  let fetchMock: { restore: () => void } | undefined;
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    fetchMock?.restore();
    fetchMock = undefined;
    vi.useRealTimers();
  });

  const running = statusFrame(
    'running',
    { a: { status: 'running', run_ids: ['7'] }, b: { status: 'pending' } },
    true,
  );

  it('keeps polling through network errors, then resumes when the server answers again', async () => {
    const answered = statusFrame(
      'completed',
      {
        a: { status: 'completed', run_ids: ['7'] },
        b: { status: 'completed', run_ids: ['8'] },
      },
      false,
    );
    fetchMock = installFetchMock([running, 'offline', 'offline', 'offline', answered]);
    const { events, stop } = startPollNodeStatus('poll-job', ['a', 'b']);

    await vi.advanceTimersByTimeAsync(0);
    expect(events.some(e => e.type === 'NODE_HEARTBEAT')).toBe(true);
    const seenWhileRunning = events.length;

    // Three failed requests: nothing ends, no node event is sent.
    await vi.advanceTimersByTimeAsync(3000);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(0);
    expect(events.length).toBe(seenWhileRunning);

    // The server answers again: the nodes move on and the run finishes.
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(300);
    expect(
      events.some(e => e.type === 'NODE_RUN_OK' && (e as { nodeId?: string }).nodeId === 'b'),
    ).toBe(true);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(1);
    stop();
  });

  it.each([
    ['a 5xx answer', 503 as const],
    ['an unparseable body', 'unparseable' as const],
  ])('retries %s like a lost connection, then resumes', async (_label, failure) => {
    const answered = statusFrame(
      'completed',
      {
        a: { status: 'completed', run_ids: ['7'] },
        b: { status: 'completed', run_ids: ['8'] },
      },
      false,
    );
    fetchMock = installFetchMock([running, failure, failure, answered]);
    const { events, stop } = startPollNodeStatus('poll-job', ['a', 'b']);

    await vi.advanceTimersByTimeAsync(0);
    const seenWhileRunning = events.length;

    // Two failed requests: polling neither ends nor moves a node.
    await vi.advanceTimersByTimeAsync(2000);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(0);
    expect(events.length).toBe(seenWhileRunning);

    // The server answers again: the nodes move on and the run finishes.
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(300);
    expect(
      events.some(e => e.type === 'NODE_RUN_OK' && (e as { nodeId?: string }).nodeId === 'b'),
    ).toBe(true);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(1);
    stop();
  });

  it('ends on a 404: the server knows no such pipeline', async () => {
    fetchMock = installFetchMock([running, 404]);
    const { events, stop } = startPollNodeStatus('poll-job', ['a', 'b']);

    await vi.advanceTimersByTimeAsync(0);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1000);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(1);

    // Ended: no further request is made.
    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls
      .length;
    await vi.advanceTimersByTimeAsync(5000);
    expect((globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(
      calls,
    );
    stop();
  });

  it('ends once failed requests have lasted past the bound', async () => {
    fetchMock = installFetchMock([running, 'offline']);
    const { events, stop } = startPollNodeStatus('poll-job', ['a', 'b']);

    await vi.advanceTimersByTimeAsync(0);
    // Just short of the bound: still retrying.
    await vi.advanceTimersByTimeAsync(POLL_RETRY_BOUND_MS);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(0);
    // Past it: polling ends, once.
    await vi.advanceTimersByTimeAsync(2000);
    expect(events.filter(e => e.type === 'PIPELINE_DONE')).toHaveLength(1);
    stop();
  });
});
