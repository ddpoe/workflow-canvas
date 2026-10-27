/**
 * The History pipeline row's "Watch in Canvas": while the pipeline reads
 * running it replaces "Open in Canvas", on the row and in its ⋯ menu, and
 * attaches the canvas to the pipeline; once it has ended, "Open in Canvas"
 * is back.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';

const watchPipelineInCanvas = vi.fn(async (_id: string, _label: string) => true);

vi.mock('../watchPipeline.js', () => ({
  watchPipelineInCanvas: (id: string, label: string) => watchPipelineInCanvas(id, label),
}));

import PipelineRow from '../PipelineRow.svelte';
import type { PipelineRowSummary } from '../historyStore';

afterEach(() => {
  cleanup();
  watchPipelineInCanvas.mockClear();
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

describe('a History pipeline row\'s Watch in Canvas', () => {
  it('replaces Open in Canvas while the pipeline runs, and watches it', async () => {
    const { getByTestId, container, getByRole } = render(PipelineRow, {
      props: { row: row('running'), currentCanvasPipelineId: null },
    });
    const buttons = [...container.querySelectorAll('.actions button.open-btn')]
      .map(b => b.textContent?.trim());
    expect(buttons).toEqual(['Watch in Canvas ↗']);

    await fireEvent.click(getByTestId('pipeline-watch'));
    expect(watchPipelineInCanvas).toHaveBeenCalledWith('pipe-01', 'my pipeline');

    // The ⋯ menu offers the same action.
    await fireEvent.click(getByRole('button', { name: 'Pipeline actions' }));
    const items = [...document.querySelectorAll('[role="menuitem"]')]
      .map(i => i.textContent?.trim());
    expect(items[0]).toBe('Watch in Canvas ↗');
    expect(items).not.toContain('Open in Canvas ↗');
  });

  it.each(['succeeded', 'failed', 'cancelled'])('shows Open in Canvas once the pipeline reads %s', (status) => {
    const { container, queryByTestId } = render(PipelineRow, {
      props: { row: row(status), currentCanvasPipelineId: null },
    });
    const buttons = [...container.querySelectorAll('.actions button.open-btn')]
      .map(b => b.textContent?.trim());
    expect(buttons).toEqual(['Open in Canvas ↗']);
    expect(queryByTestId('pipeline-watch')).toBeNull();
  });
});
