/**
 * Component-mounting Vitest UI tests for the Pipeline Variables UX
 * (Track 2, ADR-015). Incarnation 4 spec compliance — these were called
 * out as required by the architect's test plan but deferred in
 * incarnation 3.
 *
 * Each test mounts a real Svelte component via @testing-library/svelte
 * and asserts user-visible / store-visible behavior. The actor-machine
 * contract is already covered in `machines/__tests__/paramEditor.test.ts`;
 * these tests cover the rendered DOM glue layer (panel inputs ↔ store,
 * picker rendering, chip propagation).
 *
 * Tests:
 *   1. PipelineVariablesPanel render + add — clicking + Add variable,
 *      filling name/type/value, confirm → pipelineVariables store updates.
 *   2. ValueList bind/edit propagation — two bound rows for the same
 *      dict-typed param show resolved value chips that update when the
 *      underlying variable's value changes in the store.
 *   3. ValueList type-mismatch greyed — picker shows incompatible
 *      variables visible-but-disabled (matches `.incompatible` class +
 *      `disabled` attribute set by ValueList.svelte).
 *   4. ValueList bind picker empty-state (D-6) — empty pipelineVariables
 *      → picker shows hint text, no variable items, no "Create new"
 *      affordance.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import { get } from 'svelte/store';
import { tick } from 'svelte';
import PipelineVariablesPanel from '../PipelineVariablesPanel.svelte';
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

describe('PipelineVariablesPanel render + add', () => {
  it('clicking + Add variable, filling name/type/value, ✓ confirm writes to pipelineVariables store', async () => {
    const { getByTestId } = render(PipelineVariablesPanel);

    // Click the + Add variable button — inline-add row appears.
    await fireEvent.click(getByTestId('pv-add-variable'));
    await tick();
    expect(getByTestId('pv-add-row')).toBeInTheDocument();

    // Fill name=column_map, type=dict, value={"p27":"X"}.
    const nameInput = getByTestId('pv-new-name') as HTMLInputElement;
    await fireEvent.input(nameInput, { target: { value: 'column_map' } });

    const typeSelect = getByTestId('pv-new-type') as HTMLSelectElement;
    await fireEvent.change(typeSelect, { target: { value: 'dict' } });
    await tick();

    // After type=dict, the value widget is a textarea (per the
    // {#if newType === 'list' || newType === 'dict'} branch).
    const valueArea = getByTestId('pv-new-value') as HTMLTextAreaElement;
    await fireEvent.input(valueArea, { target: { value: '{"p27":"X"}' } });

    // Confirm — store should receive the new variable.
    await fireEvent.click(getByTestId('pv-confirm-add'));
    await tick();

    const $vars = get(pipelineVariables);
    expect($vars).toHaveProperty('column_map');
    expect($vars.column_map.type).toBe('dict');
    expect($vars.column_map.value).toEqual({ p27: 'X' });
  });
});

// ── ValueList helpers ────────────────────────────────────────────────────

function dictParam(name: string): ParamDef {
  return { name, type: 'dict', contractType: 'dict', required: false };
}

function strParam(name: string): ParamDef {
  return { name, type: 'string', contractType: 'str', required: false };
}

describe('ValueList bind/edit propagation', () => {
  it('two bound rows for the same dict-typed param show updated resolved values when the variable changes in the store', async () => {
    pipelineVariables.set({
      column_map: { type: 'dict', value: { p27: 'X' } },
    });

    const param = dictParam('mapping');
    const r1 = render(ValueList, {
      props: {
        nodeId: 'node_1',
        param,
        baseValue: {},
        onBaseChange: () => {},
        singleValue: true,
      },
    });
    const r2 = render(ValueList, {
      props: {
        nodeId: 'node_2',
        param,
        baseValue: {},
        onBaseChange: () => {},
        singleValue: true,
      },
    });

    // @testing-library/svelte binds query helpers to `document.body` (not
    // each render's container), so r1.getByTestId / r2.getByTestId both
    // find buttons across both renders. Scope each lookup with
    // `within(container)` so a query lands in the right component.
    const q1 = within(r1.container);
    const q2 = within(r2.container);

    // Wait for spawn $effect to register actors.
    await tick();

    // Open picker on each row, then BIND_VARIABLE → 'column_map'.
    const open1 = q1.getByTestId('open-bind-picker');
    await fireEvent.click(open1);
    await tick();
    const item1 = q1.getByTestId('bind-picker-item');
    await fireEvent.click(item1);
    await tick();

    const open2 = q2.getByTestId('open-bind-picker');
    await fireEvent.click(open2);
    await tick();
    const item2 = q2.getByTestId('bind-picker-item');
    await fireEvent.click(item2);
    await tick();

    // Both rows render the chip (bound widget).
    const chip1 = q1.getByTestId('bound-row');
    const chip2 = q2.getByTestId('bound-row');
    expect(chip1).toHaveTextContent('→ column_map');
    expect(chip2).toHaveTextContent('→ column_map');
    // Both display the original resolved value (stringified).
    expect(chip1).toHaveTextContent('"p27":"X"');
    expect(chip2).toHaveTextContent('"p27":"X"');

    // Update the variable in the store — both chips' resolved-value
    // spans should reflect the new value (chip text re-derives via
    // `displayValue(get(pipelineVariables)[name].value)` per ValueList
    // template line ~683).
    pipelineVariables.update($v => ({
      ...$v,
      column_map: { type: 'dict', value: { p27: 'Y' } },
    }));
    await tick();
    await tick();

    expect(chip1).toHaveTextContent('"p27":"Y"');
    expect(chip2).toHaveTextContent('"p27":"Y"');
  });
});

describe('ValueList type-mismatch greying in bind picker', () => {
  it('a dict variable is rendered in the picker for a str-typed param, but disabled and styled incompatible', async () => {
    pipelineVariables.set({
      mapping_var: { type: 'dict', value: { p27: 'X' } },
    });

    const param = strParam('label');
    const { getByTestId } = render(ValueList, {
      props: {
        nodeId: 'node_str',
        param,
        baseValue: 'hello',
        onBaseChange: () => {},
        singleValue: true,
      },
    });

    await tick();

    // Open the picker.
    await fireEvent.click(getByTestId('open-bind-picker'));
    await tick();

    // Picker is open; the dict variable is rendered but disabled +
    // carries the incompatible class (str ≠ dict).
    const item = getByTestId('bind-picker-item') as HTMLButtonElement;
    expect(item).toBeInTheDocument();
    expect(item.dataset.variableName).toBe('mapping_var');
    expect(item).toBeDisabled();
    expect(item.className).toMatch(/incompatible/);
  });
});

describe('ValueList bind picker empty-state (D-6: existing-only)', () => {
  it('with no pipeline variables, picker shows hint text, no items, no Create-new affordance', async () => {
    pipelineVariables.set({});

    const param = strParam('label');
    const { getByTestId, queryAllByTestId, queryByText } = render(ValueList, {
      props: {
        nodeId: 'node_empty',
        param,
        baseValue: 'hello',
        onBaseChange: () => {},
        singleValue: true,
      },
    });

    await tick();
    await fireEvent.click(getByTestId('open-bind-picker'));
    await tick();

    // Empty-state hint visible.
    const empty = getByTestId('bind-picker-empty');
    expect(empty).toBeInTheDocument();
    expect(empty.textContent ?? '').toMatch(/No pipeline variables yet/i);

    // No clickable variable list items at all.
    expect(queryAllByTestId('bind-picker-item')).toHaveLength(0);

    // No "Create new" button or affordance anywhere in the picker DOM.
    // D-6 mandates the panel is the SOLE creation surface — the picker
    // must NOT offer inline creation.
    expect(queryByText(/create new/i)).toBeNull();
    expect(queryByText(/\+ new/i)).toBeNull();
    expect(queryByText(/add variable/i)).toBeNull();
  });
});
