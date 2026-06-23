/**
 * ADR-015 Phase D US-2 contract test.
 *
 * Each timeline is typed against the GENERATED `WorkflowStatusResponse`
 * from `src/lib/types/api.ts`, so renaming/removing a field in
 * `wfc/canvas/server.py::NodeRunState` makes this test fail at tsc
 * time.  The runtime assertions below are deliberately structural —
 * they catch a malformed fixture frame, but the type contract is the
 * load-bearing guarantee.
 */
import { describe, expect, it } from 'vitest';
import {
  cacheHitTimeline,
  multiNodeTimeline,
  normalSucceededTimeline,
  systemNodeTimeline,
} from '../timelines';

describe('shared timeline fixtures', () => {
  it('all four timelines have at least one frame and required fields', () => {
    for (const [name, t] of [
      ['normalSucceeded', normalSucceededTimeline],
      ['cacheHit', cacheHitTimeline],
      ['multiNode', multiNodeTimeline],
      ['systemNode', systemNodeTimeline],
    ] as const) {
      expect(t.length, `${name} has frames`).toBeGreaterThan(0);
      for (const f of t) {
        expect(typeof f.delayMs, `${name}.delayMs`).toBe('number');
        expect(f.payload).toHaveProperty('job_id');
        expect(f.payload).toHaveProperty('overall_status');
        expect(f.payload).toHaveProperty('node_states');
      }
    }
  });

  it('cacheHitTimeline carries cache_hit + original_run_id on its frame', () => {
    const frame = cacheHitTimeline[0];
    const node = frame.payload.node_states.method_a;
    expect(node?.cache_hit).toBe(true);
    expect(node?.original_run_id).toBe('run-original-42');
    expect(node?.cache_key).toBe('cache-key-abc');
  });
});
