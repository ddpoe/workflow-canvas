/**
 * Vitest suite for the paramEditorAggregator.
 *
 * Tests the commit-before-run scenario end-to-end with real
 * paramEditorActor children through an explicit
 * `idle → committingAll → allCommitted` traversal.
 *
 *   Two editing children: send COMMIT_ALL. Wire CHILD_SETTLED bridges
 *     from each child's subscription. Assert aggregator reaches
 *     `allCommitted` ONLY after both children's currentValue carries
 *     the typed draft. The bridge mirrors what root.ts does at runtime;
 *     without it the aggregator would hang (proving the bridge is
 *     load-bearing).
 *
 *   Forward before allCommitted: an aggregator-driven commit reaches
 *     the parent callback before the aggregator reports `allCommitted`.
 *
 *   No editing children: COMMIT_ALL when every child is in
 *     viewing/committed transitions straight to allCommitted with
 *     no pending — protects callers from hanging when nothing is
 *     dirty (the natural Run-button case post-commit).
 */
import { describe, expect, it } from 'vitest';
import { createActor } from 'xstate';
import {
  makeParamEditorAggregatorMachine,
  isChildEditing,
  isChildSettled,
  type ChildActor,
} from '../paramEditorAggregator.machine';
import {
  makeParamEditorMachine,
  type ParamEditorActor,
  type ParamEditorInput,
} from '../paramEditor.machine';

function spawnEditor(input: ParamEditorInput): ParamEditorActor {
  const actor = createActor(makeParamEditorMachine(), { input });
  actor.start();
  return actor as unknown as ParamEditorActor;
}

/**
 * Wire the bridge that root.ts installs at runtime: every time a
 * registered child's snapshot changes, if it's now settled, send
 * CHILD_SETTLED to the aggregator. Returns the unsubscribe.
 */
function bridgeChildToAggregator(
  aggregator: ReturnType<typeof createActor>,
  id: string,
  child: ChildActor,
): () => void {
  let lastEditing = isChildEditing(child);
  const sub = child.subscribe(() => {
    const editingNow = isChildEditing(child);
    if (lastEditing && !editingNow && isChildSettled(child)) {
      aggregator.send({ type: 'CHILD_SETTLED', id });
    }
    lastEditing = editingNow;
  });
  return () => sub.unsubscribe();
}

describe('paramEditorAggregator', () => {
  it('COMMIT_ALL propagates to editing children and reaches allCommitted only after every child settles', async () => {
    const aggregator = createActor(makeParamEditorAggregatorMachine());
    aggregator.start();

    // Spawn two children, drive both into `editing` with distinct
    // drafts. This reproduces the commit-before-run scenario: user
    // types in two rows, never blurs/Enters, clicks Run.
    const a = spawnEditor({
      nodeId: 'node_1',
      paramName: 'sample_name',
      paramType: 'string',
      required: false,
      currentValue: 'old-a',
    });
    const b = spawnEditor({
      nodeId: 'node_1',
      paramName: 'output_dir',
      paramType: 'string',
      required: false,
      currentValue: 'old-b',
    });

    a.send({ type: 'EDIT' });
    a.send({ type: 'CHANGE_VALUE', value: 'new-a' });
    b.send({ type: 'EDIT' });
    b.send({ type: 'CHANGE_VALUE', value: 'new-b' });

    expect(a.getSnapshot().value).toBe('editing');
    expect(b.getSnapshot().value).toBe('editing');

    aggregator.send({ type: 'REGISTER', id: 'a', actor: a });
    aggregator.send({ type: 'REGISTER', id: 'b', actor: b });

    const cleanupA = bridgeChildToAggregator(aggregator, 'a', a);
    const cleanupB = bridgeChildToAggregator(aggregator, 'b', b);

    // Pre-commit: aggregator is idle, NOT allCommitted — there are
    // editing children.
    expect(aggregator.getSnapshot().value).toBe('idle');

    // The Run-button preflight does this:
    aggregator.send({ type: 'COMMIT_ALL' });
    // The action sends COMMIT to both children synchronously. The
    // children's `committing → committed` is async (fromPromise), so
    // the aggregator should be in `committingAll` here.
    expect(aggregator.getSnapshot().value).toBe('committingAll');
    expect(aggregator.getSnapshot().context.pending.size).toBe(2);

    // Yield for both children's coerce promises to resolve. Each
    // child's transition into `committed` fires the bridge, which
    // sends CHILD_SETTLED to the aggregator. After both, aggregator
    // hits `allCommitted`.
    await new Promise(r => setTimeout(r, 0));

    expect(aggregator.getSnapshot().value).toBe('allCommitted');
    expect(aggregator.getSnapshot().context.pending.size).toBe(0);

    // Final values carry the typed drafts — this is the assertion
    // that fails if Run submits before the children commit.
    expect(a.getSnapshot().context.currentValue).toBe('new-a');
    expect(b.getSnapshot().context.currentValue).toBe('new-b');
    expect(a.getSnapshot().value).toBe('committed');
    expect(b.getSnapshot().value).toBe('committed');

    cleanupA();
    cleanupB();
    a.stop();
    b.stop();
    aggregator.stop();
  });

  it('aggregator-driven commit forwards committed value to parent BEFORE allCommitted, without passing through commitRow', async () => {
    // Scenario: user types into a base row, never blurs/Enters, clicks
    // Lock All / Run. The aggregator drives COMMIT via COMMIT_ALL —
    // bypassing any UI-side commitRow handler. A parent callback
    // (`onBaseChange`) wired only through commitRow's one-shot
    // subscription would see the actor's context update but never
    // reach `data.paramValues`, and the Run-button payload would carry
    // stale values.
    //
    // The forward is a permanent subscription installed at spawn time
    // that sends `currentValue` upstream whenever the actor enters
    // `committed`. This test reproduces the spawn-time subscription
    // pattern from rowActors.ts#spawnBaseActor and asserts the parent
    // callback fires with the typed value BEFORE the aggregator's
    // `allCommitted` is observable. Without the spawn-time subscription
    // (a commitRow-only one-shot) this assertion fails because
    // aggregator COMMIT_ALL never visits commitRow.
    const aggregator = createActor(makeParamEditorAggregatorMachine());
    aggregator.start();

    const a = spawnEditor({
      nodeId: 'node_1',
      paramName: 'sample_name',
      paramType: 'string',
      required: false,
      currentValue: 'old',
    });

    // Replicate the spawn-time forward subscription from
    // rowActors.ts#spawnBaseActor: on every transition into
    // `committed`, forward `currentValue` to the parent callback.
    // De-dup via lastForwarded so RESET_TO echoes don't loop.
    const baseChanges: unknown[] = [];
    let lastForwarded: unknown = 'old';
    const onBaseChange = (value: unknown): void => {
      baseChanges.push(value);
    };
    const fwdSub = a.subscribe(snap => {
      if ((snap.value as string) !== 'committed') return;
      const cv = snap.context.currentValue;
      if (cv === lastForwarded) return;
      lastForwarded = cv;
      onBaseChange(cv);
    });

    a.send({ type: 'EDIT' });
    a.send({ type: 'CHANGE_VALUE', value: 'typed-but-not-blurred' });
    expect(a.getSnapshot().value).toBe('editing');
    expect(baseChanges).toEqual([]); // No commit yet.

    aggregator.send({ type: 'REGISTER', id: 'a', actor: a });

    // Capture aggregator value at the moment the actor reaches
    // `committed`. The watcher subscribes BEFORE the
    // bridgeChildToAggregator below so it fires first in subscription
    // order — this lets us observe the aggregator state at the moment
    // the spawn-time forward fires, before the bridge's CHILD_SETTLED
    // send moves the aggregator to `allCommitted`.
    let baseChangesAtCommitMoment: unknown[] = [];
    let aggregatorValueAtCommitMoment: string | null = null;
    const watcher = a.subscribe(snap => {
      if ((snap.value as string) === 'committed') {
        baseChangesAtCommitMoment = [...baseChanges];
        aggregatorValueAtCommitMoment =
          aggregator.getSnapshot().value as string;
      }
    });
    const cleanupBridge = bridgeChildToAggregator(aggregator, 'a', a);

    aggregator.send({ type: 'COMMIT_ALL' });
    expect(aggregator.getSnapshot().value).toBe('committingAll');

    // Yield for the child's coerce promise to resolve. The committing
    // → committed transition fires (a) the spawn-time forward
    // subscription (parent callback), then (b) the bridge's
    // CHILD_SETTLED send (aggregator → allCommitted).
    await new Promise(r => setTimeout(r, 0));

    // The parent callback fired with the typed value — this is the
    // assertion that fails with a commitRow-only one-shot: COMMIT_ALL
    // bypasses commitRow, so baseChanges would still be empty after
    // the aggregator settles.
    expect(baseChanges).toEqual(['typed-but-not-blurred']);
    expect(baseChangesAtCommitMoment).toEqual(['typed-but-not-blurred']);

    // The forward fired BEFORE the aggregator reached allCommitted.
    // At the moment the actor entered `committed` the aggregator was
    // still in `committingAll` — the bridge's CHILD_SETTLED send
    // hadn't fired yet. This proves the order: parent dict update
    // happens first, so any consumer awaiting allCommitted reads a
    // self-consistent post-commit state.
    expect(aggregatorValueAtCommitMoment).toBe('committingAll');

    expect(aggregator.getSnapshot().value).toBe('allCommitted');
    expect(a.getSnapshot().context.currentValue).toBe(
      'typed-but-not-blurred',
    );

    fwdSub.unsubscribe();
    watcher.unsubscribe();
    cleanupBridge();
    a.stop();
    aggregator.stop();
  });

  it('COMMIT_ALL with no editing children transitions straight to allCommitted (no hang)', () => {
    const aggregator = createActor(makeParamEditorAggregatorMachine());
    aggregator.start();

    // Children are registered but in `viewing` (not editing).
    const a = spawnEditor({
      nodeId: 'node_1',
      paramName: 'sample_name',
      paramType: 'string',
      required: false,
      currentValue: 'committed',
    });
    aggregator.send({ type: 'REGISTER', id: 'a', actor: a });

    expect(a.getSnapshot().value).toBe('viewing');

    aggregator.send({ type: 'COMMIT_ALL' });
    // No editing children → straight to allCommitted, no waiting.
    expect(aggregator.getSnapshot().value).toBe('allCommitted');

    a.stop();
    aggregator.stop();
  });
});
