/**
 * Vitest suite for compile.ts pipeline-variables round-trip (Track 2,
 * ADR-015). Behavior-first per project test policy.
 *
 * Load-bearing assertion (US-4): authoring state with pipeline variables
 * + per-row bindings compiles to JSON with `variables` block + {$var}
 * refs in node.params and param_sets variants; parsing the SAME JSON
 * back yields identical pipelineVariables and identical boundVariables
 * markers — proving the History reload chip-restoration path is
 * structurally sound at the data layer.
 */
import { describe, it, expect } from 'vitest';
import {
  compilePipelineToJSON,
  parsePipelineJSON,
  type AuthoringState,
} from '../compile';
import type { CanvasNodeData } from '../types';
import type { Node, Edge } from '@xyflow/svelte';

function mkMethodNode(
  id: string,
  paramValues: Record<string, unknown>,
  variants: Record<string, Record<string, unknown>> = {},
): Node<CanvasNodeData> {
  return {
    id,
    type: 'custom',
    position: { x: 0, y: 0 },
    data: {
      label: id,
      method: 'do_thing',
      module: 'mymod',
      color: '#ccc',
      inputs: [],
      outputs: [{ name: 'out', type: 'csv' }],
      params: [],
      paramValues,
      runStatus: 'idle',
      expanded: false,
      nodeType: 'method',
      variants,
    },
  };
}

describe('compile.ts variables round-trip (US-4 load-bearing)', () => {
  it('compile emits {$var} refs for bound base params and a variables block', () => {
    const nodeA = mkMethodNode('node_1', { col: 'literal_value' });
    const nodeB = mkMethodNode('node_2', { col: 'literal_value' });
    const state: AuthoringState = {
      name: 'p',
      nodes: [nodeA, nodeB],
      edges: [],
      samples: ['s1'],
      pipelineVariables: { column_map: { type: 'dict', value: { p27: 'R1_p27' } } },
      boundVariables: {
        'node_1::col': 'column_map',
        'node_2::col': 'column_map',
      },
    };
    const out = compilePipelineToJSON(state);
    expect(out.variables).toEqual({
      column_map: { type: 'dict', value: { p27: 'R1_p27' } },
    });
    const n1 = out.nodes.find(n => n.id === 'node_1')!;
    const n2 = out.nodes.find(n => n.id === 'node_2')!;
    expect(n1.params).toEqual({ col: { $var: 'column_map' } });
    expect(n2.params).toEqual({ col: { $var: 'column_map' } });
  });

  it('round-trip identity: compile → parse reproduces variables + bindings (chips restoration)', () => {
    const nodeA = mkMethodNode('node_1', { col: 'whatever' });
    const nodeB = mkMethodNode('node_2', { col: 'whatever' });
    const nodeC = mkMethodNode('node_3', { col: 'literal_kept' });
    const original: AuthoringState = {
      name: 'p',
      nodes: [nodeA, nodeB, nodeC],
      edges: [],
      samples: ['s1'],
      pipelineVariables: {
        column_map: { type: 'dict', value: { p27: 'R1_p27', CycD1: 'R1_CycD1' } },
      },
      boundVariables: {
        'node_1::col': 'column_map',
        'node_2::col': 'column_map',
      },
    };
    const json = compilePipelineToJSON(original);
    const parsed = parsePipelineJSON(json);

    // pipelineVariables identity — same shape and values as authored.
    expect(parsed.pipelineVariables).toEqual(original.pipelineVariables);

    // boundVariables identity — bound rows reproduced; node_3 stays
    // literal (no binding marker).
    expect(parsed.boundVariables).toEqual({
      'node_1::col': 'column_map',
      'node_2::col': 'column_map',
    });
    expect(parsed.boundVariables['node_3::col']).toBeUndefined();
  });

  it('round-trip: variant-level binding survives compile + parse', () => {
    // node_1 has a single-param sweep over `col` with two variants; the
    // v2 variant is bound to a pipeline variable. Round-trip must
    // reproduce the variant-level binding marker keyed
    // `${nodeId}::${paramName}::${variantName}`.
    const nodeA = mkMethodNode(
      'node_1',
      { col: 'base_val' },
      { col: { v1: 'literal_v1', v2: 'overridden_at_compile' } },
    );
    const original: AuthoringState = {
      name: 'p',
      nodes: [nodeA],
      edges: [],
      samples: ['s1'],
      pipelineVariables: { c: { type: 'str', value: 'X' } },
      boundVariables: { 'node_1::col::v2': 'c' },
    };
    const json = compilePipelineToJSON(original);
    // Variant v2's value in the wire format must be a $var ref now,
    // proving the variant-level binding was applied.
    expect(json.param_sets!['node_1']['v2']).toEqual({ col: { $var: 'c' } });
    const parsed = parsePipelineJSON(json);
    expect(parsed.boundVariables['node_1::col::v2']).toBe('c');
    expect(parsed.pipelineVariables).toEqual(original.pipelineVariables);
  });

  it('parse handles JSON with no variables block (legacy pipelines)', () => {
    // Backward-compat: pipelines authored before ADR-015 have no
    // `variables` field and no $var refs. parsePipelineJSON must return
    // empty maps rather than throwing.
    const legacy = {
      name: 'legacy',
      nodes: [{
        id: 'node_1',
        type: 'method' as const,
        method: 'm',
        module: 'mod',
        params: { col: 'literal' },
        position: { x: 0, y: 0 },
      }],
      links: [],
      samples: [],
    };
    const parsed = parsePipelineJSON(legacy);
    expect(parsed.pipelineVariables).toEqual({});
    expect(parsed.boundVariables).toEqual({});
  });
});
