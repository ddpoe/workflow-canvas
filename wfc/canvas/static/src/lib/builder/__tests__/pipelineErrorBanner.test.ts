/**
 * The pipeline-error banner over the canvas.
 *
 * `PipelineErrorBanner.svelte` renders what `setPipelineError` holds: a
 * header line, the items under it as a bulleted list (styled like the
 * Inspector's "✗ The server refused this step" box), the hint, and the ×
 * that dismisses it. A caller with one message passes no items and gets
 * that message alone. The callers that pass items: Validate refused
 * (`Toolbar.svelte`, one item per error the server returned) and the
 * required-params check before Run (`lockFlow.ts::startRun`, one item per
 * param), which this file drives through the Validate button and the store.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import PipelineErrorBanner from '../PipelineErrorBanner.svelte';
import Toolbar from '../Toolbar.svelte';
import { methodNode } from '../nodeData';
import { nodes, edges, runState } from '../stores';
import type { MethodDef, PipelineError } from '../../shared/types';

afterEach(() => { cleanup(); nodes.set([]); edges.set([]); vi.restoreAllMocks(); });

function mount(error: PipelineError, onDismiss = () => {}): HTMLElement {
  const { container } = render(PipelineErrorBanner, { props: { error, onDismiss } });
  return container.querySelector('[data-testid="pipeline-error-banner"]') as HTMLElement;
}

describe('the banner renders a header and its items as a list', () => {
  it('a header with items renders one list item per item under the header, then the hint', () => {
    const banner = mount({
      kind: 'not_found',
      message: 'Validation errors:',
      items: ["Node 'n1': parameter 'x' is not declared", 'Pipeline has no samples'],
      hint: "Fix each issue in the pipeline, or in the method's files if it says so, then click Validate again.",
    });
    expect(banner.querySelector('.pe-head')!.textContent).toBe('Validation errors:');
    expect([...banner.querySelectorAll('li')].map(li => li.textContent))
      .toEqual(["Node 'n1': parameter 'x' is not declared", 'Pipeline has no samples']);
    expect(banner.querySelector('.pe-hint')!.textContent).toBe("Fix each issue in the pipeline, or in the method's files if it says so, then click Validate again.");
    // The refused box's ✗ glyph.
    expect(banner.querySelector('.pe-icon')!.textContent!.trim()).toBe('✗');
  });

  it('a single message renders alone, with no list', () => {
    const banner = mount({ kind: 'unknown', message: 'Validation request failed: offline' });
    expect(banner.querySelector('.pe-message')!.textContent).toBe('Validation request failed: offline');
    expect(banner.querySelector('ul')).toBeNull();
    expect(banner.querySelector('.pe-hint')).toBeNull();
  });

  it('a readiness kind keeps its own icon', () => {
    expect(mount({ kind: 'not_runnable_docker', message: 'Docker is not running' })
      .querySelector('.pe-icon')!.textContent!.trim()).toBe('🐳');
  });

  it('the × dismisses it', async () => {
    const onDismiss = vi.fn();
    const banner = mount({ message: 'x' }, onDismiss);
    await fireEvent.click(banner.querySelector('.pe-close')!);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

describe('Validate refused sets a header and one item per error the server returned', () => {
  afterEach(() => { vi.unstubAllGlobals(); runState.set({ jobId: null, running: false, pipelineError: null }); });

  it('each line appears once, a draft refusal included', async () => {
    const unwired = "Node 'n1': demo.label declares input 'mask', which nothing is connected to.";
    const draft = "Node 'n2' (demo.plot): its draft has no selected script";
    // The validate route's shape: each draft refusal's message is also one
    // of `errors`.
    const verdict = { valid: false, errors: [unwired, draft], warnings: [],
      draftRefusals: [{ nodeId: 'n2', message: draft }] };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.includes('/api/workflow/validate')
      ? new Response(JSON.stringify(verdict))
      : new Response('{}', { status: 404 }))));
    const { getByText } = render(Toolbar);
    await fireEvent.click(getByText('Validate'));
    await vi.waitFor(() => expect(get(runState).pipelineError).not.toBeNull());
    const banner = mount(get(runState).pipelineError!);
    expect(banner.querySelector('.pe-head')!.textContent).toBe('Validation errors:');
    expect([...banner.querySelectorAll('li')].map(li => li.textContent)).toEqual([unwired, draft]);
  });
});

describe('the required-params check before Run sets a header and one item per param', () => {
  it('each missing required param is its own item, with no bullet typed into the text', async () => {
    const root = await import('../../machines/root');
    const dispatched = vi.spyOn(root, 'dispatchRun').mockImplementation(() => {});
    const slots = { inputs: [], outputs: [], params: [
      { name: 'alpha', type: 'str', required: true },
      { name: 'beta', type: 'str', required: true },
    ] };
    const def = { name: 'qc', module: 'mod_a', ...slots,
      versions: [{ label: 1, identity: 'id-1', newest: true, ...slots }] } as unknown as MethodDef;
    nodes.set([methodNode('n1', { x: 0, y: 0 }, def, {})]);
    const { startRun } = await import('../lockFlow');
    await startRun();
    expect(get(runState).pipelineError).toEqual({
      kind: 'not_found',
      message: 'Cannot run — required params missing:',
      items: ['qc.alpha is required', 'qc.beta is required'],
      hint: 'Fill in the listed params (or set a default) before running.',
    });
    expect(dispatched).not.toHaveBeenCalled();
  });
});
