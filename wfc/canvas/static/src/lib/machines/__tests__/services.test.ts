/**
 * Vitest unit tests for the explicit Run.status → nodeRunActor state
 * mapping (`runStatusToNodeState`) and its reverse, the
 * actor → legacy RunStatus collapse (`stateValueToRunStatus`).
 *
 * These two helpers enforce the richer-chart-than-backend invariant
 * called out in ADR-016 §"single source of truth": the actor knows
 * `cancelled.becauseUpstream` vs `cancelled.becauseUser`; the backend
 * only stores `cancelled`. Tests pin the mapping explicitly.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createActor, setup, type EventObject } from 'xstate';
import {
  isTerminalOverallStatus,
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

  it('cancelled without upstream info → USER_STOP', () => {
    const evt = runStatusToNodeState({ status: 'cancelled' });
    expect(evt).toEqual({ type: 'USER_STOP' });
  });

  it('cancelled with upstream_node_id → UPSTREAM_FAILED', () => {
    const evt = runStatusToNodeState({
      status: 'cancelled',
      upstream_node_id: 'parent_node',
      upstream_run_id: 'run-42',
    });
    expect(evt).toEqual({
      type: 'UPSTREAM_FAILED',
      upstreamNodeId: 'parent_node',
      upstreamRunId: 'run-42',
    });
  });

  it('cancelled with cancelled_due_to_run_id only → UPSTREAM_FAILED', () => {
    // Backend may only have the upstream run id (not the node id) —
    // mapper still treats this as upstream-caused cancellation.
    const evt = runStatusToNodeState({
      status: 'cancelled',
      cancelled_due_to_run_id: 'run-99',
    });
    expect(evt).toEqual({
      type: 'UPSTREAM_FAILED',
      upstreamNodeId: '?',
      upstreamRunId: 'run-99',
    });
  });

  // ADR-015 Phase D Bug 4 Path A + Bug 5: cache-hit wins over status.
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
    // Defends against a future polling-service tick where the cache
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

  it('cached → completed (cached counts as a successful outcome)', () => {
    expect(stateValueToRunStatus('cached')).toBe('completed');
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
  // backend's overall_status is terminal. `cancelled` was missing from
  // the original equality chain — without it, a fully-cancelled
  // pipeline polled forever and the canvas Run/Stop button never
  // recovered without a refresh.
  it('cancelled is terminal — fixes the never-recovers wedge', () => {
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
// We assert the contract directly here: a single onerror followed by a
// successful message within 5s must NOT propagate SSE_ERROR; an onerror
// that survives the grace window must propagate exactly once.

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
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

  it('a transient onerror followed by a successful message does not propagate SSE_ERROR', () => {
    const { events, stop } = startSubscribeSSE();
    const es = FakeEventSource.instances[0];
    expect(es).toBeDefined();

    // Wire blip — grace window starts.
    es.onerror?.(new Event('error'));
    expect(es.closed).toBe(false); // browser auto-reconnect kept alive

    // Recovery before the 5s deadline.
    vi.advanceTimersByTime(2000);
    es.onmessage?.(
      new MessageEvent('message', {
        data: JSON.stringify({ type: 'stdout', data: 'recovered' }),
      }),
    );

    // Push past the original 5s deadline — no SSE_ERROR should fire.
    vi.advanceTimersByTime(10_000);
    const errors = events.filter(e => e.type === 'SSE_ERROR');
    expect(errors).toHaveLength(0);
    const lines = events.filter(e => e.type === 'SSE_LINE');
    expect(lines).toHaveLength(1);

    stop();
  });

  it('an onerror that persists past the grace window propagates SSE_ERROR exactly once', () => {
    const { events, stop } = startSubscribeSSE();
    const es = FakeEventSource.instances[0];

    es.onerror?.(new Event('error'));
    // Subsequent onerror calls during the grace window must not stack timers.
    es.onerror?.(new Event('error'));
    es.onerror?.(new Event('error'));

    vi.advanceTimersByTime(5000);
    const errors = events.filter(e => e.type === 'SSE_ERROR');
    expect(errors).toHaveLength(1);
    expect(es.closed).toBe(true);

    stop();
  });
});
