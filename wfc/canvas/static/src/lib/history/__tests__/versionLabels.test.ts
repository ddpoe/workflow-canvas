/**
 * History shows the method version a run used, apart from the run's NID.
 *
 * `WfcRun.version` is the version's display label (a number). The run detail
 * hero and facts name it as `v<label>`; a Descendants card shows the NID and
 * the version as separate items, so the NID text is never `v<label>`-mixed.
 * Component tests over the real components; the run detail's only boundary
 * stub is `fetch`.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import RunDetailPanel from '../RunDetailPanel.svelte';
import DescendantsView from '../DescendantsView.svelte';
import { runs, resetFilters, descendantsCollapsed } from '../historyStore';
import type { WfcRun } from '../historyApi';

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: 'r1',
    module: 'seg',
    method: 'segment',
    version: 2,
    timestamp: 1,
    duration: 0,
    status: 'success',
    inputs: {},
    outputs: {},
    outputAvailability: {},
    metrics: {},
    dataSource: 'sample_a',
    parentRunIds: [],
    upstreamRunIds: [],
    parents: [],
    experimentId: 'e',
    runName: 'pip/run',
    nid: 'seg-main',
    user: 'u',
    favorite: false,
    pipelineId: null,
    scriptPath: null,
    ...partial,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('run detail', () => {
  it("names the run's method version in the hero and the facts", async () => {
    const run = mkRun({ id: 'run-v2', version: 2 });
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      const body = url === '/api/wfc/run/run-v2' ? run : [];
      return {
        ok: true,
        status: 200,
        json: async () => body,
        text: async () => JSON.stringify(body),
      } as unknown as Response;
    }) as unknown as typeof fetch;

    const view = render(RunDetailPanel, { props: { runId: 'run-v2' } });

    expect(await view.findByTestId('run-version')).toHaveTextContent('v2');
    const label = view.getByText('Method version');
    expect(label.nextElementSibling).toHaveTextContent('v2');
  });
});

describe('a Descendants card', () => {
  beforeEach(() => {
    runs.set([]);
    resetFilters();
    descendantsCollapsed.set(new Set());
  });

  it('shows the NID and the version apart', () => {
    runs.set([mkRun({ id: 'desc-v3', nid: 'seg-main', version: 3 })]);
    const { container } = render(DescendantsView);

    expect(container.querySelector('.card-nid')?.textContent).toBe('seg-main');
    expect(container.querySelector('.card-version')?.textContent).toBe('v3');
  });
});
