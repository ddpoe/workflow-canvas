/**
 * How the run detail panel shows a failed run.
 *
 * The failure lives in the hero card, which every tab shows: its message,
 * cut to two lines, and a See output pill that opens the Output tab. The
 * traceback is not repeated in Overview; it is the stderr the Output tab's
 * log already shows. The Output tab's label carries a ✗ only for a failed
 * run. Component test over the real panel; the boundary stubs are `fetch`
 * and `EventSource`.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import RunDetailPanel from '../RunDetailPanel.svelte';
import type { WfcRun } from '../historyApi';
import { openRunOn, takePendingSection } from '../historyStore';

const MESSAGE = "KeyError: 'gene_symbol'";
const TRACEBACK = 'Traceback (most recent call last):\n  File "normalize.py", line 61, in main\n' + MESSAGE;

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: 'r1', module: 'mod', method: 'normalize', version: 3, versionIdentity: null,
    timestamp: 1, duration: 14.2, status: 'success', inputs: {}, outputs: {},
    outputAvailability: {}, metrics: {}, dataSource: 'S1', parentRunIds: [],
    upstreamRunIds: [], parents: [], codeFingerprint: null, envName: null,
    envFingerprint: null, gitCommit: null, wfcVersion: null, pipelineDocHash: null,
    keyedUnderEarlierRule: false, paramsHash: null, inputsHash: null, paramTypes: {},
    experimentId: 'p', runName: 'normalize/S1', nid: 'v1',
    user: '', favorite: false, pipelineId: null, scriptPath: null,
    ...partial,
  };
}

function serve(run: WfcRun): void {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    const body = url === `/api/wfc/run/${run.id}` ? run : [];
    return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response;
  }) as unknown as typeof fetch;
}

/** The log stream: records each one opened so a test can push frames. */
class FakeEventSource {
  static last: FakeEventSource | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeEventSource.last = this; }
  push(frame: object): void { this.onmessage?.({ data: JSON.stringify(frame) }); }
  close(): void {}
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  FakeEventSource.last = null;
});

const failed = mkRun({ status: 'failed', error_message: MESSAGE, error_traceback: TRACEBACK });

describe('run detail, a failed run', () => {
  it('shows the message in the hero and not the traceback in Overview', async () => {
    serve(failed);
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    const err = await view.findByTestId('run-hero-error');
    expect(err.textContent).toContain(MESSAGE);
    expect(view.container.textContent).not.toContain('Traceback (most recent call last)');
  });

  it('marks the Output tab with ✗, and See output opens it', async () => {
    serve(failed);
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    const outputTab = await view.findByRole('button', { name: /^Output/ });
    expect(outputTab.textContent).toContain('✗');

    await fireEvent.click(view.getByRole('button', { name: /See output/ }));
    expect(outputTab.classList.contains('active')).toBe(true);
    expect(view.getByTestId('run-hero-error')).toBeTruthy();
  });

  it('shows the log once on Output, with no second error box under it', async () => {
    serve(failed);
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    await fireEvent.click(await view.findByRole('button', { name: /^Output/ }));
    FakeEventSource.last!.push({ type: 'stderr', data: 'Traceback (most recent call last):' });
    FakeEventSource.last!.push({ type: 'stderr', data: MESSAGE });
    FakeEventSource.last!.push({ type: 'terminal', status: 'failed', error_message: MESSAGE, error_traceback: TRACEBACK });

    const pane = await view.findByTestId('log-pane');
    expect(pane.textContent).toContain(MESSAGE);
    // The log, plus the hero's one line: the traceback is never printed twice.
    const tracebacks = (view.container.textContent ?? '').split('Traceback (most recent call last)').length - 1;
    expect(tracebacks).toBe(1);
  });

  it('says so in the log bar when the stream is lost', async () => {
    serve(failed);
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    await fireEvent.click(await view.findByRole('button', { name: /^Output/ }));
    FakeEventSource.last!.onerror?.();
    expect((await view.findByTestId('log-note')).textContent).toBe('Connection lost');
  });
});

describe('run detail, failures in other shapes', () => {
  it('a traceback message shows its exception line; the title keeps the whole text', async () => {
    const tb = 'Traceback (most recent call last):\n  File "m.py", line 3\nValueError: bad input column';
    serve(mkRun({ status: 'failed', error_message: tb }));
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    const err = await view.findByTestId('run-hero-error');
    const msg = err.querySelector('.err-box-msg') as HTMLElement;
    expect(msg.textContent).toBe('ValueError: bad input column');
    expect(msg.title).toBe(tb);
  });

  it('a failed run with no message still says it failed, with See output', async () => {
    serve(mkRun({ status: 'failed' }));
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    const err = await view.findByTestId('run-hero-error');
    expect(err.textContent).toContain('This step failed');
    expect(view.getByRole('button', { name: /See output/ })).toBeTruthy();
  });

  it('✗ Failed on the open run switches it to Output', async () => {
    serve(failed);
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    const outputTab = await view.findByRole('button', { name: /^Output/ });
    expect(outputTab.classList.contains('active')).toBe(false);

    openRunOn('r1', 'output');         // what the card's ✗ Failed calls
    await waitFor(() => expect(outputTab.classList.contains('active')).toBe(true));
    // Taken once: a later plain open of this run starts on Overview.
    expect(takePendingSection('r1')).toBe('overview');
  });

  it('✗ Failed on another run while Output is open lands that run on Output', async () => {
    const other = mkRun({ id: 'r2', status: 'failed', error_message: MESSAGE });
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      const body = url === '/api/wfc/run/r1' ? failed : url === '/api/wfc/run/r2' ? other : [];
      return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response;
    }) as unknown as typeof fetch;
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    await fireEvent.click(await view.findByRole('button', { name: /^Output/ }));

    openRunOn('r2', 'output');         // the store's selection, then the panel's new run
    await view.rerender({ runId: 'r2' });
    const outputTab = await view.findByRole('button', { name: /^Output/ });
    await waitFor(() => expect(view.container.textContent).toContain('normalize'));
    expect(outputTab.classList.contains('active')).toBe(true);
    expect(FakeEventSource.last!.url).toContain('/api/wfc/run/r2/');
  });

  it('a log of only warnings still shows the launch traceback after them', async () => {
    serve(failed);
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    await fireEvent.click(await view.findByRole('button', { name: /^Output/ }));
    FakeEventSource.last!.push({ type: 'stderr', data: 'warning: sample restored from cache' });
    FakeEventSource.last!.push({ type: 'terminal', status: 'failed', error_message: 'boom',
      error_traceback: 'Traceback (most recent call last):\n  File "dispatch.py", line 656\nOSError: launch failed' });
    const pane = await view.findByTestId('log-pane');
    expect(pane.textContent).toContain('warning: sample restored from cache');
    expect(pane.textContent).toContain('OSError: launch failed');
  });

  it('a run that wrote no output shows the traceback its ending carried', async () => {
    serve(failed);
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    await fireEvent.click(await view.findByRole('button', { name: /^Output/ }));
    FakeEventSource.last!.push({ type: 'terminal', status: 'failed', error_message: MESSAGE, error_traceback: TRACEBACK });
    const pane = await view.findByTestId('log-pane');
    expect(pane.textContent).toContain('File "normalize.py", line 61');
    expect(pane.querySelectorAll('.log-stderr').length).toBe(3);
    expect(view.queryByText('No output captured.')).toBeNull();
  });
});

describe('run detail, the log box', () => {
  /** jsdom lays nothing out: give every element a content and a box height. */
  function fakeHeights(content: number, box: number): void {
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(content);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(box);
  }

  it('a run opened while running stops following once its stream ends', async () => {
    fakeHeights(900, 200);
    serve(mkRun({ status: 'running' }));
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    await fireEvent.click(await view.findByRole('button', { name: /^Output/ }));
    FakeEventSource.last!.push({ type: 'stdout', data: 'tick 1' });
    const pane = await view.findByTestId('log-pane');
    await waitFor(() => expect(pane.scrollTop).toBe(900));        // following
    FakeEventSource.last!.push({ type: 'terminal', status: 'success' });
    await waitFor(() => expect(pane.scrollTop).toBe(0));          // back to line 1
  });

  it('another run opens its log box closed', async () => {
    fakeHeights(900, 200);
    const other = mkRun({ id: 'r2' });
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      const body = url === '/api/wfc/run/r1' ? failed : url === '/api/wfc/run/r2' ? other : [];
      return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response;
    }) as unknown as typeof fetch;
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    await fireEvent.click(await view.findByRole('button', { name: /^Output/ }));
    FakeEventSource.last!.push({ type: 'stdout', data: 'line' });
    await fireEvent.click(await view.findByRole('button', { name: /Show more/ }));
    expect(view.getByTestId('log-pane').classList.contains('open')).toBe(true);

    openRunOn('r2', 'output');
    await view.rerender({ runId: 'r2' });
    await waitFor(() => expect(FakeEventSource.last!.url).toContain('/r2/'));
    FakeEventSource.last!.push({ type: 'stdout', data: 'line' });
    await waitFor(() => expect(view.getByTestId('log-pane').classList.contains('open')).toBe(false));
  });
});

describe('run detail, a succeeded run', () => {
  it('has no error in the hero and no ✗ on Output', async () => {
    serve(mkRun({ status: 'success' }));
    const view = render(RunDetailPanel, { props: { runId: 'r1' } });
    const outputTab = await view.findByRole('button', { name: /^Output/ });
    expect(outputTab.textContent).not.toContain('✗');
    expect(view.queryByTestId('run-hero-error')).toBeNull();
  });
});
