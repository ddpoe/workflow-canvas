/**
 * A param bound to a pipeline variable stays bound after the Inspector moves
 * to another node.
 *
 * Selecting a different node tears down the first node's row actors. The
 * pipeline that Run and Export send (`exportPipeline`) must still carry the
 * first node's param as `{"$var": <name>}`, not as a literal value.
 *
 * A row saved bound that the user unbinds and types into, without locking,
 * comes back after the switch the way any unlocked row does: unbound, with the
 * typed text, and not committed.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import { tick } from 'svelte';
import InspectorPanel from '../InspectorPanel.svelte';
import { clearKeptDrafts } from '../rowActors';
import { nodes, edges, selectedNodeId, modules, samples, pipelineVariables } from '../../builder/stores';
import { exportPipeline } from '../../builder/pipeline';
import type { Node } from '@xyflow/svelte';
import type { CanvasNodeData, ModuleDef } from '../../shared/types';

const CONTRACT: ModuleDef = {
  name: 'mod_a',
  methods: [{
    name: 'm_a',
    module: 'mod_a',
    inputs: [{ name: 'data', type: 'csv' }],
    outputs: [{ name: 'output', type: 'csv' }],
    params: [{ name: 'mapping', type: 'dict', contractType: 'dict', required: false }],
  }],
};

function methodNode(id: string): Node<CanvasNodeData> {
  return {
    id,
    type: 'custom',
    position: { x: 0, y: 0 },
    data: {
      label: id, method: 'm_a', module: 'mod_a', color: '#2ecc71',
      inputs: CONTRACT.methods[0].inputs,
      outputs: CONTRACT.methods[0].outputs,
      params: CONTRACT.methods[0].params,
      paramValues: {},
      runStatus: 'idle', expanded: false, nodeType: 'method',
    } as CanvasNodeData,
  };
}

function exportedParams(nodeId: string): Record<string, unknown> | undefined {
  return exportPipeline().nodes.find(n => n.id === nodeId)?.params as Record<string, unknown> | undefined;
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => null })));
  nodes.set([methodNode('node_a'), methodNode('node_b')]);
  edges.set([]);
  samples.set([]);
  modules.set([CONTRACT]);
  pipelineVariables.set({ column_map: { type: 'dict', value: { p27: 'X' } } });
  selectedNodeId.set(null);
});

afterEach(() => {
  cleanup();
  clearKeptDrafts();
  vi.unstubAllGlobals();
  pipelineVariables.set({});
  selectedNodeId.set(null);
});

describe('a bound param after the Inspector switches nodes', () => {
  it('still exports as {"$var": name} once another node, then no node, is selected, and shows bound again', async () => {
    selectedNodeId.set('node_a');
    const { container } = render(InspectorPanel);
    await tick();

    // Bind node_a's `mapping` to `column_map` through the row's picker.
    const q = within(container);
    await fireEvent.click(q.getByTestId('open-bind-picker'));
    await tick();
    await fireEvent.click(q.getByTestId('bind-picker-item'));
    await tick();
    await waitFor(() => expect(q.getByTestId('bound-row')).toHaveTextContent('→ column_map'));

    // While node_a is shown, the binding is in the exported pipeline.
    expect(exportedParams('node_a')?.mapping).toEqual({ $var: 'column_map' });

    // Show node_b: node_a's rows are torn down.
    selectedNodeId.set('node_b');
    await tick();
    await tick();
    expect(exportedParams('node_a')?.mapping).toEqual({ $var: 'column_map' });

    // Close the Inspector entirely.
    selectedNodeId.set(null);
    await tick();
    await tick();
    expect(exportedParams('node_a')?.mapping).toEqual({ $var: 'column_map' });

    // Show node_a again: its row is still the bound row, not a plain editor.
    selectedNodeId.set('node_a');
    await tick();
    await waitFor(() => expect(q.getByTestId('bound-row')).toHaveTextContent('→ column_map'));
  });

  it('exports the literal again, with no reference, once the row is unbound', async () => {
    nodes.update(ns => ns.map(n => n.id === 'node_a'
      ? { ...n, data: { ...n.data, paramValues: { mapping: { p27: 'Y' } } } }
      : n));
    selectedNodeId.set('node_a');
    const { container } = render(InspectorPanel);
    await tick();

    const q = within(container);
    await fireEvent.click(q.getByTestId('open-bind-picker'));
    await tick();
    await fireEvent.click(q.getByTestId('bind-picker-item'));
    await tick();
    await waitFor(() => expect(exportedParams('node_a')?.mapping).toEqual({ $var: 'column_map' }));

    await fireEvent.click(q.getByTestId('unbind'));
    await tick();
    await waitFor(() => expect(exportedParams('node_a')?.mapping).toEqual({ p27: 'Y' }));
    expect(q.queryByTestId('bound-row')).toBeNull();
  });

  it('exports the literal again as soon as the user starts editing the bound row', async () => {
    nodes.update(ns => ns.map(n => n.id === 'node_a'
      ? { ...n, data: { ...n.data, paramValues: { mapping: { p27: 'Y' } } } }
      : n));
    selectedNodeId.set('node_a');
    const { container } = render(InspectorPanel);
    await tick();

    const q = within(container);
    await fireEvent.click(q.getByTestId('open-bind-picker'));
    await tick();
    await fireEvent.click(q.getByTestId('bind-picker-item'));
    await tick();
    await waitFor(() => expect(exportedParams('node_a')?.mapping).toEqual({ $var: 'column_map' }));

    await fireEvent.click(q.getByTestId('unbind-edit'));
    await tick();
    await waitFor(() => expect(exportedParams('node_a')?.mapping).toEqual({ p27: 'Y' }));
    expect(q.queryByTestId('bound-row')).toBeNull();
  });
});

describe('a row saved bound, unbound and typed into, after the Inspector switches nodes', () => {
  const LABELLED: ModuleDef = {
    ...CONTRACT,
    methods: [{ ...CONTRACT.methods[0], params: [{ name: 'label', type: 'string', contractType: 'str', required: false }] }],
  };

  function labelledNode(id: string, label?: unknown): Node<CanvasNodeData> {
    const n = methodNode(id);
    return { ...n, data: { ...n.data, params: LABELLED.methods[0].params, paramValues: label === undefined ? {} : { label } } };
  }

  function labelBox(container: HTMLElement): HTMLInputElement | null {
    return container.querySelector('.row input.text');
  }

  it('comes back unlocked with the typed text, unbound, and the text is not exported until locked', async () => {
    modules.set([LABELLED]);
    pipelineVariables.set({ sample_label: { type: 'str', value: 'p27' } });
    nodes.set([labelledNode('node_a', { $var: 'sample_label' }), labelledNode('node_b')]);
    selectedNodeId.set('node_a');
    const { container } = render(InspectorPanel);
    await tick();

    // The saved reference opens the row bound.
    const q = within(container);
    await waitFor(() => expect(q.getByTestId('bound-row')).toHaveTextContent('→ sample_label'));

    // Unbind to edit the literal, and type without locking.
    await fireEvent.click(q.getByTestId('unbind-edit'));
    await tick();
    await waitFor(() => expect(labelBox(container)).not.toBeNull());
    await fireEvent.input(labelBox(container)!, { target: { value: 'typed_label' } });
    await tick();

    // Show node_b, then node_a again.
    selectedNodeId.set('node_b');
    await tick();
    await tick();
    selectedNodeId.set('node_a');
    await tick();

    await waitFor(() => expect(labelBox(container)?.value).toBe('typed_label'));
    expect(labelBox(container)!.readOnly).toBe(false);
    expect(container.querySelector('.row')!.classList.contains('editing')).toBe(true);
    expect(q.queryByTestId('bound-row')).toBeNull();
    // Neither the binding nor the unlocked text is what Run would send.
    expect(exportedParams('node_a')?.label).not.toEqual({ $var: 'sample_label' });
    expect(exportedParams('node_a')?.label).not.toBe('typed_label');
  });
});
