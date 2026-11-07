/**
 * The Built-from `param ↗` link, from a row the user has typed into but not
 * locked: the link selects the upstream node, asks the canvas to centre it
 * and the Inspector to land on the parameter, and the typed value survives
 * the switch, still unlocked and never committed, until the canvas is
 * replaced (a load, or an undo or redo across one). A Run Reference's link
 * opens the run in History on its Params tab.
 *
 * The Inspector reuses one `ValueList` per param across selections, so the
 * switch is a `nodeId` (and param) prop change on the mounted component.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { tick } from 'svelte';
import ValueList from '../ValueList.svelte';
import { clearKeptDrafts } from '../rowActors';
import { deleteNodes, modules, nodes, pipelineVariables, selectedNodeId } from '../../builder/stores';
import { loadPipeline } from '../../builder/pipeline';
import { canUndo, pushState, redo, undo } from '../../builder/undo';
import { moduleDefsFromRegistry } from '../../builder/api';
import { centerOnNodeRequest, inspectParamRequest, tabRequest } from '../../shared/uiState';
import { selectedRunId, takePendingSection } from '../../history/historyStore';
import type { ParamOptions } from '../options';
import { rememberModuleColors } from '../../shared/moduleColor.svelte';
import type { ParamDef } from '../../shared/types';

function paletteParam(name: string, schema: Record<string, unknown>): ParamDef {
  const [mod] = moduleDefsFromRegistry({ mod: { methods: { m: { params_schema: { [name]: schema } } } } });
  return mod.methods[0].params[0];
}

const CHANNEL_COL = paletteParam('channel_col', { type: 'str', options_from: 'data' });
const CHANNELS = paletteParam('channels', { type: 'list', default: ['dapi', 'gfp'] });

const BUILT = { fixed: ['cell_id'], built: [{ params: ['channels'], names: ['dapi_mean', 'gfp_mean'] }], patterns: [], all: ['cell_id', 'dapi_mean', 'gfp_mean'] };

function optionsFrom(kind: 'method' | 'run_reference'): ParamOptions {
  return {
    inputs: ['data'],
    upstreams: [{
      input: 'data', nodeId: 'node_2', slot: 'measurements', nodeLabel: 'measure_cells',
      kind, module: kind === 'method' ? 'measure' : '', method: 'measure_cells',
      runId: kind === 'run_reference' ? '412' : undefined, options: BUILT,
    }],
  };
}

function downstream(onBaseChange: (v: unknown) => void, kind: 'method' | 'run_reference' = 'method') {
  return { nodeId: 'node_3', param: CHANNEL_COL, baseValue: 'dapi_mean', options: optionsFrom(kind), onBaseChange, singleValue: true };
}

async function typeAndFollow(container: HTMLElement, text: string): Promise<void> {
  const box = container.querySelector('[data-testid="options-input"]') as HTMLInputElement;
  // Typing opens the list, filtered to the names the text matches.
  await fireEvent.input(box, { target: { value: text } });
  await fireEvent.click(container.querySelector('[data-testid="options-param-pill"]')!);
  await tick();
}

/** A pipeline that reuses the canvas's node ids. */
const LOADED = {
  name: 'p', samples: [], links: [],
  nodes: ['node_2', 'node_3'].map(id => ({ id, type: 'method', method: 'm', module: 'measure', params: {} })),
} as never;

/** The Inspector's props once it has moved to the upstream node. */
const UPSTREAM_ROWS = { nodeId: 'node_2', param: CHANNELS, baseValue: ['dapi', 'gfp'], options: undefined, onBaseChange: () => {}, singleValue: true };

function inputValue(container: HTMLElement): string {
  return (container.querySelector('[data-testid="options-input"]') as HTMLInputElement).value;
}

/**
 * The undo stack is module-level with no reset export: empty it. Each undo
 * restores an earlier canvas, so this runs before `beforeEach` builds the
 * test's own canvas, never after.
 */
function drainUndo(): void {
  for (let guard = 0; canUndo() && guard < 100; guard += 1) undo();
}

beforeEach(() => {
  drainUndo();
  pipelineVariables.set({});
  modules.set([{ name: 'measure', methods: [] }]);
  rememberModuleColors([{ name: 'measure', color: '#06809C' }]);
  nodes.set([
    { id: 'node_2', position: { x: 0, y: 0 }, data: {} as never },
    { id: 'node_3', position: { x: 0, y: 0 }, data: {} as never, selected: true },
  ]);
  selectedNodeId.set('node_3');
});

afterEach(() => {
  cleanup();
  clearKeptDrafts();
  for (const s of [centerOnNodeRequest, inspectParamRequest, tabRequest, selectedRunId, selectedNodeId]) s.set(null);
  nodes.set([]);
  modules.set([]);
});

describe('the Built-from link from an unlocked row', () => {
  it('selects and centres the upstream, lands on its parameter, and the typed value comes back unlocked', async () => {
    const onBaseChange = vi.fn();
    const { container, rerender } = render(ValueList, { props: downstream(onBaseChange) });
    await tick();
    await typeAndFollow(container, 'gfp_m');

    expect(get(selectedNodeId)).toBe('node_2');
    expect(get(nodes).filter(n => n.selected).map(n => n.id)).toEqual(['node_2']);
    expect(get(centerOnNodeRequest)).toBe('node_2');
    expect(get(inspectParamRequest)).toEqual({ nodeId: 'node_2', param: 'channels' });

    // The Inspector moves to the upstream node, then back.
    await rerender({ nodeId: 'node_2', param: CHANNELS, baseValue: ['dapi', 'gfp'], options: undefined, onBaseChange: () => {}, singleValue: true });
    await tick();
    await rerender(downstream(onBaseChange));
    await tick();

    const box = container.querySelector('[data-testid="options-input"]') as HTMLInputElement;
    expect(box.value).toBe('gfp_m');
    expect(box.readOnly).toBe(false);
    expect(container.querySelector('.row')!.classList.contains('editing')).toBe(true);
    expect(onBaseChange).not.toHaveBeenCalled();
  });

  it('drops a kept draft when the value it was typed over has changed meanwhile', async () => {
    const { container, rerender } = render(ValueList, { props: downstream(() => {}) });
    await tick();
    await typeAndFollow(container, 'gfp_m');
    await rerender({ nodeId: 'node_2', param: CHANNELS, baseValue: ['dapi'], options: undefined, onBaseChange: () => {}, singleValue: true });
    await tick();
    await rerender({ ...downstream(() => {}), baseValue: 'cell_id' });
    await tick();

    expect((container.querySelector('[data-testid="options-input"]') as HTMLInputElement).value).toBe('cell_id');
  });

  it('a kept draft does not come back after a pipeline load that reuses the node id', async () => {
    const { container, rerender } = render(ValueList, { props: downstream(() => {}) });
    await tick();
    await typeAndFollow(container, 'gfp_m');
    const method = (id: string) => ({ id, type: 'method', method: 'm', module: 'measure', params: {} });
    await loadPipeline({ name: 'p', samples: [], nodes: [method('node_2'), method('node_3')], links: [] } as never);
    // The Inspector's rows for the old canvas are torn down after the load.
    await rerender({ nodeId: 'node_2', param: CHANNELS, baseValue: ['dapi', 'gfp'], options: undefined, onBaseChange: () => {}, singleValue: true });
    await tick();
    await rerender(downstream(() => {}));
    await tick();

    expect((container.querySelector('[data-testid="options-input"]') as HTMLInputElement).value).toBe('dapi_mean');
  });

  it('a kept draft does not come back on a new node that takes a deleted node\'s id', async () => {
    const { container, rerender } = render(ValueList, { props: downstream(() => {}) });
    await tick();
    await typeAndFollow(container, 'gfp_m');
    await rerender({ nodeId: 'node_2', param: CHANNELS, baseValue: ['dapi', 'gfp'], options: undefined, onBaseChange: () => {}, singleValue: true });
    await tick();
    deleteNodes(['node_3']);
    nodes.update(ns => [...ns, { id: 'node_3', position: { x: 0, y: 0 }, data: {} as never }]);
    await rerender(downstream(() => {}));
    await tick();

    expect((container.querySelector('[data-testid="options-input"]') as HTMLInputElement).value).toBe('dapi_mean');
  });

  it('a kept draft does not come back when the node is deleted while its rows are shown', async () => {
    const { container, rerender } = render(ValueList, { props: downstream(() => {}) });
    await tick();
    await fireEvent.input(container.querySelector('[data-testid="options-input"]')!, { target: { value: 'gfp_m' } });
    deleteNodes(['node_3']);
    await rerender({ nodeId: 'node_2', param: CHANNELS, baseValue: ['dapi', 'gfp'], options: undefined, onBaseChange: () => {}, singleValue: true });
    await tick();
    nodes.update(ns => [...ns, { id: 'node_3', position: { x: 0, y: 0 }, data: {} as never }]);
    await rerender(downstream(() => {}));
    await tick();

    expect((container.querySelector('[data-testid="options-input"]') as HTMLInputElement).value).toBe('dapi_mean');
  });

  it('a kept draft does not come back after an undo across a pipeline load', async () => {
    // The Toolbar's load: record the canvas before, then replace it.
    pushState();
    await loadPipeline(LOADED);
    const { container, rerender } = render(ValueList, { props: downstream(() => {}) });
    await tick();
    await typeAndFollow(container, 'gfp_m');
    await rerender(UPSTREAM_ROWS);
    await tick();
    // Ctrl+Z brings back the canvas before the load, with the same node ids.
    undo();
    await rerender(downstream(() => {}));
    await tick();

    expect(inputValue(container)).toBe('dapi_mean');
  });

  it('a kept draft does not come back after a redo across a pipeline load', async () => {
    pushState();
    await loadPipeline(LOADED);
    undo();
    // A draft typed on the canvas before the load, then the load redone.
    const { container, rerender } = render(ValueList, { props: downstream(() => {}) });
    await tick();
    await typeAndFollow(container, 'gfp_m');
    await rerender(UPSTREAM_ROWS);
    await tick();
    redo();
    await rerender(downstream(() => {}));
    await tick();

    expect(inputValue(container)).toBe('dapi_mean');
  });

  it('a kept draft survives an undo of an unrelated edit on the same canvas', async () => {
    const { container, rerender } = render(ValueList, { props: downstream(() => {}) });
    await tick();
    await typeAndFollow(container, 'gfp_m');
    await rerender(UPSTREAM_ROWS);
    await tick();
    // Move node_2, then take the move back.
    pushState();
    nodes.update(ns => ns.map(n => (n.id === 'node_2' ? { ...n, position: { x: 40, y: 40 } } : n)));
    undo();
    await rerender(downstream(() => {}));
    await tick();

    expect(inputValue(container)).toBe('gfp_m');
    expect(container.querySelector('.row')!.classList.contains('editing')).toBe(true);
  });

  it('a kept draft survives an undo within the canvas an undo across a load brought back', async () => {
    // An edit on the first canvas, then a load, then the load undone.
    pushState();
    nodes.update(ns => ns.map(n => (n.id === 'node_2' ? { ...n, position: { x: 40, y: 40 } } : n)));
    pushState();
    await loadPipeline(LOADED);
    undo();
    const { container, rerender } = render(ValueList, { props: downstream(() => {}) });
    await tick();
    await typeAndFollow(container, 'gfp_m');
    await rerender(UPSTREAM_ROWS);
    await tick();
    // Taking back the edit stays on the same canvas.
    undo();
    await rerender(downstream(() => {}));
    await tick();

    expect(inputValue(container)).toBe('gfp_m');
  });

  it('opens a Run Reference upstream\'s run in History on its Params tab', async () => {
    const { container } = render(ValueList, { props: downstream(() => {}, 'run_reference') });
    await tick();
    await typeAndFollow(container, 'gfp_m');

    expect(get(tabRequest)).toBe('history');
    // The run opens on the next task, once History has begun its reload.
    await new Promise(r => setTimeout(r, 0));
    expect(get(selectedRunId)).toBe('412');
    expect(takePendingSection('412')).toBe('params');
    expect(get(selectedNodeId)).toBe('node_3');
  });
});
