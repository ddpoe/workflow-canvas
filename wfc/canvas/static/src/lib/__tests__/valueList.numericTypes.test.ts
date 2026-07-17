/**
 * Numeric param classification through ValueList (paramTypeForActor).
 *
 * The Sidebar maps both int and float method.yaml params to the legacy
 * `type: 'number'`, keeping the precise type in `contractType`. ValueList
 * must classify the editor from `contractType` first — a float param
 * (`contractType: 'float'`) must accept fractional input, while an int
 * param (`contractType: 'int'`) must reject it. The coerce rules
 * themselves are covered in machines/__tests__/paramEditor.test.ts;
 * these tests cover the classification glue that picks which rule runs.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { tick } from 'svelte';
import ValueList from '../ValueList.svelte';
import { pipelineVariables } from '../stores';
import type { ParamDef } from '../types';

beforeEach(() => {
  pipelineVariables.set({});
});

afterEach(() => {
  cleanup();
  pipelineVariables.set({});
});

// Shape as delivered by Sidebar.svelte's /api/modules transform.
function floatParam(name: string): ParamDef {
  return { name, type: 'number', contractType: 'float', required: false };
}

function intParam(name: string): ParamDef {
  return { name, type: 'number', contractType: 'int', required: false };
}

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
  it('float param (type number, contractType float) accepts a fractional value', async () => {
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

  it('int param (type number, contractType int) still rejects a fractional value', async () => {
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
