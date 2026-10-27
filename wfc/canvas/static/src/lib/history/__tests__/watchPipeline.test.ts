/**
 * History's "Watch in canvas" when the canvas is already on a running
 * pipeline: the user is asked first. A "no" leaves the current pipeline
 * watched and loads nothing; a "yes" switches the canvas to the new one.
 * While the canvas is still starting its own run (no pipeline id yet) the
 * watch is refused with a flash.
 *
 * Drives the real stores and the real pipeline machine singleton; only
 * `fetch` and the pipeline-document route are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { PipelineJSON } from '../../shared/types';

const fetchPipelineDocument = vi.fn(async (_id: string): Promise<PipelineJSON> => DOC_B);

vi.mock('../historyApi.js', async importOriginal => ({
  ...(await importOriginal<typeof import('../historyApi.js')>()),
  fetchPipelineDocument: (id: string) => fetchPipelineDocument(id),
}));

import { watchPipelineInCanvas } from '../watchPipeline';
import { dispatchAttach, dispatchReset, dispatchRun, pipelineRunActor } from '../../machines/root';
import { flashToast, nodes, pipelineName, runState } from '../../builder/stores';
import { confirmDialogState } from '../../shared/uiState';

const DOC_B: PipelineJSON = {
  name: 'B',
  nodes: [{ id: 'x', type: 'method', method: 'filter', module: 'mod', params: {} }],
  links: [],
  samples: [],
};

/** The status route answers "running" for any pipeline. */
const STATUS = { overall_status: 'running', thread_alive: true, node_states: {} };

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  fetchMock = vi.fn(async (url: string) => {
    if (String(url).startsWith('/api/workflow/status/')) {
      return { ok: true, status: 200, json: async () => STATUS };
    }
    // Anything else (validate, run) never answers: a submit stays in flight.
    return new Promise(() => {});
  });
  vi.stubGlobal('fetch', fetchMock);
  fetchPipelineDocument.mockClear();
  confirmDialogState.set(null);
  flashToast.set(null);
  nodes.set([]);
  pipelineName.set('A');
});

afterEach(async () => {
  dispatchReset();
  await new Promise(r => setTimeout(r, 0));
  vi.unstubAllGlobals();
});

/** Put the canvas on running pipeline A, as a first Watch in canvas would. */
function watchA() {
  dispatchAttach('pipe-A', { nodes: [{ id: 'a' }] });
  expect(pipelineRunActor.getSnapshot().value).toBe('polling');
  expect(get(runState)).toMatchObject({ running: true, jobId: 'pipe-A' });
}

/** The open switch question; fails when no dialog (or another) is open. */
function switchQuestion() {
  const dialog = get(confirmDialogState);
  expect(dialog?.variant).toBe('switch-watch');
  return dialog!;
}

describe('Watch in canvas while another pipeline is watched', () => {
  it('asks first; a "no" leaves the current pipeline watched and loads nothing', async () => {
    watchA();

    const answered = watchPipelineInCanvas('pipe-B', 'B');
    await Promise.resolve();
    const dialog = switchQuestion();
    expect(dialog).toMatchObject({ currentName: 'A', targetName: 'B' });
    dialog.resolve(false);

    expect(await answered).toBe(false);
    expect(get(confirmDialogState)).toBeNull();
    const snap = pipelineRunActor.getSnapshot();
    expect(snap.value).toBe('polling');
    expect(snap.context.jobId).toBe('pipe-A');
    expect(Object.keys(snap.context.nodeRefs)).toEqual(['a']);
    expect(fetchPipelineDocument).not.toHaveBeenCalled();
  });

  it('a "yes" switches the canvas to the new pipeline, stops the old one\'s node actors and cancels nothing', async () => {
    watchA();
    const oldNodeActor = pipelineRunActor.getSnapshot().context.nodeRefs['a'];
    expect(oldNodeActor.getSnapshot().status).toBe('active');

    const answered = watchPipelineInCanvas('pipe-B', 'B');
    await Promise.resolve();
    switchQuestion().resolve(true);

    expect(await answered).toBe(true);
    expect(fetchPipelineDocument).toHaveBeenCalledWith('pipe-B');
    const snap = pipelineRunActor.getSnapshot();
    expect(snap.value).toBe('polling');
    expect(snap.context.jobId).toBe('pipe-B');
    expect(Object.keys(snap.context.nodeRefs)).toEqual(['x']);
    expect(get(nodes).map(n => n.id)).toEqual(['x']);
    expect(get(runState)).toMatchObject({ running: true, jobId: 'pipe-B' });
    expect(get(flashToast)?.message).toBe('Watching B in canvas');
    // The old run's node actor (and its log stream) is stopped; pipeline A
    // is left running: no cancel request goes out.
    expect(oldNodeActor.getSnapshot().status).toBe('stopped');
    const urls = fetchMock.mock.calls.map(c => String(c[0]));
    expect(urls.filter(u => u.includes('/cancel/'))).toEqual([]);
  });

  it('after a "yes", a canvas with nodes on it is replaced only after the replace question', async () => {
    watchA();
    nodes.set([{ id: 'a', type: 'method', position: { x: 0, y: 0 }, data: {} } as never]);

    const answered = watchPipelineInCanvas('pipe-B', 'B');
    await Promise.resolve();
    switchQuestion().resolve(true);
    await vi.waitFor(() => {
      expect(get(confirmDialogState)?.variant).toBe('dirty-confirm');
    });
    get(confirmDialogState)!.resolve(false);

    expect(await answered).toBe(false);
    expect(fetchPipelineDocument).not.toHaveBeenCalled();
    const snap = pipelineRunActor.getSnapshot();
    expect(snap.value).toBe('polling');
    expect(snap.context.jobId).toBe('pipe-A');
  });

  it('is refused with a flash while the canvas is starting its own run', async () => {
    dispatchRun({ nodes: [{ id: 'own' }] });
    await vi.waitFor(() => {
      expect(pipelineRunActor.getSnapshot().value).toBe('submitting');
    });
    expect(get(runState)).toMatchObject({ running: true, jobId: null });

    expect(await watchPipelineInCanvas('pipe-B', 'B')).toBe(false);

    expect(get(confirmDialogState)).toBeNull();
    expect(get(flashToast)).toMatchObject({ kind: 'error' });
    expect(get(flashToast)?.message).toMatch(/starting a run/);
    expect(pipelineRunActor.getSnapshot().value).toBe('submitting');
    expect(fetchPipelineDocument).not.toHaveBeenCalled();
  });
});
