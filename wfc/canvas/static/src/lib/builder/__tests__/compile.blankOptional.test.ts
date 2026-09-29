/**
 * A blank optional parameter is absent from every compiled row.
 *
 * An optional value committed blank in the inspector arrives as `null` (a
 * number) or `''` (a string or enum). The compiled document must omit it on
 * the base row, on each sweep variant and on each per-sample override
 * variant, never send it as `null`, so the method runs its declared default
 * exactly as when the box was never touched. The lock summary tells the user
 * such a parameter "runs its default"; this is what makes that true.
 */
import { describe, it, expect } from 'vitest';
import { compilePipelineToJSON, type AuthoringState } from '../compile';
import type { CanvasNodeData } from '../../shared/types';
import type { Node } from '@xyflow/svelte';

function methodNode(data: Partial<CanvasNodeData>): Node<CanvasNodeData> {
  return {
    id: 'm',
    type: 'custom',
    position: { x: 0, y: 0 },
    data: {
      label: 'm',
      method: 'filter',
      module: 'mod',
      color: '#ccc',
      inputs: [],
      outputs: [{ name: 'out', type: 'csv' }],
      params: [],
      paramValues: {},
      runStatus: 'idle',
      expanded: false,
      nodeType: 'method',
      ...data,
    } as CanvasNodeData,
  };
}

function compile(node: Node<CanvasNodeData>, samples = ['A', 'B']) {
  const state: AuthoringState = {
    name: 'blank-optional', nodes: [node], edges: [], samples,
    pipelineVariables: {}, boundVariables: {},
  };
  return compilePipelineToJSON(state);
}

/** Every params dict the engine will read for node `m`: base plus every variant. */
function everyRow(out: ReturnType<typeof compile>): Record<string, unknown>[] {
  const node = out.nodes.find(n => n.id === 'm')!;
  return [node.params, ...Object.values(out.param_sets?.m ?? {})];
}

// A committed-blank number, a committed-blank string, and a never-touched
// parameter (no key at all) sit beside one real value on every shape.
const BLANKS = { threshold: 0.5, max_area: null, label: '' };

describe('compile: a blank optional parameter is absent on every row', () => {
  it('plain row: the base params omit the blanks', () => {
    const out = compile(methodNode({ paramValues: { ...BLANKS } }));
    expect(out.nodes[0].params).toEqual({ threshold: 0.5 });
  });

  it('sweep variants: every variant omits the blanks, single- and multi-param sweeps', () => {
    const single = compile(methodNode({
      paramValues: { ...BLANKS },
      variants: { threshold: { v1: 0.5, v2: 0.7 } },
    }));
    expect(single.param_sets).toEqual({ m: { v1: { threshold: 0.5 }, v2: { threshold: 0.7 } } });

    const multi = compile(methodNode({
      paramValues: { ...BLANKS },
      variants: { threshold: { lo: 0.1, hi: 0.9 }, bins: { few: 5, many: null } },
    }));
    for (const row of everyRow(multi)) {
      expect(Object.values(row)).not.toContain(null);
      expect(Object.values(row)).not.toContain('');
      expect(row).not.toHaveProperty('max_area');
      expect(row).not.toHaveProperty('label');
    }
    // The variant whose own sweep value is blank runs that parameter's default too.
    const withBlankBins = Object.values(multi.param_sets!.m).filter(r => !('bins' in r));
    expect(withBlankBins).toHaveLength(2);
  });

  it('per-sample override variants omit the blanks, including a blank override value', () => {
    const out = compile(methodNode({
      paramValues: { ...BLANKS },
      sampleOverrides: { A: { threshold: 0.9, label: '' }, B: { max_area: null, threshold: 0.8 } },
      sampleVariants: { A: { threshold: { s1: 0.95 } } },
    }));
    const rows = everyRow(out);
    expect(rows.length).toBeGreaterThan(1);
    for (const row of rows) {
      expect(Object.values(row)).not.toContain(null);
      expect(Object.values(row)).not.toContain('');
    }
    expect(out.param_sets).toEqual({
      m: { A__o1: { threshold: 0.95 }, B__o2: { threshold: 0.8 } },
    });
  });
});
