/**
 * ADR-015 Phase D Pass 1: parameterized Vitest test that drives every
 * row of the polling-driven behavior catalog through the bridge layer.
 *
 * For each catalog row, every per-node entry of every timeline frame
 * is fed through `runStatusToNodeState` and the resulting event
 * sequence is deep-equaled against the row's `expectedEvents`.  This
 * catches a backend semantic change (e.g. dropping `upstream_node_id`
 * from a cancelled response) as a single named row failure rather
 * than a vague mapping-test breakage.
 *
 * Counterpart: `tests/e2e/behaviors.spec.ts` runs the same catalog
 * through the canvas DOM — together they cover Tier 2 (bridge) and
 * Tier 3 (UI) for every polling row of the bug-class table.
 */
import { describe, expect, it } from 'vitest';
import {
  behaviorCatalog,
  type BehaviorRow,
} from '../../__fixtures__/timelines';
import { runStatusToNodeState, type RunStatusEvent } from '../services';

describe('behavior catalog — bridge layer (Tier 2)', () => {
  for (const [name, row] of Object.entries(behaviorCatalog)) {
    if (row.skipVitest) {
      it.skip(`${name} — skipped (no polling timeline)`, () => {});
      continue;
    }
    it(`${name} — every frame's per-node entry maps to expected event`, () => {
      const expected = row.expectedEvents;
      expect(
        expected,
        `behavior row "${name}" must declare expectedEvents when skipVitest is unset`,
      ).toBeDefined();
      const expectedFrames = expected as (RunStatusEvent | null)[][];

      expect(row.timeline.length).toBe(expectedFrames.length);

      row.timeline.forEach((frame, frameIdx) => {
        const entries = Object.entries(frame.payload.node_states ?? {});
        const expectedFrame = expectedFrames[frameIdx];
        expect(
          entries.length,
          `frame ${frameIdx} of ${name} has ${entries.length} node entries but expectedEvents declares ${expectedFrame.length}`,
        ).toBe(expectedFrame.length);

        entries.forEach(([nodeId, raw], nodeIdx) => {
          const got = runStatusToNodeState(raw);
          expect(
            got,
            `${name} frame ${frameIdx} node "${nodeId}"`,
          ).toEqual(expectedFrame[nodeIdx]);
        });
      });
    });
  }

  it('catalog ships exactly the canonical 13 rows', () => {
    // Pass 1 (8): cancelledByUpstreamFailure, failedWithTraceback,
    // tallyProgression, queuedBehindRunning, errorMidGraph, mixedStatus,
    // partialCacheHit, zeroJobDAG.
    // Pass 2 (4 new): streamingConnecting, liveLogLineAppend,
    // faultOnStream, cancelledByUserMidRun.
    // Pass 3 (1 new): faultMidStream — paced streaming-then-crash
    // distinct from faultOnStream's instant-crash recording.
    expect(Object.keys(behaviorCatalog).sort()).toEqual(
      [
        'cancelledByUpstreamFailure',
        'cancelledByUserMidRun',
        'errorMidGraph',
        'failedWithTraceback',
        'faultMidStream',
        'faultOnStream',
        'liveLogLineAppend',
        'mixedStatus',
        'partialCacheHit',
        'queuedBehindRunning',
        'streamingConnecting',
        'tallyProgression',
        'zeroJobDAG',
      ].sort(),
    );
  });

  it('every non-skipped row references a known fixtureKey', () => {
    // App.svelte::seedFixture only knows these keys; a typo here would
    // surface as an empty canvas in the Playwright spec — catch it at
    // the Vitest layer instead.
    const known = new Set([
      'single-method',
      'single-method-streaming',
      'two-methods',
      'two-methods-cancel',
      'three-methods-chain',
      'cache-hit-method',
      'method-and-system',
    ]);
    for (const [name, row] of Object.entries(behaviorCatalog)) {
      expect(known.has(row.fixtureKey), `${name} fixtureKey "${row.fixtureKey}"`).toBe(true);
    }
  });
});

// Lightweight type-level smoke: BehaviorRow is exported and the catalog
// values conform to it.  Compile-time check; the runtime body is empty.
const _typeCheck: BehaviorRow = behaviorCatalog.failedWithTraceback;
void _typeCheck;
