/**
 * Component-mounting Vitest tests for the unarchived-cache toolbar badge.
 *
 * ArchiveBadge polls `GET /api/wfc/archive-status` and renders one of
 * three states: hidden (zero unarchived), amber warning (`⚠ N runs
 * unarchived`, suppressed while a pipeline is in flight), or blue
 * archiving progress. These tests mount the real component with a
 * stubbed `fetch` and drive all three states plus the per-run output
 * dropdown from mocked archive-status payloads.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import ArchiveBadge from '../ArchiveBadge.svelte';
import { archiveStatus, refreshArchiveStatus, type ArchiveStatus } from '../archiveStatus.js';

function idlePayload(over: Partial<ArchiveStatus> = {}): ArchiveStatus {
  return {
    state: 'idle',
    unarchived_runs: 0,
    unarchived_outputs: 0,
    pipeline_running: false,
    progress: null,
    ...over,
  };
}

const archivingPayload: ArchiveStatus = {
  state: 'archiving',
  unarchived_runs: 3,
  unarchived_outputs: 7,
  pipeline_running: false,
  progress: {
    runs_done: 1,
    runs_total: 3,
    current_output: 'aligned_reads.bam',
    per_run: [
      {
        run_id: 5, label: 'qc_report:sampleA', done: 2, total: 2,
        outputs: [
          { name: 'qc.html', status: 'archived' },
          { name: 'qc.json', status: 'archived' },
        ],
      },
      {
        run_id: 6, label: 'align_reads:sampleA', done: 2, total: 6,
        outputs: [
          { name: 'sorted.bam', status: 'archived' },
          { name: 'sorted.bam.bai', status: 'archived' },
          { name: 'aligned_reads.bam', status: 'hashing' },
          { name: 'flagstat.txt', status: 'pending' },
          { name: 'metrics.json', status: 'pending' },
          { name: 'log.txt', status: 'pending' },
        ],
      },
      {
        run_id: 4, label: 'normalize:sampleA', done: 0, total: 2,
        outputs: [
          { name: 'norm.csv', status: 'pending' },
          { name: 'norm.log', status: 'pending' },
        ],
      },
    ],
  },
};

let statusPayload: ArchiveStatus;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  statusPayload = idlePayload();
  archiveStatus.set(null);
  fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => statusPayload,
  } as unknown as Response));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ArchiveBadge states', () => {
  it('is hidden at zero, amber with explicit run count, and suppressed while a pipeline runs', async () => {
    const { queryByTestId, findByTestId } = render(ArchiveBadge);

    // Hidden: zero unarchived → no chrome at all.
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(queryByTestId('archive-badge')).toBeNull();

    // Amber: explicit "N runs unarchived" wording.
    statusPayload = idlePayload({ unarchived_runs: 3, unarchived_outputs: 7 });
    await refreshArchiveStatus();
    const badge = await findByTestId('archive-badge');
    expect(badge.textContent).toContain('3 runs unarchived');
    expect(badge.textContent).toContain('⚠');

    // Suppressed: same counts, but a pipeline is in flight.
    statusPayload = idlePayload({
      unarchived_runs: 3, unarchived_outputs: 7, pipeline_running: true,
    });
    await refreshArchiveStatus();
    await waitFor(() => expect(queryByTestId('archive-badge')).toBeNull());
  });

  it('shows blue run-counted progress and the per-run output dropdown while archiving', async () => {
    statusPayload = archivingPayload;
    const { findByTestId, getAllByTestId } = render(ArchiveBadge);

    const badge = await findByTestId('archive-badge');
    expect(badge.textContent).toContain('archiving runs 1/3');

    // Open the popover: run-level summary + current output.
    await fireEvent.click(badge);
    const popover = await findByTestId('archive-popover');
    expect(popover.textContent).toContain('1 of 3 runs archived');
    const current = within(popover).getByTestId('archive-current-output');
    expect(current.textContent).toContain('aligned_reads.bam');

    // Expand run details, then run 6's own output dropdown.
    await fireEvent.click(within(popover).getByTestId('archive-details-toggle'));
    const rows = getAllByTestId('archive-run-row');
    expect(rows).toHaveLength(3);
    expect(rows[1].textContent).toContain('align_reads:sampleA');
    expect(rows[1].textContent).toContain('2/6 outputs');

    await fireEvent.click(within(rows[1]).getByRole('button'));
    const outs = getAllByTestId('archive-out-row');
    expect(outs).toHaveLength(6);
    expect(outs[0].textContent).toContain('sorted.bam');
    expect(outs[0].textContent).toContain('✓ archived');
    expect(outs[2].textContent).toContain('hashing…');
    expect(outs[3].textContent).toContain('pending');
  });

  it('lingers a green confirmation after an observed archiving → zero transition', async () => {
    statusPayload = archivingPayload;
    const { findByTestId, queryByTestId } = render(ArchiveBadge);
    await findByTestId('archive-badge');

    // Archive pass finishes: next poll reports zero unarchived. Instead
    // of vanishing instantly, the badge lingers as a green confirmation.
    statusPayload = idlePayload();
    await refreshArchiveStatus();
    const badge = await findByTestId('archive-badge');
    expect(badge.textContent).toContain('✓ 3 runs archived');

    // Clicking dismisses the confirmation without waiting out the timer.
    await fireEvent.click(badge);
    await waitFor(() => expect(queryByTestId('archive-badge')).toBeNull());
  });
});
