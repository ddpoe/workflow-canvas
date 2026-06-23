/**
 * Vitest suite for the SSE-streaming machine.
 *
 * Asserts behavior, not state shape: each test is a correctness spec for
 * one user-visible outcome (success / failed / cancelled / wire-failure).
 * The `subscribeSSE` callback service is replaced with a controllable
 * stub via `.provide` so tests never open a real EventSource.
 */
import { describe, expect, it } from 'vitest';
import { createActor, fromCallback, type EventObject } from 'xstate';
import { makeStreamingMachine } from '../streaming.machine';

function makeStubService() {
  let sendBackRef: ((evt: EventObject) => void) | null = null;
  const stub = fromCallback<EventObject, EventObject>(({ sendBack }) => {
    sendBackRef = sendBack;
    return () => {
      sendBackRef = null;
    };
  });
  return {
    stub,
    emit: (evt: EventObject) => {
      sendBackRef?.(evt);
    },
  };
}

function startActor(stub: ReturnType<typeof fromCallback>) {
  const m = makeStreamingMachine().provide({
    actors: { subscribeSSE: stub as never },
  });
  const actor = createActor(m, {
    input: { runId: 'run-1', fullMode: false },
  });
  actor.start();
  return actor;
}

describe('streamingActor', () => {
  it('happy path: connecting → streaming → succeeded preserves single subscription', () => {
    const { stub, emit } = makeStubService();
    const actor = startActor(stub);

    // Auto-connect on spawn — connection-alive parent invokes subscribeSSE.
    expect(actor.getSnapshot().matches({ 'connection-alive': 'connecting' })).toBe(true);

    // First line flips child to streaming WITHOUT leaving connection-alive,
    // so the parent's invoke (the EventSource) is preserved.
    emit({ type: 'SSE_LINE', kind: 'stdout', line: 'hello' });
    expect(actor.getSnapshot().matches({ 'connection-alive': 'streaming' })).toBe(true);
    expect(actor.getSnapshot().context.lines.length).toBe(1);

    // Backend's _log_map_terminal_status emits 'success' (not 'completed').
    emit({
      type: 'SSE_TERMINAL',
      status: 'success',
      error_message: null,
      error_traceback: null,
    });
    expect(actor.getSnapshot().matches('succeeded')).toBe(true);
    expect(actor.getSnapshot().context.terminalStatus).toBe('success');
    actor.stop();
  });

  it('terminal:failed routes to the failed final with error payload', () => {
    const { stub, emit } = makeStubService();
    const actor = startActor(stub);

    emit({
      type: 'SSE_TERMINAL',
      status: 'failed',
      error_message: 'boom',
      error_traceback: 'tb',
    });
    expect(actor.getSnapshot().matches('failed')).toBe(true);
    expect(actor.getSnapshot().context.terminalError).toBe('boom');
    expect(actor.getSnapshot().context.terminalTraceback).toBe('tb');

    // Late SSE_LINE after a final state must not flip us back.
    emit({ type: 'SSE_LINE', kind: 'stdout', line: 'late' });
    expect(actor.getSnapshot().matches('failed')).toBe(true);
    actor.stop();
  });

  it('terminal:cancelled routes to the cancelled final', () => {
    const { stub, emit } = makeStubService();
    const actor = startActor(stub);

    emit({
      type: 'SSE_TERMINAL',
      status: 'cancelled',
      error_message: null,
      error_traceback: null,
    });
    expect(actor.getSnapshot().matches('cancelled')).toBe(true);
    actor.stop();
  });

  it('SSE_ERROR (wire failure past grace window) → failed with synthesized message', () => {
    const { stub, emit } = makeStubService();
    const actor = startActor(stub);

    // The grace window lives in services.ts; by the time the machine
    // sees SSE_ERROR, the failure has already persisted. The machine's
    // job is just to land in `failed` with a user-readable message.
    emit({ type: 'SSE_ERROR' });
    expect(actor.getSnapshot().matches('failed')).toBe(true);
    expect(actor.getSnapshot().context.terminalError).toBe('Connection lost');
    actor.stop();
  });

  it('unknown terminal status (e.g., raw `running` from wall-time guard) defaults to failed', () => {
    const { stub, emit } = makeStubService();
    const actor = startActor(stub);

    // server.py:2092's wall-time-guard path emits `cur_status` raw
    // without going through _log_map_terminal_status. Defensive default.
    emit({
      type: 'SSE_TERMINAL',
      status: 'running',
      error_message: null,
      error_traceback: null,
    });
    expect(actor.getSnapshot().matches('failed')).toBe(true);
    actor.stop();
  });
});
