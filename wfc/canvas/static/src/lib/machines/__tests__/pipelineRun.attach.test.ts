/**
 * The pipeline machine's ATTACH event: History's "Watch in canvas".
 *
 * ATTACH puts the canvas on a pipeline that is already running, as if the
 * user had never left the builder. From `idle` or `done` it goes straight
 * to `polling` with the pipeline's id, spawning the node actors as
 * preflight does and skipping preflight and submit; the real poller's
 * first tick then drives each node to the state the status route reports,
 * and a running node starts its log stream. From `polling` it switches:
 * the current run's node actors and poller stop, and nothing is cancelled.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createActor, fromCallback, fromPromise, type EventObject } from 'xstate';
import { makePipelineRunMachine } from '../pipelineRun.machine';
import { makeNodeRunMachine } from '../nodeRun.machine';
import { pollNodeStatus } from '../services';

/** Every log stream the node actors open: its run id, and whether it ended. */
type Stream = { runId: string; stopped: boolean };

function streamingRecorder(streams: Stream[]) {
  return fromCallback<EventObject, { runId: string }>(({ input }) => {
    const s: Stream = { runId: input.runId, stopped: false };
    streams.push(s);
    return () => {
      s.stopped = true;
    };
  });
}

/** Every poller started: the id it polls, and whether it was stopped. */
type Poller = { jobId: string | null; stopped: boolean };

function pollRecorder(pollers: Poller[]) {
  return fromCallback<EventObject, { jobId: string | null }>(({ input }) => {
    const p: Poller = { jobId: input.jobId, stopped: false };
    pollers.push(p);
    return () => {
      p.stopped = true;
    };
  });
}

function start(opts: {
  poll?: unknown;
  streams: Stream[];
  submit?: unknown;
}) {
  const m = makePipelineRunMachine().provide({
    actors: {
      submitPipeline: (opts.submit ??
        fromPromise(async () => ({ jobId: 'own-run' }))) as never,
      pollNodeStatus: (opts.poll ?? pollNodeStatus) as never,
      streamingActor: streamingRecorder(opts.streams) as never,
      // The node actors are where streams open (root.ts wires it the same way).
      nodeRunActor: makeNodeRunMachine().provide({
        actors: { streamingActor: streamingRecorder(opts.streams) as never },
      }) as never,
      awaitAllCommitted: fromPromise(async () => {}),
    },
  });
  const actor = createActor(m, { input: { pipeline: { nodes: [] } } });
  actor.start();
  return actor;
}

/** The status route's answer for the watched pipeline: one node per state. */
const STATUS = {
  overall_status: 'running',
  thread_alive: true,
  node_states: {
    a: {
      status: 'running',
      run_ids: ['run-a'],
      tally: { running: 1, completed: 0, failed: 0 },
    },
    b: {
      status: 'completed',
      run_ids: ['run-b'],
      tally: { running: 0, completed: 1, failed: 0 },
    },
    c: { status: 'failed', run_ids: ['run-c'], error: 'boom' },
    d: {
      status: 'completed',
      run_ids: ['run-d'],
      cache_hit: true,
      cache_key: 'key-d',
      original_run_id: 'run-orig',
    },
  },
};

const WATCHED = {
  nodes: [
    { id: 'sel', type: 'input_selector' },
    { id: 'a' },
    { id: 'b' },
    { id: 'c' },
    { id: 'd' },
  ],
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async (url: string) => {
    if (String(url).startsWith('/api/workflow/status/')) {
      return { ok: true, status: 200, json: async () => STATUS };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Run the canvas's own pipeline to `done`, so ATTACH starts from there. */
async function reachDone(actor: ReturnType<typeof start>) {
  actor.send({ type: 'RUN_CLICKED', pipeline: { nodes: [{ id: 'own' }] } });
  await new Promise(r => setTimeout(r, 0));
  await new Promise(r => setTimeout(r, 0));
  expect(actor.getSnapshot().value).toBe('polling');
  actor.send({ type: 'PIPELINE_DONE' });
  expect(actor.getSnapshot().value).toBe('done');
}

describe('pipelineRun ATTACH (Watch in canvas)', () => {
  it.each(['idle', 'done'] as const)(
    'from %s: goes straight to polling and each node reaches its reported state',
    async from => {
      const streams: Stream[] = [];
      let submits = 0;
      const submit = fromPromise(async () => {
        submits += 1;
        return { jobId: 'own-run' };
      });
      const actor = start({ streams, submit });
      if (from === 'done') await reachDone(actor);
      const submitsBefore = submits;
      const visited: unknown[] = [];
      const sub = actor.subscribe(s => visited.push(s.value));

      actor.send({ type: 'ATTACH', jobId: 'pipe-1', pipeline: WATCHED });

      // Straight to polling under the watched pipeline's id; no preflight,
      // no submit.
      const snap = actor.getSnapshot();
      expect(snap.value).toBe('polling');
      expect(snap.context.jobId).toBe('pipe-1');
      expect(snap.context.pipeline).toBe(WATCHED);
      await new Promise(r => setTimeout(r, 0));
      expect(submits).toBe(submitsBefore);
      expect(visited).not.toContain('preflight');
      expect(visited).not.toContain('submitting');
      sub.unsubscribe();
      expect(fetchMock).toHaveBeenCalledWith('/api/workflow/status/pipe-1');
      // One node actor per method node, none for the input selector.
      expect(Object.keys(snap.context.nodeRefs).sort()).toEqual(['a', 'b', 'c', 'd']);

      // The poller's first tick drives every node to what the route reports.
      await vi.waitFor(() => {
        const refs = actor.getSnapshot().context.nodeRefs;
        expect(refs.a.getSnapshot().value).toBe('running');
        expect(refs.b.getSnapshot().value).toBe('succeeded');
        expect(refs.c.getSnapshot().value).toBe('failed');
        expect(refs.d.getSnapshot().value).toBe('cached');
      });
      const refs = actor.getSnapshot().context.nodeRefs;
      expect(refs.a.getSnapshot().context.runId).toBe('run-a');
      expect(refs.c.getSnapshot().context.error_message).toBe('boom');
      expect(refs.d.getSnapshot().context.cacheKey).toBe('key-d');

      // The running node's stream is open; the cached node never streamed.
      const streamA = streams.find(s => s.runId === 'run-a');
      expect(streamA).toBeDefined();
      expect(streamA!.stopped).toBe(false);
      expect(streams.some(s => s.runId === 'run-d')).toBe(false);
      actor.stop();
    },
  );

  it('from polling: switches to the new pipeline, detaching the current one without cancelling it', async () => {
    const streams: Stream[] = [];
    const pollers: Poller[] = [];
    let pollSendBack: ((e: EventObject) => void) | null = null;
    const poll = fromCallback<EventObject, { jobId: string | null }>(({ input, sendBack }) => {
      const p: Poller = { jobId: input.jobId, stopped: false };
      pollers.push(p);
      pollSendBack = sendBack;
      return () => {
        p.stopped = true;
      };
    });
    const actor = start({ streams, poll });

    // Watching pipeline A, with node `a` running and streaming.
    actor.send({ type: 'ATTACH', jobId: 'pipe-A', pipeline: { nodes: [{ id: 'a' }] } });
    pollSendBack!({ type: 'NODE_HEARTBEAT', nodeId: 'a', runId: 'run-a' });
    const oldA = actor.getSnapshot().context.nodeRefs.a;
    expect(oldA.getSnapshot().value).toBe('running');
    expect(streams.map(s => s.runId)).toEqual(['run-a']);

    // The user said yes: watch pipeline B.
    actor.send({ type: 'ATTACH', jobId: 'pipe-B', pipeline: { nodes: [{ id: 'x' }] } });

    const snap = actor.getSnapshot();
    expect(snap.value).toBe('polling');
    expect(snap.context.jobId).toBe('pipe-B');
    expect(Object.keys(snap.context.nodeRefs)).toEqual(['x']);
    expect(snap.context.nodeRefs.x.getSnapshot().value).toBe('queued');
    // A's poller, node actor and log stream are stopped...
    expect(pollers.map(p => [p.jobId, p.stopped])).toEqual([
      ['pipe-A', true],
      ['pipe-B', false],
    ]);
    expect(oldA.getSnapshot().status).toBe('stopped');
    expect(streams[0].stopped).toBe(true);
    // ...and A is not cancelled: nothing was sent to the server at all.
    expect(fetchMock).not.toHaveBeenCalled();
    actor.stop();
  });

  it('is not taken while the canvas is starting its own run', async () => {
    let resolveSubmit!: (out: { jobId: string }) => void;
    const submit = fromPromise<{ jobId: string }, unknown>(
      () => new Promise(res => { resolveSubmit = res; }),
    );
    const actor = start({ streams: [], poll: pollRecorder([]), submit });
    actor.send({ type: 'RUN_CLICKED', pipeline: { nodes: [{ id: 'own' }] } });
    await new Promise(r => setTimeout(r, 0));
    expect(actor.getSnapshot().value).toBe('submitting');

    actor.send({ type: 'ATTACH', jobId: 'pipe-1', pipeline: WATCHED });
    expect(actor.getSnapshot().value).toBe('submitting');
    expect(Object.keys(actor.getSnapshot().context.nodeRefs)).toEqual(['own']);
    resolveSubmit({ jobId: 'own-run' });
    actor.stop();
  });
});
