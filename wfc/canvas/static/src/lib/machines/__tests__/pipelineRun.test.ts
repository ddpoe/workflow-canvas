/**
 * Vitest suite for the top-level pipeline run machine.
 *
 * Each test injects no-op stubs for `submitPipeline` and `pollNodeStatus`
 * via `.provide` so we never hit the network. The tests prove:
 *
 *   1. Single-Run E2E (mock services drive idle → preflight → submitting → polling → done)
 *   2. Double-click Run is a structural no-op while not in `idle`
 *   3. Sibling-failure cascades into cancelled.becauseUpstream on the spawned nodeRunActor
 */
import { describe, expect, it, vi } from 'vitest';
import { createActor, fromCallback, fromPromise, type EventObject } from 'xstate';
import { makePipelineRunMachine } from '../pipelineRun.machine';

// Trivial no-op SSE stub the spawned nodeRunActors can invoke when they
// hit `running`. We don't care about streaming in pipelineRun tests.
const noopSSE = fromCallback(() => () => {});

function startWith({
  submit,
  poll,
  pipeline,
  onCommitAll,
}: {
  submit?: ReturnType<typeof fromPromise>;
  poll?: ReturnType<typeof fromCallback>;
  pipeline: { nodes: Array<{ id: string }> };
  onCommitAll?: () => void;
}) {
  const m = makePipelineRunMachine().provide({
    actors: {
      submitPipeline:
        submit ??
        (fromPromise(async () => ({ jobId: 'job-1' })) as never),
      pollNodeStatus: (poll ?? noopSSE) as never,
      streamingActor: noopSSE as never,
      // The `preflight → submitting` transition is gated on this actor
      // resolving — replaces the legacy `requestCommitAll` action spy.
      // Tests that just want submit to fire pass an immediate-resolve.
      awaitAllCommitted: fromPromise(async () => {
        onCommitAll?.();
      }),
    },
  });
  const actor = createActor(m, { input: { pipeline } });
  actor.start();
  return actor;
}

describe('pipelineRunActor', () => {
  it('happy path: idle → preflight → submitting → polling → done', async () => {
    let pollSendBack: ((evt: EventObject) => void) | null = null;
    const submit = fromPromise(async () => ({ jobId: 'job-7' }));
    const poll = fromCallback<EventObject, EventObject>(({ sendBack }) => {
      pollSendBack = sendBack;
      return () => {};
    });
    const onCommit = vi.fn();

    const actor = startWith({
      submit,
      poll,
      pipeline: { nodes: [{ id: 'a' }, { id: 'b' }] },
      onCommitAll: onCommit,
    });

    expect(actor.getSnapshot().value).toBe('idle');

    actor.send({ type: 'RUN_CLICKED' });
    // preflight invokes awaitAllCommitted (async) before transitioning
    // to submitting. Post-conditions: commit-await fired, children
    // spawned. The transient `preflight` value is visible to the
    // Stately Inspector but skipped over by tick-after-tick assertion.
    const ctxBefore = actor.getSnapshot().context;
    expect(Object.keys(ctxBefore.nodeRefs).length).toBe(2);

    // First tick: awaitAllCommitted resolves, preflight → submitting.
    // Second tick: submitPipeline resolves, submitting → polling.
    await new Promise(r => setTimeout(r, 0));
    await new Promise(r => setTimeout(r, 0));
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(actor.getSnapshot().value).toBe('polling');
    expect(actor.getSnapshot().context.jobId).toBe('job-7');
    expect(typeof pollSendBack).toBe('function');

    // After submitting.onDone, every spawned child should have received
    // a `RUN` event (jobId attached) and be sitting in `queued`. This is
    // the load-bearing assertion masked by the iteration-1 test gap —
    // without RUN at submitting.onDone, children stay in idle and silently
    // drop polling-emitted HEARTBEAT/RUN_OK/RUN_FAILED events.
    {
      const refs = actor.getSnapshot().context.nodeRefs;
      expect(refs['a'].getSnapshot().value).toBe('queued');
      expect(refs['a'].getSnapshot().context.jobId).toBe('job-7');
      expect(refs['b'].getSnapshot().value).toBe('queued');
    }

    // Polling-driven heartbeat → child flips to `running`, then RUN_OK
    // → `succeeded`. Asserting the child reaches a terminal happy-path
    // state (not just that the parent reaches `done`) is what catches
    // the missing-RUN bug.
    pollSendBack!({
      type: 'NODE_HEARTBEAT',
      nodeId: 'a',
      runId: 'run-a-1',
    });
    pollSendBack!({
      type: 'NODE_RUN_OK',
      nodeId: 'a',
      tally: { running: 0, completed: 1, failed: 0 },
    });
    {
      const refs = actor.getSnapshot().context.nodeRefs;
      expect(refs['a'].getSnapshot().value).toBe('succeeded');
    }

    // Poll service emits a terminal so the machine moves to `done`.
    pollSendBack!({ type: 'PIPELINE_DONE' });
    expect(actor.getSnapshot().value).toBe('done');
    actor.stop();
  });

  it('double-click RUN_CLICKED is a no-op while not in idle', async () => {
    const submit = fromPromise(async () => ({ jobId: 'job-1' }));
    let calls = 0;
    const wrappedSubmit = fromPromise(async () => {
      calls += 1;
      return { jobId: 'job-1' };
    });

    const actor = startWith({
      submit: wrappedSubmit,
      pipeline: { nodes: [{ id: 'a' }] },
    });

    actor.send({ type: 'RUN_CLICKED' });
    actor.send({ type: 'RUN_CLICKED' }); // ignored — not in idle
    actor.send({ type: 'RUN_CLICKED' }); // ignored
    // Two ticks: one for awaitAllCommitted, one for submitPipeline.
    await new Promise(r => setTimeout(r, 0));
    await new Promise(r => setTimeout(r, 0));

    // Only one submission ran.
    expect(calls).toBe(1);
    actor.stop();
    void submit; // keep import-shape consistent
  });

  it('UPSTREAM_FAILED from sibling cascades into a cancelled.becauseUpstream child', async () => {
    let pollSendBack: ((evt: EventObject) => void) | null = null;
    const submit = fromPromise(async () => ({ jobId: 'job-1' }));
    const poll = fromCallback<EventObject, EventObject>(({ sendBack }) => {
      pollSendBack = sendBack;
      return () => {};
    });

    const actor = startWith({
      submit,
      poll,
      pipeline: { nodes: [{ id: 'faulty' }, { id: 'heartbeat' }] },
    });

    actor.send({ type: 'RUN_CLICKED' });
    // Two ticks: awaitAllCommitted resolves, then submitPipeline.
    await new Promise(r => setTimeout(r, 0));
    await new Promise(r => setTimeout(r, 0));
    expect(actor.getSnapshot().value).toBe('polling');

    // Submitting.onDone has fired RUN to both children; both should be
    // in `queued`. RUN_FAILED is only handled in `running`, so push the
    // faulty child through HEARTBEAT first to land it in `running`.
    pollSendBack!({
      type: 'NODE_HEARTBEAT',
      nodeId: 'faulty',
      runId: 'run-7',
    });

    // Faulty fails; pipeline forwards UPSTREAM_FAILED to all siblings
    // currently in idle/queued/running.
    pollSendBack!({
      type: 'NODE_RUN_FAILED',
      nodeId: 'faulty',
      runId: 'run-7',
      error_message: 'boom',
    });
    pollSendBack!({
      type: 'NODE_UPSTREAM_FAILED',
      nodeId: 'heartbeat',
      upstreamNodeId: 'faulty',
      upstreamRunId: 'run-7',
    });

    const refs = actor.getSnapshot().context.nodeRefs;
    // Faulty must reach `failed` — the load-bearing assertion the
    // iteration-1 test gap masked. RUN_FAILED arriving in `idle` would
    // be silently dropped without the `submitting.onDone → RUN`
    // cascade.
    const faultySnap = refs['faulty'].getSnapshot();
    expect(faultySnap.value).toBe('failed');
    expect(faultySnap.context.error_message).toBe('boom');

    const heartbeatSnap = refs['heartbeat'].getSnapshot();
    expect(heartbeatSnap.matches({ cancelled: 'becauseUpstream' })).toBe(true);
    expect(heartbeatSnap.context.upstreamNodeId).toBe('faulty');
    expect(heartbeatSnap.context.upstreamRunId).toBe('run-7');
    actor.stop();
  });

  it('USER_STOP arriving in `submitting` reaches `done` (click-window)', async () => {
    // The Toolbar's Stop button is reachable any time `runState.running`
    // is true — including the sub-second window between RUN_CLICKED and
    // polling start. Before the top-level USER_STOP handler, this
    // event was silently dropped from `preflight` and `submitting`,
    // leaving the actor wedged once polling started against a
    // cancelled-on-the-server job.
    //
    // Hold the submit promise open so the actor stays in `submitting`
    // when USER_STOP lands. A mid-`submitting` USER_STOP MUST land in
    // `done` without depending on the in-flight submitPipeline ever
    // resolving.
    let resolveSubmit!: (out: { jobId: string }) => void;
    const submit = fromPromise<{ jobId: string }, unknown>(
      () =>
        new Promise(res => {
          resolveSubmit = res;
        }),
    );

    const actor = startWith({
      submit: submit as never,
      pipeline: { nodes: [{ id: 'a' }, { id: 'b' }] },
    });

    actor.send({ type: 'RUN_CLICKED' });
    // Tick so awaitAllCommitted resolves and we transition into
    // `submitting`. The submit promise stays unresolved.
    await new Promise(r => setTimeout(r, 0));
    expect(actor.getSnapshot().value).toBe('submitting');

    actor.send({ type: 'USER_STOP' });
    expect(actor.getSnapshot().value).toBe('done');

    // Resolve the orphaned submit promise so the test's pending
    // microtask doesn't leak — the actor has already stopped invoking
    // it, but the underlying Promise is still around.
    resolveSubmit({ jobId: 'unused' });
    actor.stop();
  });

  it('re-run from `done` lands a second submission (RUN_CLICKED in done)', async () => {
    // Regression: after run #1 reached `done`, RUN_CLICKED was dropped
    // because preflight's entry called `.stop()` on the still-tracked
    // spawned children. xstate v5 throws "A non-root actor cannot be
    // stopped directly" from there, which aborts the entry mid-action
    // and silently reverts the transition — the inspector showed the
    // RUN_CLICKED event but no follow-up snapshot. Reassigning
    // `nodeRefs` to a fresh object is enough; xstate auto-stops the
    // orphaned children.
    let pollSendBack: ((evt: EventObject) => void) | null = null;
    let submitCalls = 0;

    const actor = startWith({
      submit: fromPromise(async () => {
        submitCalls += 1;
        return { jobId: `job-${submitCalls}` };
      }) as never,
      poll: fromCallback<EventObject, EventObject>(({ sendBack }) => {
        pollSendBack = sendBack;
        return () => {};
      }) as never,
      pipeline: { nodes: [{ id: 'a' }] },
    });

    actor.send({ type: 'RUN_CLICKED' });
    await new Promise(r => setTimeout(r, 0));
    await new Promise(r => setTimeout(r, 0));
    pollSendBack!({ type: 'PIPELINE_DONE' });
    expect(actor.getSnapshot().value).toBe('done');
    const firstChild = actor.getSnapshot().context.nodeRefs['a'];

    actor.send({ type: 'RUN_CLICKED' });
    await new Promise(r => setTimeout(r, 0));
    await new Promise(r => setTimeout(r, 0));
    expect(actor.getSnapshot().value).toBe('polling');
    expect(submitCalls).toBe(2);

    // The child for run #2 must be a freshly-spawned actor, not the
    // stale ref from run #1 — otherwise polling-driven NODE_* events
    // would target a stopped/cached child.
    const secondChild = actor.getSnapshot().context.nodeRefs['a'];
    expect(secondChild).not.toBe(firstChild);
    expect(secondChild.getSnapshot().value).toBe('queued');

    actor.stop();
  });
});
