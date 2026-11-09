/**
 * A failed run's Descendants card says it failed, not why.
 *
 * The card keeps its size: its meta line reads "✗ Failed after <duration>"
 * and carries no error message. "✗ Failed" opens the run in the detail
 * panel on its Output tab, where the log is. Component test over the real
 * view and stores.
 */
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import { get } from 'svelte/store';
import DescendantsView from '../DescendantsView.svelte';
import {
  runs, resetFilters, descendantsCollapsed, selectedRunId, takePendingSection,
} from '../historyStore';
import type { WfcRun } from '../historyApi';

const MESSAGE = "KeyError: 'gene_symbol'";

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: 'r1', module: 'seg', method: 'normalize', version: 3, timestamp: 1, duration: 14.2,
    status: 'success', inputs: {}, outputs: {}, outputAvailability: {}, metrics: {},
    dataSource: 'sample_a', parentRunIds: [], upstreamRunIds: [], parents: [],
    experimentId: 'e', runName: 'pip/run', nid: 'v1', user: 'u', favorite: false,
    pipelineId: null, scriptPath: null,
    ...partial,
  } as WfcRun;
}

beforeEach(() => {
  runs.set([]);
  resetFilters();
  descendantsCollapsed.set(new Set());
  selectedRunId.set(null);
});

afterEach(() => cleanup());

describe('a failed Descendants card', () => {
  it('says ✗ Failed and shows no error message', () => {
    runs.set([mkRun({ id: 'bad', status: 'failed', error_message: `${MESSAGE}\nTraceback…` })]);
    const { container, getByRole } = render(DescendantsView);
    expect(getByRole('button', { name: 'Failed' })).toBeTruthy();
    expect(container.textContent).not.toContain('KeyError');
  });

  it('✗ Failed opens the run on its Output tab', async () => {
    runs.set([mkRun({ id: 'bad', status: 'failed', error_message: MESSAGE })]);
    const { getByRole } = render(DescendantsView);
    await fireEvent.click(getByRole('button', { name: 'Failed' }));
    expect(get(selectedRunId)).toBe('bad');
    expect(takePendingSection('bad')).toBe('output');
  });

  it('reads ✗ Failed after its duration', () => {
    runs.set([mkRun({ id: 'bad', status: 'failed', duration: 14.2 })]);
    const { container } = render(DescendantsView);
    expect(container.textContent).toMatch(/Failed\s+after 14\.2s/);
  });

  it('a run refused before it started reads ✗ Failed with no duration', () => {
    runs.set([mkRun({ id: 'bad', status: 'failed', duration: null })]);
    const { container, getByRole } = render(DescendantsView);
    expect(getByRole('button', { name: 'Failed' })).toBeTruthy();
    expect(container.textContent).not.toContain('after');
  });

  it('a succeeded card has no failure link', () => {
    runs.set([mkRun({ id: 'ok' })]);
    const { queryByRole } = render(DescendantsView);
    expect(queryByRole('button', { name: /Failed/ })).toBeNull();
  });

  it("Enter on ✗ Failed is the link's, not the card's", async () => {
    runs.set([mkRun({ id: 'bad', status: 'failed', error_message: MESSAGE })]);
    const { getByRole } = render(DescendantsView);
    await fireEvent.keyDown(getByRole('button', { name: 'Failed' }), { key: 'Enter' });
    // The card's own Enter would select the run on Overview.
    expect(get(selectedRunId)).toBeNull();
  });
});
