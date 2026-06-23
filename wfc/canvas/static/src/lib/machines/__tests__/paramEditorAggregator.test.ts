/**
 * Vitest suite for the paramEditorAggregator (ADR-016 Phase 2 expand).
 *
 * Tests the US-2 commit-before-run scenario end-to-end with real
 * paramEditorActor children — replacing the 0.2.8 microtask race
 * (`requestCommitAll(); await new Promise(r => setTimeout(r, 0))`)
 * with an explicit `idle → committingAll → allCommitted` traversal.
 *
 * Two tests, both load-bearing:
 *
 *   T1 (US-2 root cause): aggregator with two editing children.
 *     Send COMMIT_ALL. Wire CHILD_SETTLED bridges from each child's
 *     subscription. Assert aggregator reaches `allCommitted` ONLY
 *     after both children's currentValue carries the typed draft.
 *     The bridge mirrors what root.ts does at runtime; without it
 *     the aggregator would hang (proving the bridge is load-bearing).
 *
 *   T2 (no editing children): COMMIT_ALL when every child is in
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
 * Wire the bridge that root.ts will install at runtime: every time a
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
  it('COMMIT_ALL propagates to editing children and reaches allCommitted only after every child settles (US-2 race fix)', async () => {
    const aggregator = createActor(makeParamEditorAggregatorMachine());
    aggregator.start();

    // Spawn two children, drive both into `editing` with distinct
    // drafts. This reproduces the 0.2.8 scenario: user types in two
    // rows, never blurs/Enters, clicks Run.
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
    // that fails under the 0.2.8 race (Run submitted before the
    // microtask flushed).
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

  it('aggregator-driven commit forwards committed value to parent BEFORE allCommitted (closes D-15 gap; would fail under legacy commitRow one-shot)', async () => {
    // This is the test that would have caught D-15 before it landed.
    //
    // Scenario: user types into a base row, never blurs/Enters, clicks
    // Lock All / Run. The aggregator drives COMMIT via COMMIT_ALL —
    // bypassing any UI-side commitRow handler. Pre-D-15 the parent
    // callback (`onBaseChange`) was wired ONLY through commitRow's
    // one-shot subscription — so an aggregator-driven commit updated
    // the actor's context but never reached `data.paramValues`. The
    // Run-button payload would carry stale values (the exact race
    // US-2 was supposed to eliminate).
    //
    // The fix (D-15) is a permanent subscription installed at spawn
    // time that forwards `currentValue` upstream whenever the actor
    // enters `committed`. This test reproduces the spawn-time
    // subscription pattern from ValueList.svelte#spawnBaseActor and
    // asserts the parent callback fires with the typed value BEFORE
    // the aggregator's `allCommitted` is observable. Removing the
    // spawn-time subscription (reverting to a commitRow-only one-shot)
    // would make this assertion fail because aggregator COMMIT_ALL
    // never visits commitRow.
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
    // ValueList.svelte#spawnBaseActor: on every transition into
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
    // assertion that fails under a legacy commitRow-only one-shot.
    // Under the legacy pattern, COMMIT_ALL bypasses commitRow, so
    // baseChanges would still be empty after the aggregator settles.
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
