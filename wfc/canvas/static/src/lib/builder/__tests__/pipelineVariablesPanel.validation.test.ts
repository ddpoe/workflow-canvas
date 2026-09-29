/**
 * Pipeline Variables panel — typed-value validation on add.
 *
 * A variable's value must conform to its declared type before it is
 * created: invalid input surfaces an inline error, keeps the add row
 * open, and leaves the store untouched. The rules match the param
 * editor's (coerceParamValue): int accepts integers only, float accepts
 * any finite number.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { tick } from 'svelte';
import PipelineVariablesPanel from '../PipelineVariablesPanel.svelte';
import { pipelineVariables } from '../stores';

beforeEach(() => {
  pipelineVariables.set({});
});

afterEach(() => {
  cleanup();
  pipelineVariables.set({});
});

async function openAddRow(getByTestId: (id: string) => HTMLElement) {
  await fireEvent.click(getByTestId('pv-add-variable'));
  await tick();
}

describe('PipelineVariablesPanel value validation', () => {
  it('rejects a fractional value for an int variable with an inline error, then accepts a corrected value', async () => {
    const { getByTestId, queryByTestId } = render(PipelineVariablesPanel);
    await openAddRow(getByTestId);

    await fireEvent.input(getByTestId('pv-new-name'), { target: { value: 'n_tiles' } });
    await fireEvent.change(getByTestId('pv-new-type'), { target: { value: 'int' } });
    await tick();
    await fireEvent.input(getByTestId('pv-new-value'), { target: { value: '0.5' } });
    await fireEvent.click(getByTestId('pv-confirm-add'));
    await tick();

    expect(get(pipelineVariables)).toEqual({});
    // Add row stays open with a visible error.
    expect(getByTestId('pv-add-row')).toBeInTheDocument();
    const err = getByTestId('pv-add-error');
    expect(err.textContent ?? '').toMatch(/int/i);

    // Correcting the value clears the block and creates the variable.
    await fireEvent.input(getByTestId('pv-new-value'), { target: { value: '5' } });
    await fireEvent.click(getByTestId('pv-confirm-add'));
    await tick();

    expect(get(pipelineVariables).n_tiles).toEqual({ type: 'int', value: 5 });
    expect(queryByTestId('pv-add-row')).toBeNull();
  });

  it('accepts 0.5 for a float variable', async () => {
    const { getByTestId } = render(PipelineVariablesPanel);
    await openAddRow(getByTestId);

    await fireEvent.input(getByTestId('pv-new-name'), { target: { value: 'overlap' } });
    await fireEvent.change(getByTestId('pv-new-type'), { target: { value: 'float' } });
    await tick();
    await fireEvent.input(getByTestId('pv-new-value'), { target: { value: '0.5' } });
    await fireEvent.click(getByTestId('pv-confirm-add'));
    await tick();

    expect(get(pipelineVariables).overlap).toEqual({ type: 'float', value: 0.5 });
  });
});
