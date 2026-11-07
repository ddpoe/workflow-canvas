/**
 * The options picker mounted in a ValueList row: how the list opens and
 * closes, the keyboard, the pattern fill, the locked row, and that the row
 * keeps committing free text. The section rules themselves are proven in
 * `options.sections.test.ts`; these tests cover what only the mounted row
 * does.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { tick } from 'svelte';
import ValueList from '../ValueList.svelte';
import { modules, pipelineVariables } from '../../builder/stores';
import { moduleDefsFromRegistry } from '../../builder/api';
import type { ParamOptions } from '../options';
import { rememberModuleColors } from '../../shared/moduleColor.svelte';
import type { ParamDef } from '../../shared/types';

beforeEach(() => {
  pipelineVariables.set({});
  modules.set([{ name: 'measure', methods: [] }]);
  rememberModuleColors([{ name: 'measure', color: '#06809C' }]);
});

afterEach(() => {
  cleanup();
  pipelineVariables.set({});
  modules.set([]);
});

/** A param as the palette transform delivers it from an /api/modules payload. */
function paletteParam(name: string, schema: Record<string, unknown>): ParamDef {
  const [mod] = moduleDefsFromRegistry({
    mod: { methods: { m: { params_schema: { [name]: schema } } } },
  });
  return mod.methods[0].params[0];
}

const CHANNEL_COL = paletteParam('channel_col', { type: 'str', options_from: 'data' });

const OPTIONS: ParamOptions = {
  inputs: ['data'],
  upstreams: [{
    input: 'data', nodeId: 'node_1', slot: 'measurements', nodeLabel: 'measure_cells',
    kind: 'method', module: 'measure', method: 'measure_cells',
    options: {
      fixed: ['cell_id', 'area', 'perimeter'],
      built: [{ params: ['channels'], names: ['dapi_mean', 'gfp_mean'] }],
      patterns: ['intensity_*'],
      all: ['cell_id', 'area', 'perimeter', 'dapi_mean', 'gfp_mean'],
    },
  }],
};

function mount(baseValue: string, onBaseChange: (v: unknown) => void = () => {}) {
  return render(ValueList, {
    props: {
      nodeId: 'node_2', param: CHANNEL_COL, baseValue, options: OPTIONS,
      onBaseChange, singleValue: true,
    },
  });
}

function input(c: HTMLElement): HTMLInputElement {
  return c.querySelector('[data-testid="options-input"]') as HTMLInputElement;
}
function chevron(c: HTMLElement): HTMLButtonElement {
  return c.querySelector('[data-testid="options-chevron"]') as HTMLButtonElement;
}
function list(c: HTMLElement): HTMLElement | null {
  return c.querySelector('[data-testid="options-list"]');
}
function optionTexts(c: HTMLElement): string[] {
  return [...c.querySelectorAll('[data-testid="options-item"]')].map(e => e.textContent!.trim());
}

describe('opening and closing the list', () => {
  it('the chevron, a click in the box and ArrowDown each open it; the header names node, input and count', async () => {
    const { container } = mount('dapi_mean');
    await tick();
    expect(list(container)).toBeNull();
    expect(chevron(container).title).toBe('Show the options');

    await fireEvent.mouseDown(chevron(container));
    expect(list(container)).not.toBeNull();
    const head = container.querySelector('[data-testid="options-section-head"]')!;
    expect(head.textContent).toContain('Options of');
    expect(head.querySelector('.chip')!.textContent!.trim()).toBe('measure_cells');
    expect(head.textContent).toContain('via data');
    expect(head.querySelector('.scount')!.textContent!.trim()).toBe('6');
    // One section: no fold chevron.
    expect(head.querySelector('.sc')).toBeNull();
    expect(optionTexts(container)).toEqual(['cell_id', 'area', 'perimeter', 'dapi_mean', 'gfp_mean', 'intensity_*']);
    expect(container.querySelector('.opt.cur')!.textContent!.trim()).toBe('dapi_mean');

    await fireEvent.mouseDown(chevron(container));
    expect(list(container)).toBeNull();

    await fireEvent.click(input(container));
    expect(list(container)).not.toBeNull();
    await fireEvent.keyDown(input(container), { key: 'Escape' });
    expect(list(container)).toBeNull();

    await fireEvent.keyDown(input(container), { key: 'ArrowDown' });
    expect(list(container)).not.toBeNull();
  });

  it('Esc closes the list and keeps the typed text; the row stays unlocked', async () => {
    const { container } = mount('dapi_mean');
    await tick();
    await fireEvent.input(input(container), { target: { value: 'gfp' } });
    expect(list(container)).not.toBeNull();
    expect(optionTexts(container)).toEqual(['gfp_mean']);
    await fireEvent.keyDown(input(container), { key: 'Escape' });
    expect(list(container)).toBeNull();
    expect(input(container).value).toBe('gfp');
    expect(container.querySelector('.row.editing')).not.toBeNull();
  });
});

describe('picking', () => {
  it('ArrowDown then Enter picks the highlighted option and closes the list', async () => {
    let committed: unknown = 'never';
    const { container } = mount('', v => { committed = v; });
    await tick();
    await fireEvent.input(input(container), { target: { value: 'mean' } });
    await fireEvent.keyDown(input(container), { key: 'ArrowDown' });
    await fireEvent.keyDown(input(container), { key: 'ArrowDown' });
    expect(container.querySelector('.opt.hl')!.textContent!.trim()).toBe('gfp_mean');
    await fireEvent.keyDown(input(container), { key: 'Enter' });
    expect(list(container)).toBeNull();
    expect(input(container).value).toBe('gfp_mean');
    // Picking fills the box; locking is still the user's ✓.
    expect(committed).toBe('never');
    expect(container.querySelector('.row.editing')).not.toBeNull();
  });

  it('a pattern click fills its fixed part and keeps the list open', async () => {
    const { container } = mount('');
    await tick();
    await fireEvent.mouseDown(chevron(container));
    const pat = container.querySelector('.pat') as HTMLElement;
    await fireEvent.mouseDown(pat);
    expect(input(container).value).toBe('intensity_');
    expect(list(container)).not.toBeNull();
    expect(optionTexts(container)).toEqual(['intensity_*']);
  });

  it('Enter with nothing highlighted commits free text not among the options', async () => {
    let committed: unknown = 'never';
    const { container } = mount('dapi_mean', v => { committed = v; });
    await tick();
    await fireEvent.input(input(container), { target: { value: 'dapi_avg' } });
    expect(container.querySelector('[data-testid="options-no-match"]')).not.toBeNull();
    await fireEvent.keyDown(input(container), { key: 'Enter' });
    await waitFor(() => expect(committed).toBe('dapi_avg'));
    expect(container.querySelector('.row.editing')).toBeNull();
  });
});

describe('the row around the picker', () => {
  it('a locked row keeps its chevron, dimmed and disabled, and the box is read-only', async () => {
    const { container } = mount('dapi_mean', () => {});
    await tick();
    await fireEvent.click(container.querySelector('.act.commit') as HTMLButtonElement);
    await waitFor(() => expect(container.querySelector('.row.editing')).toBeNull());
    expect(chevron(container).disabled).toBe(true);
    expect(chevron(container).classList.contains('off')).toBe(true);
    expect(chevron(container).title).toBe('Unlock the row to change it');
    expect(input(container).readOnly).toBe(true);
    await fireEvent.click(input(container));
    await fireEvent.keyDown(input(container), { key: 'ArrowDown' });
    expect(list(container)).toBeNull();
  });

  it('✎ unlock puts the caret in the options box and marks it focused, without opening the list', async () => {
    const { container } = mount('dapi_mean', () => {});
    await tick();
    const picker = container.querySelector('[data-testid="options-picker"]')!;
    await fireEvent.click(container.querySelector('.act.commit') as HTMLButtonElement);
    await waitFor(() => expect(container.querySelector('.row.editing')).toBeNull());
    (document.activeElement as HTMLElement | null)?.blur();
    expect(picker.classList.contains('focus')).toBe(false);

    await fireEvent.click(container.querySelector('.act.edit') as HTMLButtonElement);
    await waitFor(() => expect(document.activeElement).toBe(input(container)));
    expect(picker.classList.contains('focus')).toBe(true);
    expect(list(container)).toBeNull();
  });

  it('the box is marked focused while the list is open and not once it loses focus closed', async () => {
    const { container } = mount('dapi_mean');
    await tick();
    const picker = container.querySelector('[data-testid="options-picker"]')!;
    input(container).blur();
    await tick();
    expect(picker.classList.contains('focus')).toBe(false);
    await fireEvent.mouseDown(chevron(container));
    expect(list(container)).not.toBeNull();
    expect(picker.classList.contains('focus')).toBe(true);
    input(container).blur();
    await tick();
    expect(list(container)).toBeNull();
    expect(picker.classList.contains('focus')).toBe(false);
  });

  it('an options row has no ⤢ button; a plain string row keeps it', async () => {
    const { container } = mount('dapi_mean');
    await tick();
    expect(container.querySelector('.act.expand')).toBeNull();
    cleanup();
    const plain = render(ValueList, {
      props: {
        nodeId: 'node_3', param: paletteParam('title', { type: 'str' }), baseValue: 'x',
        onBaseChange: () => {}, singleValue: true,
      },
    });
    await tick();
    expect(plain.container.querySelector('.act.expand')).not.toBeNull();
    expect(plain.container.querySelector('[data-testid="options-picker"]')).toBeNull();
  });
});
