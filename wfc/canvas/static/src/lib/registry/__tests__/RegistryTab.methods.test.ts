/**
 * Component-mounting Vitest tests for the Registry's Methods view.
 *
 * The real RegistryTab over a stubbed `fetch`: modules as collapsible
 * headings with their methods' labelled tags, an open method's contract
 * tables, the method and version ⋯ menus, the version details card and
 * Refresh. The clipboard and the
 * export download are checked by the value each action passes.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import RegistryTab from '../RegistryTab.svelte';
import { registryFocus } from '../registryFocus.js';

vi.mock('../shikiHighlight.js', () => ({
  highlight: async (content: string) => `<pre class="shiki"><code>${content}</code></pre>`,
}));

const V1 = 'a'.repeat(64);
const V2 = 'b'.repeat(64);

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function version(label: number, identity: string, commit: string | null, runCount: number,
                 newest: boolean) {
  return { label, identity, registered_at: `2026-10-0${label}T09:30:00+00:00`, env: 'tp132',
           runCount, newest, commit, envFingerprint: 'e'.repeat(64),
           image: 'docker://local/tp132-byo@sha256:' + 'f'.repeat(64), codeTree: 'c'.repeat(40) };
}

const METHODS = [
  { name: 'plot_tf_activity', module: 'tp132', env: 'tp132', runCount: 3, source: null,
    versions: [version(1, V1, null, 2, false), version(2, V2, '3b8712511b' + '0'.repeat(30), 1, true)] },
  { name: 'prepare_cohort', module: 'tp132', env: 'tp132', runCount: 3, source: null,
    versions: [version(1, V1, '52ca89ba21' + '0'.repeat(30), 3, true)] },
  { name: 'count_qc', module: 'qc_tools', env: 'scanpy', runCount: 0, source: null,
    versions: [version(1, V2, null, 0, true)] },
];


let contractNote = '';
let detailCalls = 0;

function detail() {
  return {
    files: [
      { name: 'plot_tf_activity.py', language: 'python', content: `print("plot")${contractNote}` },
      { name: 'method.yaml', language: 'yaml', content: 'env: tp132' },
    ],
    contract: {
      input_slots: { tf_acts: { type: '.csv', required: true, description: 'TF activity table' } },
      output_slots: { barplot: { type: '.pdf' } },
      params_schema: {
        top: { type: 'int', default: 30, required: false, constraints: { min: 1 },
               description: 'How many TFs to plot' },
        title: { type: 'str', columns: ['a'], new_column: 'x' },
      },
      executor: 'python',
    },
    env: 'tp132',
    version: version(2, V2, '3b8712511b' + '0'.repeat(30), 1, true),
  };
}

beforeEach(() => {
  contractNote = '';
  detailCalls = 0;
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url.includes('/detail')) { detailCalls += 1; return jsonResponse(detail()); }
    if (url === '/api/registry/methods') return jsonResponse({ methods: METHODS });
    if (url === '/api/registry/modules') return jsonResponse({ modules: [] });
    if (url === '/api/registry/samples') return jsonResponse({ samples: [] });
    return jsonResponse({});
  }) as unknown as typeof fetch;
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn(async () => {}) }, configurable: true,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function methodsView() {
  const view = render(RegistryTab, { props: { visible: true } });
  await fireEvent.click(await view.findByRole('button', { name: /^Methods/ }));
  await waitFor(() => expect(view.queryAllByTestId('method-row')).toHaveLength(3));
  return view;
}

function row(view: Awaited<ReturnType<typeof methodsView>>, name: string): HTMLElement {
  return view.getAllByTestId('method-row').find(r => r.textContent?.includes(name))!;
}

describe('Registry methods list', () => {
  it('groups methods under module headings, open by default, each row its method, version and env tags', async () => {
    const view = await methodsView();
    const groups = view.getAllByTestId('module-group');
    expect(groups.map(g => g.dataset.module)).toEqual(['tp132', 'qc_tools']);
    const heading = within(groups[0]).getByTestId('module-heading');
    expect(heading).toHaveTextContent('MODULE');
    expect(heading).toHaveTextContent('tp132');
    expect(within(groups[0]).getByTestId('module-count')).toHaveTextContent('2 methods');
    expect(within(groups[1]).getByTestId('module-count')).toHaveTextContent('1 method');
    // The module tag takes the module's own colour.
    expect((heading.querySelector('.ltag') as HTMLElement).style.getPropertyValue('--c')).toMatch(/^hsl\(/);

    const plot = row(view, 'plot_tf_activity');
    expect(plot).toHaveTextContent('method');
    expect(plot).toHaveTextContent('version');
    expect(plot).toHaveTextContent('v2');
    expect(plot).toHaveTextContent('env');
    expect(plot).toHaveTextContent('tp132');
    expect(plot).not.toHaveTextContent('runs');
  });

  it('collapses a module on click and Collapse all / Expand all set every module', async () => {
    const view = await methodsView();
    await fireEvent.click(within(view.getAllByTestId('module-group')[0]).getByTestId('module-heading'));
    expect(view.getAllByTestId('method-row')).toHaveLength(1);

    await fireEvent.click(view.getByTestId('expand-all'));
    expect(view.getAllByTestId('method-row')).toHaveLength(3);
    await fireEvent.click(view.getByTestId('collapse-all'));
    expect(view.queryAllByTestId('method-row')).toHaveLength(0);
  });

  it('a filter match and a Registry link open the module they land in', async () => {
    const view = await methodsView();
    await fireEvent.click(view.getByTestId('collapse-all'));

    await fireEvent.input(view.getByPlaceholderText('Filter methods…'), { target: { value: 'count' } });
    expect(view.getAllByTestId('method-row').map(r => r.textContent)).toEqual([
      expect.stringContaining('count_qc')]);

    await fireEvent.input(view.getByPlaceholderText('Filter methods…'), { target: { value: '' } });
    expect(view.queryAllByTestId('method-row')).toHaveLength(0);
    registryFocus.set({ sub: 'methods', module: 'tp132', method: 'plot_tf_activity',
                        versionIdentity: V2, versionLabel: 'v2' });
    await waitFor(() => expect(view.getByTestId('method-panel')).toBeInTheDocument());
    expect(view.getAllByTestId('method-row').map(r => r.textContent)).toEqual([
      expect.stringContaining('plot_tf_activity'), expect.stringContaining('prepare_cohort')]);
  });
});

describe('Registry contract tables', () => {
  it('boxes each section, rows its name, type and default, and lists what an entry declares when opened', async () => {
    const view = await methodsView();
    await fireEvent.click(row(view, 'plot_tf_activity'));
    const tables = await view.findAllByTestId('contract-table');
    expect(tables.map(t => t.dataset.kind)).toEqual(['input', 'output', 'param']);
    // Each box's header bar: its caption and count head the name column.
    const header = (t: HTMLElement) => within(t).getByTestId('contract-header').textContent?.replace(/\s+/g, ' ').trim();
    expect(header(tables[0])).toBe('Inputs1 Type');
    expect(header(tables[2])).toBe('Params2 Type Default');

    const params = within(tables[2]);
    const entry = (name: string) => params.getAllByTestId('contract-row').find(r => r.dataset.name === name)!;
    // A row: name, type and the default in Python form, in the type's colour.
    expect(within(entry('top')).getByTestId('cell-default')).toHaveTextContent('30');
    expect(within(entry('top')).getByTestId('cell-default')).toHaveClass('t-int');
    expect(within(entry('top')).getByTestId('cell-type').querySelector('.tchip')).toHaveClass('t-int');
    // A missing default reads None.
    expect(within(entry('title')).getByTestId('cell-default')).toHaveTextContent('None');
    // A default that fits is not a button that opens anything.
    expect(within(entry('top')).getByTestId('cell-default')).toBeDisabled();
    expect(within(entry('top')).queryByTestId('contract-details')).toBeNull();

    // Opening a row lists, under FIELD | VALUE, its description and then what it declares.
    const fields = (name: string) => within(entry(name)).getAllByTestId('contract-field')
      .map(f => [f.dataset.key, within(f).getByRole('button').textContent]);
    await fireEvent.click(within(entry('top')).getByTestId('row-toggle'));
    expect(within(entry('top')).getByTestId('details-header')).toHaveTextContent(/Field\s*Value/);
    expect(fields('top')).toEqual([
      ['description', 'How many TFs to plot'], ['required', 'False'], ['constraints', "{'min': 1}"]]);
    await fireEvent.click(within(entry('title')).getByTestId('row-toggle'));
    expect(fields('title')).toEqual([
      ['description', 'No description in method.yaml'], ['columns', "['a']"], ['new_column', "'x'"]]);

    // A slot lists only the fields it declares: tf_acts declares required, not multiple.
    const slot = within(tables[0]).getByTestId('contract-row');
    expect(within(slot).getByTestId('cell-type')).toHaveTextContent('csv');
    await fireEvent.click(within(slot).getByTestId('row-toggle'));
    expect(within(slot).getAllByTestId('contract-field').map(f => f.dataset.key)).toEqual(['description', 'required']);
  });

  it('the divider moves with the arrow keys, between its bounds, one width for every open method', async () => {
    localStorage.removeItem('wfc.registry.contractWidth');
    const { contractWidth, CONTRACT_DEFAULT, CONTRACT_MAX, CONTRACT_MIN } = await import('../contractWidth.js');
    contractWidth.set(CONTRACT_DEFAULT);
    const view = await methodsView();
    await fireEvent.click(row(view, 'plot_tf_activity'));
    const divider = await view.findByTestId('split-divider');
    const width = () => divider.closest('.split')!.getAttribute('style');

    expect(width()).toContain(`--contract-w: ${CONTRACT_DEFAULT}%`);
    await fireEvent.keyDown(divider, { key: 'ArrowRight' });
    expect(width()).toContain(`--contract-w: ${CONTRACT_DEFAULT + 2}%`);
    expect(localStorage.getItem('wfc.registry.contractWidth')).toBe(String(CONTRACT_DEFAULT + 2));
    for (let i = 0; i < 40; i++) await fireEvent.keyDown(divider, { key: 'ArrowRight' });
    expect(width()).toContain(`--contract-w: ${CONTRACT_MAX}%`);
    for (let i = 0; i < 40; i++) await fireEvent.keyDown(divider, { key: 'ArrowLeft' });
    expect(width()).toContain(`--contract-w: ${CONTRACT_MIN}%`);
    contractWidth.set(CONTRACT_DEFAULT);
  });

  it('opens on the main script, the first file the detail lists', async () => {
    const view = await methodsView();
    await fireEvent.click(row(view, 'plot_tf_activity'));
    await view.findByText('print("plot")');
    expect(view.getAllByTestId('file-tab')[0]).toHaveClass('active');
    expect(view.getAllByTestId('file-tab')[0]).toHaveTextContent('plot_tf_activity.py');
  });
});

describe('Registry menus', () => {
  it('the method ⋯ lists its actions and never opens the row; a choice, Escape or an outside click closes it', async () => {
    const view = await methodsView();
    const plot = row(view, 'plot_tf_activity');
    await fireEvent.click(within(plot).getByTestId('method-menu-button'));
    const menu = view.getByTestId('method-menu');
    expect([...menu.querySelectorAll('[role="menuitem"]')].map(i => i.textContent?.trim())).toEqual([
      expect.stringContaining('Version details…'),
      expect.stringContaining('Copy version identity'),
      expect.stringContaining('Export script and contract'),
      expect.stringContaining('Register a new version…'),
    ]);
    expect(view.queryByTestId('method-panel')).toBeNull();

    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(view.queryByTestId('method-menu')).toBeNull();

    await fireEvent.click(within(plot).getByTestId('method-menu-button'));
    await fireEvent.click(document.body);
    expect(view.queryByTestId('method-menu')).toBeNull();

    await fireEvent.click(within(plot).getByTestId('method-menu-button'));
    await fireEvent.click(view.getByTestId('menu-copy-identity'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(V2);
    expect(view.queryByTestId('method-menu')).toBeNull();

    // ⋯ again closes it.
    await fireEvent.click(within(plot).getByTestId('method-menu-button'));
    await fireEvent.click(within(plot).getByTestId('method-menu-button'));
    expect(view.queryByTestId('method-menu')).toBeNull();
  });

  it('Export downloads the version\'s zip from the export route', async () => {
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.getAttribute('href') ?? '');
    });
    const view = await methodsView();
    await fireEvent.click(within(row(view, 'plot_tf_activity')).getByTestId('method-menu-button'));
    await fireEvent.click(view.getByTestId('menu-export'));
    expect(clicked).toEqual([`/api/registry/methods/tp132/plot_tf_activity/export?version=${V2}`]);
  });

  it('a version\'s ⋯ in the picker lists its actions; Copy commit copies the commit', async () => {
    const view = await methodsView();
    await fireEvent.click(row(view, 'plot_tf_activity'));
    await fireEvent.click(await view.findByTestId('version-picker'));
    const entries = within(await view.findByTestId('version-toggle'));
    await fireEvent.click(entries.getAllByTestId('version-menu-button')[0]);
    const menu = view.getByTestId('version-menu');
    expect([...menu.querySelectorAll('[role="menuitem"]')].map(i => i.textContent?.trim())).toEqual([
      expect.stringContaining('Version details…'),
      expect.stringContaining('Copy version identity'),
      expect.stringContaining('Copy commit'),
      expect.stringContaining('Export script and contract'),
    ]);
    await fireEvent.click(view.getByTestId('menu-copy-commit'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('3b8712511b' + '0'.repeat(30));
  });
});

describe('Registry version details', () => {
  it('shows each field, with "not recorded" for a version whose commit this database did not record', async () => {
    const view = await methodsView();
    await fireEvent.click(within(row(view, 'plot_tf_activity')).getByTestId('method-menu-button'));
    await fireEvent.click(view.getByTestId('menu-details'));
    let card = within(view.getByTestId('version-details'));
    expect(card.getByTestId('details-registered')).toHaveTextContent('2026-10-02 09:30 UTC');
    expect(card.getByTestId('details-commit')).toHaveTextContent('3b8712511b');
    expect(card.getByTestId('details-runs')).toHaveTextContent('1 on this version (3 on all)');
    expect(card.getByTestId('details-image')).toHaveTextContent('docker://local/tp132-byo@sha256:ffffffff…');
    expect(card.getByTestId('details-code-tree')).toHaveAttribute('title', 'c'.repeat(40));
    await fireEvent.click(card.getByTestId('copy-code-tree'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('c'.repeat(40));
    await fireEvent.click(card.getByTestId('copy-identity'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(V2);
    await fireEvent.click(card.getByTestId('version-details-close'));
    expect(view.queryByTestId('version-details')).toBeNull();

    await fireEvent.click(row(view, 'plot_tf_activity'));
    await fireEvent.click(await view.findByTestId('version-picker'));
    const entries = within(await view.findByTestId('version-toggle'));
    await fireEvent.click(entries.getAllByTestId('version-menu-button')[1]);
    await fireEvent.click(view.getByTestId('menu-details'));
    card = within(view.getByTestId('version-details'));
    expect(card.getByTestId('details-commit')).toHaveTextContent('not recorded');
    expect(card.queryByTestId('copy-commit')).toBeNull();
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(view.queryByTestId('version-details')).toBeNull();
  });
});

describe('Registry refresh', () => {
  it('shows it is reloading, then the open method\'s new detail, and says it was updated', async () => {
    const view = await methodsView();
    await fireEvent.click(row(view, 'plot_tf_activity'));
    await view.findByText('print("plot")');
    const calls = detailCalls;

    contractNote = ' # changed on the server';
    let release!: () => void;
    const gate = new Promise<void>(r => { release = r; });
    const plain = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/registry/methods') await gate;
      return (plain as any)(input);
    }) as unknown as typeof fetch;

    await fireEvent.click(view.getByTestId('registry-refresh'));
    expect(view.getByTestId('refresh-note')).toHaveTextContent('Reloading…');
    expect(view.getByTestId('registry-refresh').querySelector('.spin')).toHaveClass('on');
    release();

    await view.findByText('print("plot") # changed on the server');
    expect(detailCalls).toBe(calls + 1);
    await waitFor(() => expect(view.getByTestId('refresh-note')).toHaveTextContent('Updated just now'));
  });
});
