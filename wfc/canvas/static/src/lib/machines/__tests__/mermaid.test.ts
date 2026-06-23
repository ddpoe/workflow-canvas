/**
 * Vitest smoke test for the Mermaid generator.
 *
 * Imports the generator's `buildMermaid` directly (no file I/O,
 * no subprocess). Asserts:
 *   - each diagram contains at least N transition lines
 *   - no `[object Object]` substring (the v3 toDirectedGraph bug
 *     fixed in review iteration 1 surfaced as that literal in
 *     pipelineRun.mmd)
 *   - the cancelled substates are qualified (e.g.
 *     `cancelled.becauseUpstream`)
 */
import { describe, expect, it } from 'vitest';
import { buildMermaid } from '../../../../scripts/gen-machine-mermaid';
import { makeNodeRunMachine } from '../nodeRun.machine';
import { makeStreamingMachine } from '../streaming.machine';
import { makePipelineRunMachine } from '../pipelineRun.machine';

function transitionLineCount(out: string): number {
  return out.split('\n').filter(l => /-->/.test(l) && !l.includes('[*]')).length;
}

describe('mermaid generator', () => {
  it('nodeRun emits ≥ 5 real transitions and qualifies cancelled substates', () => {
    const out = buildMermaid('nodeRun', makeNodeRunMachine() as never);
    expect(out).not.toContain('[object Object]');
    expect(transitionLineCount(out)).toBeGreaterThanOrEqual(5);
    // Cancelled substates must be qualified — bare `becauseUpstream`
    // would conflict if other parents ever introduce identically-named
    // children.
    expect(out).toContain('cancelled.becauseUpstream');
    expect(out).toContain('cancelled.becauseUser');
  });

  it('streaming emits ≥ 3 real transitions including SSE events and named-guard annotations', () => {
    const out = buildMermaid('streaming', makeStreamingMachine() as never);
    expect(out).not.toContain('[object Object]');
    expect(transitionLineCount(out)).toBeGreaterThanOrEqual(3);
    expect(out).toContain('SSE_LINE');
    expect(out).toContain('SSE_TERMINAL');
    // Named guards on the SSE_TERMINAL fan-out must surface in the diagram
    // — without this, the three branches collapse to indistinguishable edges.
    expect(out).toContain('isSuccessStatus');
  });

  it('pipelineRun emits ≥ 3 real transitions including RUN_CLICKED', () => {
    const out = buildMermaid('pipelineRun', makePipelineRunMachine() as never);
    expect(out).not.toContain('[object Object]');
    expect(transitionLineCount(out)).toBeGreaterThanOrEqual(3);
    expect(out).toContain('RUN_CLICKED');
  });
});
