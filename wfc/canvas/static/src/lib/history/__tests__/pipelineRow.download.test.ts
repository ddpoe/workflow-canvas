/**
 * The History pipeline row's "Download pipeline JSON" action (the ⋯ menu
 * item): it downloads the run's frozen copy through the download
 * route, which serves `pipeline.json` byte for byte as an attachment.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import PipelineRow from '../PipelineRow.svelte';
import type { PipelineRowSummary } from '../historyStore';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const ROW: PipelineRowSummary = {
  pipelineId: 'pipe 01',
  name: 'my pipeline',
  status: 'succeeded',
  buckets: { ran: 2, cached: 0, failed: 0, cancelled: 0, running: 0, pending: 0 },
  sampleCount: 1,
  started: Date.now(),
  runs: [],
};

describe('a History pipeline row', () => {
  it('downloads that run\'s frozen pipeline.json through the download route', async () => {
    const clicked: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });
    const { container, getByTestId } = render(PipelineRow, {
      props: { row: ROW, currentCanvasPipelineId: null },
    });
    await fireEvent.click(container.querySelector('button.kebab')!);
    const item = getByTestId('pipeline-menu-download');
    expect(item.textContent?.trim()).toBe('Download pipeline JSON');
    await fireEvent.click(item);

    expect(clicked).toHaveLength(1);
    expect(clicked[0].getAttribute('href')).toBe('/api/pipelines/pipe%2001/download');
    expect(clicked[0].getAttribute('download')).toBe('pipe 01.pipeline.json');
  });
});
