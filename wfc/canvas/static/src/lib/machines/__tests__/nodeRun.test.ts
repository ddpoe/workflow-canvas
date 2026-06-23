/**
 * Vitest suite for the per-node lifecycle machine.
 *
 * Asserts each transition documented in ADR-016's nodeRunActor state map.
 * Uses `setup({ actors: { streamingActor: ... } })` to inject a no-op
 * stub so the test never opens an EventSource. Impossible transitions
 * (e.g. `idle → succeeded`) are covered by xstate's strict mode rather
 * than per-edge negative assertions.
 */
import { describe, expect, it } from 'vitest';
import { createActor, createMachine, fromCallback } from 'xstate';
import { makeNodeRunMachine } from '../nodeRun.machine';

// Tiny no-op streaming actor stub. We override the streamingActor child
// in the machine's `setup` so spawning `running` does not try to open a
// real EventSource. The stub stays alive until the parent stops it.
const streamingStub = fromCallback(() => {
  return () => {};
});

function makeActor() {
  const m = makeNodeRunMachine().provide({
    actors: { streamingActor: streamingStub },
  });
  return createActor(m);
}

describe('nodeRunActor', () => {
  it('idle → queued → running → succeeded with finalTally', () => {
    const actor = makeActor();
    actor.start();
    expect(actor.getSnapshot().value).toBe('idle');

    actor.send({ type: 'RUN', jobId: 'job-1' });
    expect(actor.getSnapshot().value).toBe('queued');

    actor.send({ type: 'HEARTBEAT', runId: 'run-1' });
    expect(actor.getSnapshot().value).toBe('running');
    expect(actor.getSnapshot().context.runId).toBe('run-1');

    actor.send({ type: 'RUN_OK', tally: { running: 0, completed: 4, failed: 0 } });
    expect(actor.getSnapshot().value).toBe('succeeded');
    expect(actor.getSnapshot().context.finalTally).toEqual({
      running: 0,
      completed: 4,
      failed: 0,
    });
    actor.stop();
  });

  it('running → completed_with_failures when tally has failures', () => {
    const actor = makeActor();
    actor.start();
    actor.send({ type: 'RUN', jobId: 'job-1' });
    actor.send({ type: 'HEARTBEAT', runId: 'run-1' });

    actor.send({
      type: 'RUN_OK',
      tally: { running: 0, completed: 3, failed: 1 },
      // ADR-015 Phase D Pass 1.5 (D-B2): RUN_OK now carries an
      // optional error_message. The polling bridge populates it when
      // mapping a backend `mixed` status; the machine assigns it into
      // context so the Inspector's node-error-box can render the
      // failed-sample error string.
      error_message: 'sample-007 raised KeyError',
    });
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('completed_with_failures');
    expect(snap.context.finalTally?.failed).toBe(1);
    expect(snap.context.error_message).toBe('sample-007 raised KeyError');
    actor.stop();
  });

  it('running → failed carries error_message and error_traceback', () => {
    const actor = makeActor();
    actor.start();
    actor.send({ type: 'RUN', jobId: 'job-1' });
    actor.send({ type: 'HEARTBEAT', runId: 'run-1' });

    actor.send({
      type: 'RUN_FAILED',
      error_message: 'boom',
      error_traceback: 'Traceback...\n  File ...',
    });
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('failed');
    expect(snap.context.error_message).toBe('boom');
    expect(snap.context.error_traceback).toContain('Traceback');
    actor.stop();
  });

  it('UPSTREAM_FAILED → cancelled.becauseUpstream carries upstream payload', () => {
    const actor = makeActor();
    actor.start();
    actor.send({ type: 'RUN', jobId: 'job-1' });
    // queued is also valid for UPSTREAM_FAILED — the upstream may fail
    // before this node has even heartbeat. Stay in queued.
    actor.send({
      type: 'UPSTREAM_FAILED',
      upstreamNodeId: 'faulty',
      upstreamRunId: 'run-7',
    });
    const snap = actor.getSnapshot();
    expect(snap.matches({ cancelled: 'becauseUpstream' })).toBe(true);
    expect(snap.context.upstreamNodeId).toBe('faulty');
    expect(snap.context.upstreamRunId).toBe('run-7');
    actor.stop();
  });

  it('USER_STOP from running → cancelled.becauseUser (no upstream payload)', () => {
    const actor = makeActor();
    actor.start();
    actor.send({ type: 'RUN', jobId: 'job-1' });
    actor.send({ type: 'HEARTBEAT', runId: 'run-1' });
    actor.send({ type: 'USER_STOP' });
    const snap = actor.getSnapshot();
    expect(snap.matches({ cancelled: 'becauseUser' })).toBe(true);
    expect(snap.context.upstreamNodeId).toBeUndefined();
    actor.stop();
  });

  // ADR-015 Phase D Bug 4 Path B: running.CACHE_HIT routes to `cached`
  // and the streaming child is torn down on state exit (xstate v5
  // invoke semantics).  The streaming child stub captures its dispose
  // callback so we can assert it was called.
  it('running → cached on CACHE_HIT and tears down the streaming child', () => {
    let streamingDisposed = false;
    const captureDispose = fromCallback(() => {
      return () => {
        streamingDisposed = true;
      };
    });
    const m = makeNodeRunMachine().provide({
      actors: { streamingActor: captureDispose },
    });
    const actor = createActor(m);
    actor.start();
    actor.send({ type: 'RUN', jobId: 'job-1' });
    actor.send({ type: 'HEARTBEAT', runId: 'run-1' });
    expect(actor.getSnapshot().value).toBe('running');
    // Streaming child should be alive at this point.
    expect(streamingDisposed).toBe(false);

    actor.send({
      type: 'CACHE_HIT',
      cacheKey: 'k1',
      originalRunId: 'run-original-9',
    });
    const snap = actor.getSnapshot();
    expect(snap.value).toBe('cached');
    expect(snap.context.cacheKey).toBe('k1');
    expect(snap.context.originalRunId).toBe('run-original-9');
    // Leaving `running` must dispose the streaming child.
    expect(streamingDisposed).toBe(true);
    actor.stop();
  });

  it('illegal idle→succeeded is a no-op (not honored, no transition)', () => {
    // xstate v5 silently drops events that have no matching transition.
    // This asserts the chart's structural defense — there is no edge
    // from idle to succeeded, so RUN_OK in idle leaves us in idle.
    const actor = makeActor();
    actor.start();
    actor.send({ type: 'RUN_OK', tally: { running: 0, completed: 1, failed: 0 } });
    expect(actor.getSnapshot().value).toBe('idle');
    actor.stop();
  });
});
