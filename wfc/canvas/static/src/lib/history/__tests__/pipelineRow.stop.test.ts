/**
 * The History pipeline row's Stop: shown only while the pipeline reads
 * running; it calls the cancel route with the pipeline id, then refreshes
 * History.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, waitFor } from '@testing-library/svelte';

const cancelJob = vi.fn(async (_id: string) => ({ ok: true, status: 200 }) as Response);
const loadRuns = vi.fn(async () => {});

vi.mock('../../machines/services.js', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  cancelJob: (id: string) => cancelJob(id),
}));
vi.mock('../historyStore.js', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  loadRuns: () => loadRuns(),
}));

import PipelineRow from '../PipelineRow.svelte';
import type { PipelineRowSummary } from '../historyStore';

afterEach(() => {
  cleanup();
  cancelJob.mockClear();
  loadRuns.mockClear();
});

function row(status: string): PipelineRowSummary {
  return {
    pipelineId: 'pipe-01',
    name: 'my pipeline',
    status,
    buckets: { ran: 1, cached: 0, failed: 0, cancelled: 0, running: 1, pending: 2 },
    sampleCount: 1,
    started: Date.now(),
    runs: [],
  } as PipelineRowSummary;
}

describe('a History pipeline row\'s Stop', () => {
  it('is shown while the pipeline reads running, and calls the cancel route then refreshes', async () => {
    const { getByTestId } = render(PipelineRow, {
      props: { row: row('running'), currentCanvasPipelineId: null },
    });
    await fireEvent.click(getByTestId('pipeline-stop'));

    await waitFor(() => expect(loadRuns).toHaveBeenCalledTimes(1));
    expect(cancelJob).toHaveBeenCalledWith('pipe-01');
  });

  it.each(['succeeded', 'failed', 'cancelled'])('is not shown once the pipeline reads %s', (status) => {
    const { queryByTestId } = render(PipelineRow, {
      props: { row: row(status), currentCanvasPipelineId: null },
    });
    expect(queryByTestId('pipeline-stop')).toBeNull();
  });
});
