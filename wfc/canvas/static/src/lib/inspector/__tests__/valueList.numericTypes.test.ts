/**
 * Param classification through ValueList (paramTypeForActor).
 *
 * The palette transform (`builder/api.ts::moduleDefsFromRegistry`) keeps
 * the method.yaml type as declared in `type` and narrows it to the
 * vocabulary in `contractType`. ValueList classifies the editor from
 * `contractType` alone — a float param must accept fractional input, while
 * an int param must reject it; an `any` param edits as text. The coerce
 * rules themselves are covered in machines/__tests__/paramValueCheck.test.ts;
 * these tests cover the classification glue that picks which rule runs.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { tick } from 'svelte';
import ValueList from '../ValueList.svelte';
import { pipelineVariables } from '../../builder/stores';
import { moduleDefsFromRegistry } from '../../builder/api';
import { classifyParamType } from '../paramTypes';
import type { ParamDef } from '../../shared/types';

beforeEach(() => {
  pipelineVariables.set({});
});

afterEach(() => {
  cleanup();
  pipelineVariables.set({});
});

// A param as the palette transform delivers it from an /api/modules payload.
function paletteParam(name: string, type: string): ParamDef {
  const [mod] = moduleDefsFromRegistry({
    mod: { methods: { m: { params_schema: { [name]: { type, required: false } } } } },
  });
  return mod.methods[0].params[0];
}

function floatParam(name: string): ParamDef {
  return paletteParam(name, 'float');
}

function intParam(name: string): ParamDef {
  return paletteParam(name, 'int');
}

describe('the palette transform keeps the declared type', () => {
  it('an `any` param is `any` in the palette and edits as text; legacy spellings are not types', () => {
    const anyParam = paletteParam('extra', 'any');
    expect(anyParam.type).toBe('any');
    expect(anyParam.contractType).toBe('any');
    expect(classifyParamType(anyParam)).toBe('string');
    expect(intParam('n').type).toBe('int');
    // A spelling outside the vocabulary is unknown, never a number or a bool.
    expect(classifyParamType(paletteParam('x', 'number'))).toBe('string');
    expect(classifyParamType(paletteParam('y', 'boolean'))).toBe('string');
  });
});

async function editAndCommit(container: HTMLElement, raw: string): Promise<void> {
  await tick();
  // Rows may spawn straight into editing (autofocused input + ✓ present);
  // only click ✎ when the row spawned locked.
  const editBtn = container.querySelector('.act.edit') as HTMLButtonElement | null;
  if (editBtn) {
    await fireEvent.click(editBtn);
    await tick();
  }
  const input = container.querySelector('input.text') as HTMLInputElement;
  await fireEvent.input(input, { target: { value: raw } });
  const commitBtn = container.querySelector('.act.commit') as HTMLButtonElement;
  await fireEvent.click(commitBtn);
}

describe('ValueList numeric param classification', () => {
  it('float param accepts a fractional value', async () => {
    let committed: unknown = 'never-called';
    const { container } = render(ValueList, {
      props: {
        nodeId: 'node_f',
        param: floatParam('overlap'),
        baseValue: 0.1,
        onBaseChange: (v: unknown) => { committed = v; },
        singleValue: true,
      },
    });

    await editAndCommit(container, '0.5');

    await waitFor(() => expect(committed).toBe(0.5));
    expect(container.querySelector('.row.invalid')).toBeNull();
  });

  it('int param rejects a fractional value', async () => {
    let committed: unknown = 'never-called';
    const { container } = render(ValueList, {
      props: {
        nodeId: 'node_i',
        param: intParam('tile_size'),
        baseValue: 256,
        onBaseChange: (v: unknown) => { committed = v; },
        singleValue: true,
      },
    });

    await editAndCommit(container, '0.5');

    await waitFor(() => expect(container.querySelector('.row.invalid')).not.toBeNull());
    expect(committed).toBe('never-called');
  });

  it('a float pipeline variable is bindable (not greyed) on a float param', async () => {
    pipelineVariables.set({
      overlap_frac: { type: 'float', value: 0.25 },
    });

    const { getByTestId } = render(ValueList, {
      props: {
        nodeId: 'node_bind',
        param: floatParam('overlap'),
        baseValue: 0.1,
        onBaseChange: () => {},
        singleValue: true,
      },
    });

    await tick();
    await fireEvent.click(getByTestId('open-bind-picker'));
    await tick();

    const item = getByTestId('bind-picker-item') as HTMLButtonElement;
    expect(item.dataset.variableName).toBe('overlap_frac');
    expect(item).not.toBeDisabled();
    expect(item.className).not.toMatch(/incompatible/);
  });
});
