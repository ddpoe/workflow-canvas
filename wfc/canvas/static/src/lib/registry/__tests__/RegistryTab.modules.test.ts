/**
 * Component-mounting Vitest tests for the Registry's Modules view, its
 * Colour picker and the Envs tab's pointer to the command line.
 *
 * The real RegistryTab over a stubbed `fetch`. The picker never decides
 * whether a colour may be used: it asks `POST /api/registry/modules/color-check`
 * and saves with `PATCH /api/registry/modules/{name}`, and these tests answer
 * both as the server would (SOP Canvas Colours, Module Colours).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import RegistryTab from '../RegistryTab.svelte';
import { forgetModuleColors } from '../../shared/moduleColor.svelte.js';

vi.mock('../../shared/shikiHighlight.js', () => ({
  highlight: async (content: string) => `<pre class="shiki"><code>${content}</code></pre>`,
}));

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function version(label: number, newest: boolean) {
  return { label, identity: String(label).repeat(64), registered_at: null, env: 'demo-env',
           runCount: 0, newest, commit: null, envFingerprint: '', image: '', codeTree: '' };
}

const MODULES = [
  { name: '__demo__', description: 'Demo pipeline methods', methods: 2,
    source: 'src/__demo__', color: '#6e5cd6',
    contracts: [{ type: 'output', name: 'labeled', value_type: '.csv', required: true },
                { type: 'param', name: 'top', value_type: 'int', required: false }] },
  { name: 'qc', description: 'Quality-control reports', methods: 1, source: 'src/qc',
    color: '#687b00', contracts: [] },
];

const METHODS = [
  { name: '__demo__preprocess', module: '__demo__', env: 'demo-env', runCount: 0, source: null,
    versions: [version(1, true)] },
  { name: '__demo__label', module: '__demo__', env: 'demo-env', runCount: 0, source: null,
    versions: [version(1, false), version(3, true)] },
  { name: 'qc_report', module: 'qc', env: 'qc-env', runCount: 0, source: null,
    versions: [version(1, true)] },
];

const REFUSED = {
  ok: false, color: '#e06c75', failedTest: 'distance', reserved: 'failed',
  message: 'Too close to a colour Workflow Canvas uses to show status.',
  suggested: '#b25a7b', suggestedName: 'pale violet red',
};

type Call = { method: string; url: string; body: any };
let calls: Call[] = [];
let checkAnswer: () => Response;
let patchAnswer: (body: any) => Response;

beforeEach(() => {
  forgetModuleColors();
  calls = [];
  checkAnswer = () => response(REFUSED);
  patchAnswer = (body) => response({ ...MODULES[0], color: body.color ?? '#6e5cd6' });
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, url, body });
    if (method === 'POST' && url === '/api/registry/modules/color-check') return checkAnswer();
    if (method === 'PATCH') return patchAnswer(body);
    if (url === '/api/registry/modules') return response({ modules: MODULES });
    if (url === '/api/registry/methods') return response({ methods: METHODS });
    if (url === '/api/registry/samples') return response({ samples: [] });
    if (url === '/api/registry/envs') return response({ envs: [] });
    return response({});
  }) as unknown as typeof fetch;
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn(async () => {}) }, configurable: true,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function modulesView() {
  const view = render(RegistryTab, { props: { visible: true } });
  await waitFor(() => expect(view.queryAllByTestId('module-row')).toHaveLength(2));
  return view;
}

function moduleRow(view: Awaited<ReturnType<typeof modulesView>>, name: string): HTMLElement {
  return view.getAllByTestId('module-row').find(r => r.dataset.module === name)!;
}

function tagColor(el: HTMLElement): string {
  return (el.querySelector('.ltag') as HTMLElement).style.getPropertyValue('--c');
}

async function openPicker(view: Awaited<ReturnType<typeof modulesView>>, name = '__demo__') {
  await fireEvent.click(within(moduleRow(view, name)).getByTestId('module-colour-button'));
  return within(await view.findByTestId('colour-picker'));
}

describe('Registry modules list', () => {
  it('lists one closed row per module; opening one shows its contracts, methods and source', async () => {
    const view = await modulesView();
    const demo = moduleRow(view, '__demo__');
    expect(demo).toHaveTextContent('MODULE');
    expect(demo).toHaveTextContent('Demo pipeline methods');
    expect(demo).toHaveTextContent('2 methods →');
    expect(tagColor(demo)).toBe('#6e5cd6');
    expect(view.queryByTestId('module-panel')).toBeNull();

    await fireEvent.click(demo);
    const panel = within(view.getByTestId('module-panel'));
    const contracts = panel.getByTestId('module-contracts');
    expect(within(contracts).getAllByTestId('module-contract-row').map(r => r.textContent?.replace(/\s+/g, ' ').trim()))
      .toEqual(['labeled output', 'top param']);
    const methods = within(panel.getByTestId('module-methods')).getAllByTestId('module-method-row');
    expect(methods).toHaveLength(2);
    expect(methods[0].textContent).toMatch(/^\s*__demo__preprocess\s*version\s*v1\s*demo-env\s*$/);
    expect(methods[1].textContent).toMatch(/^\s*__demo__label\s*version\s*v3\s*demo-env\s*$/);
    expect(panel.getByTestId('module-source')).toHaveTextContent('src/__demo__');

    // "N methods →" goes to the Methods tab filtered to the module.
    await fireEvent.click(within(demo).getByTestId('module-methods-link'));
    expect(view.getByPlaceholderText('Filter methods…')).toHaveValue('__demo__');
  });
});

describe('Module Colour picker', () => {
  it('offers the sixteen palette colours and a Custom box, with the reasons collapsed', async () => {
    const view = await modulesView();
    const picker = await openPicker(view);
    const swatches = picker.getAllByTestId('palette-swatch');
    expect(swatches).toHaveLength(16);
    expect(swatches[0]).toHaveAttribute('title', 'moss');
    expect(swatches.filter(s => s.getAttribute('aria-pressed') === 'true').map(s => s.title)).toEqual(['indigo']);
    expect(picker.getByTestId('custom-colour')).toBeInTheDocument();

    const why = picker.getByTestId('colour-why') as HTMLDetailsElement;
    expect(why.open).toBe(false);
    expect(within(why).getByText("Why some colours can't be used")).toBeInTheDocument();
    expect(why).toHaveTextContent(
      "Some colours can't be used for modules because Workflow Canvas uses them to show status:");
    expect(within(why).getAllByTestId('reserved-colour').map(r => r.textContent?.trim())).toEqual([
      'green = completed', 'amber = running', 'red = failed', 'slate = cancelled',
      'lilac = cached results', 'blue = buttons and links', 'teal = system nodes']);
  });

  it('a typed colour too close to a status colour is refused with the suggested shade, which Apply then saves', async () => {
    const view = await modulesView();
    const picker = await openPicker(view);
    const custom = picker.getByTestId('custom-colour');
    await fireEvent.input(custom, { target: { value: '#E06C75' } });
    await fireEvent.change(custom);

    const refusal = await picker.findByTestId('colour-refusal');
    expect(calls.find(c => c.method === 'POST')).toEqual(
      { method: 'POST', url: '/api/registry/modules/color-check', body: { color: '#E06C75' } });
    expect(refusal).toHaveTextContent('Too close to a colour Workflow Canvas uses to show status.');
    expect(refusal).toHaveTextContent('or pick from the palette.');
    expect(refusal).not.toHaveTextContent('#');
    const use = within(refusal).getByTestId('use-suggested');
    expect(use).toHaveTextContent('Use pale violet red');
    expect(picker.getByTestId('colour-apply')).toBeDisabled();

    // Use fills the Custom box and the preview; Apply still saves it.
    await fireEvent.click(use);
    expect(custom).toHaveValue('#b25a7b');
    expect(picker.queryByTestId('colour-refusal')).toBeNull();
    expect(picker.getByTestId('colour-preview').getAttribute('style')).toContain('#b25a7b');
    expect(calls.filter(c => c.method === 'PATCH')).toHaveLength(0);

    await fireEvent.click(picker.getByTestId('colour-apply'));
    await waitFor(() => expect(view.queryByTestId('colour-picker')).toBeNull());
    expect(calls.filter(c => c.method === 'PATCH')).toEqual([
      { method: 'PATCH', url: '/api/registry/modules/__demo__', body: { color: '#b25a7b' } }]);
  });

  it('a saved colour shows in every view without a reload', async () => {
    const view = await modulesView();
    const picker = await openPicker(view);
    await fireEvent.click(picker.getAllByTestId('palette-swatch').find(s => s.title === 'petrol')!);
    await fireEvent.click(picker.getByTestId('colour-apply'));
    await waitFor(() => expect(tagColor(moduleRow(view, '__demo__'))).toBe('#06809C'));
    expect(calls.filter(c => c.method === 'PATCH').map(c => c.body)).toEqual([{ color: '#06809C' }]);

    const gets = calls.filter(c => c.method === 'GET').length;
    await fireEvent.click(view.getByRole('button', { name: /^Methods/ }));
    const heading = view.getAllByTestId('module-group').find(g => g.dataset.module === '__demo__')!;
    expect(tagColor(within(heading).getByTestId('module-heading'))).toBe('#06809C');
    expect(calls.filter(c => c.method === 'GET').length).toBe(gets);
  });

  it('a refused save says why; Reset to default sends no colour; Cancel closes', async () => {
    patchAnswer = () => response({ detail: REFUSED }, 400);
    const view = await modulesView();
    let picker = await openPicker(view);
    await fireEvent.click(picker.getAllByTestId('palette-swatch').find(s => s.title === 'moss')!);
    await fireEvent.click(picker.getByTestId('colour-apply'));
    expect(await picker.findByTestId('colour-refusal')).toHaveTextContent(
      'Too close to a colour Workflow Canvas uses to show status.');
    expect(tagColor(moduleRow(view, '__demo__'))).toBe('#6e5cd6');

    patchAnswer = () => response({ ...MODULES[0], color: '#3f708d' });
    await fireEvent.click(picker.getByTestId('colour-reset'));
    await waitFor(() => expect(view.queryByTestId('colour-picker')).toBeNull());
    expect(calls.filter(c => c.method === 'PATCH').at(-1)!.body).toEqual({ color: null });
    expect(tagColor(moduleRow(view, '__demo__'))).toBe('#3f708d');

    picker = await openPicker(view);
    await fireEvent.click(picker.getByTestId('colour-cancel'));
    expect(view.queryByTestId('colour-picker')).toBeNull();
  });

  it('a value that is not a colour shows the server\'s reason', async () => {
    checkAnswer = () => response({ detail: 'not a hex colour: red' }, 400);
    const view = await modulesView();
    const picker = await openPicker(view);
    const custom = picker.getByTestId('custom-colour');
    await fireEvent.input(custom, { target: { value: 'red' } });
    await fireEvent.change(custom);
    expect(await picker.findByTestId('colour-error')).toHaveTextContent('not a hex colour: red');
    expect(picker.getByTestId('colour-apply')).toBeDisabled();
  });
});

describe('Envs Register', () => {
  it('opens the pointer to the command line, whose Copy copies the command', async () => {
    const view = await modulesView();
    await fireEvent.click(view.getByRole('button', { name: /^Envs/ }));
    await fireEvent.click(view.getByRole('button', { name: '+ Register env' }));
    const dialog = within(view.getByRole('dialog'));
    expect(dialog.getByRole('heading')).toHaveTextContent('Envs are registered from the command line');
    expect(dialog.getByTestId('env-cli-command')).toHaveTextContent('wfc register-env --help');
    await fireEvent.click(dialog.getByRole('button', { name: 'Copy' }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('wfc register-env --help');
    expect(dialog.getByRole('link', { name: /Registering an Environment/ })).toHaveAttribute(
      'href', 'https://workflow-canvas.readthedocs.io/en/stable/tutorials/registering-an-environment.html');
    await fireEvent.click(dialog.getByRole('button', { name: 'Close' }));
    expect(view.queryByRole('dialog')).toBeNull();
  });
});
