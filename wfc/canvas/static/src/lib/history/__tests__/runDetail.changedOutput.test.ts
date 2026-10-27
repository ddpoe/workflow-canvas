/**
 * History shows an output wfc found changed after its run wrote it as
 * unavailable.
 *
 * The run payload carries each output's availability. The run detail
 * panel's Artifacts tab lists a `changed` output as unavailable, with the
 * run-again text and no download link, beside the run's other artifacts.
 * Component test over the real panel; the only boundary stub is `fetch`.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import RunDetailPanel from '../RunDetailPanel.svelte';
import type { Artifact, WfcRun } from '../historyApi';

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: 'r1',
    module: 'mod',
    method: 'head',
    version: 1,
    versionIdentity: null,
    timestamp: 1,
    duration: 0,
    status: 'success',
    inputs: {},
    outputs: {},
    outputAvailability: {},
    metrics: {},
    dataSource: 'S1',
    parentRunIds: [],
    upstreamRunIds: [],
    parents: [],
    codeFingerprint: null,
    envName: null,
    envFingerprint: null,
    gitCommit: null,
    wfcVersion: null,
    pipelineDocHash: null,
    keyedUnderEarlierRule: false,
    experimentId: 'p',
    runName: 'head/S1',
    nid: 'v1',
    user: '',
    favorite: false,
    pipelineId: null,
    scriptPath: null,
    ...partial,
  };
}

function serve(run: WfcRun, artifacts: Artifact[]): void {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    const body = url === `/api/wfc/run/${run.id}`
      ? run
      : url.startsWith(`/api/wfc/run/${run.id}/artifacts`) ? artifacts : [];
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  }) as unknown as typeof fetch;
}

async function openArtifacts(runId: string) {
  const view = render(RunDetailPanel, { props: { runId } });
  await fireEvent.click(await view.findByRole('button', { name: /Artifacts/ }));
  return view;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('run detail, Artifacts tab', () => {
  it('shows a changed output unavailable with the run-again text', async () => {
    const report: Artifact = { name: 'report.csv', size: 12, is_image: false, extension: 'csv' };
    serve(
      mkRun({
        id: 'r7',
        outputs: { data: '/p/.wfc/runs/00000007/data.csv', report: '/p/.wfc/runs/00000007/report.csv' },
        outputAvailability: { data: 'changed', report: 'available' },
      }),
      [report],
    );

    const view = await openArtifacts('r7');

    const rows = await view.findAllByTestId('unavailable-output');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('data');
    expect(rows[0]).toHaveTextContent('changed after the run wrote it; run the step again');
    expect(rows[0].querySelector('a')).toBeNull();
    expect(rows[0].closest('a')).toBeNull();
    // The available output is still listed as a download.
    const link = view.getByText('report.csv').closest('a');
    expect(link?.getAttribute('href')).toContain('report.csv');
  });

  it('shows the changed output when it is the run\'s only output', async () => {
    serve(
      mkRun({
        id: 'r8',
        outputs: { data: '/p/.wfc/runs/00000008/data.csv' },
        outputAvailability: { data: 'changed' },
      }),
      [],
    );

    const view = await openArtifacts('r8');

    expect(await view.findByTestId('unavailable-output')).toHaveTextContent('data');
    expect(view.queryByText('No artifacts found.')).toBeNull();
  });

  it('lists nothing unavailable when every output is available', async () => {
    serve(
      mkRun({
        id: 'r9',
        outputs: { data: '/p/.wfc/runs/00000009/data.csv' },
        outputAvailability: { data: 'available' },
      }),
      [{ name: 'data.csv', size: 3, is_image: false, extension: 'csv' }],
    );

    const view = await openArtifacts('r9');

    await view.findByText('data.csv');
    expect(view.queryByTestId('unavailable-outputs')).toBeNull();
  });
});
