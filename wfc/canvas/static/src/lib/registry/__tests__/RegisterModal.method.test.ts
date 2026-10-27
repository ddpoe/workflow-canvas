/**
 * Component-mounting Vitest tests for the Register modal's Method kind.
 *
 * The New method | New method version toggle, the Module → Method →
 * Folder order with the now / next version, the method ⋯'s "Register a new
 * version…" opening it with both chosen, and a taken name offering the
 * switch. One button that checks, then confirms — red refusals and
 * Check again; the confirm card with its Before / After tables; "nothing to
 * register"; the new method's Registering card — with ← Back from every
 * state to the filled form, and × closing having registered nothing. The
 * registration POSTs are a stubbed `fetch`; the dry run's answers are the
 * route's shapes.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import RegisterModal from '../RegisterModal.svelte';
import RegistryTab from '../RegistryTab.svelte';

vi.mock('../shikiHighlight.js', () => ({
  highlight: async (content: string) => `<pre class="shiki"><code>${content}</code></pre>`,
}));

function response(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const PASSING = [
  { status: 'ok', label: 'directory exists' },
  { status: 'ok', label: 'method.yaml present' },
  { status: 'ok', label: 'module registered' },
];

function versionRow(label: number, newest: boolean) {
  return { label, identity: `${label}`.repeat(64), registered_at: '2026-10-21T13:18:00Z', env: 'tp132',
           runCount: 0, newest, commit: null, envFingerprint: 'e'.repeat(64),
           image: 'docker://x', codeTree: 'c'.repeat(40) };
}

const METHODS = [
  { name: 'plot_tf_activity', module: 'tp132', env: 'tp132', runCount: 0, source: null,
    versions: [versionRow(1, false), versionRow(2, true)] },
  { name: 'prepare_cohort', module: 'tp132', env: 'tp132', runCount: 0, source: null,
    versions: [versionRow(1, true)] },
];

const LONG = 'Infiltrating Lobular Carcinoma';

const CHANGED_PLAN = {
  outcome: 'changed', label: 3, newestLabel: 2, token: 't-changed',
  message: 'tp132/plot_tf_activity is already registered (newest v2); this adds v3.',
  changes: {
    code: { changed: ['plot_tf_activity.py'], added: ['utils.py'], removed: [] },
    slots: [
      { name: 'tf_acts', kind: 'input', change: 'changed',
        before: { type: '.csv', required: true }, after: { type: '.csv', required: false } },
      { name: 'barplot_png', kind: 'output', change: 'added', before: null, after: { type: '.png' } },
    ],
    params: [
      { name: 'contrast_name', change: 'removed', before: { type: 'str', default: LONG }, after: null },
      { name: 'n_bins', change: 'changed',
        before: { type: 'int', default: 50, constraints: { min: 1, max: 100 } },
        after: { type: 'int', default: 50, constraints: { min: 1, max: 500 } } },
      { name: 'top', change: 'changed', before: { type: 'int', default: 30 }, after: { type: 'int', default: 20 } },
    ],
    run: {},
    env: { before: 'tp132', after: 'tp132', rebuilt: false },
  },
  holds: { script: 'plot_tf_activity.py', helpers: ['utils.py'], ignored: ['notes.md', 'test.csv'] },
};

const NEW_PLAN = {
  outcome: 'new', label: 1, newestLabel: null, token: 't-new',
  message: 'tp132/volcano_plot is not registered yet; this registers it as v1.',
  changes: {
    code: { changed: [], added: ['volcano_plot.py'], removed: [] },
    slots: [
      { name: 'deseq2_results', kind: 'input', change: 'added', before: null,
        after: { type: '.csv', required: true, columns: { strict: ['padj'] } } },
      { name: 'volcano', kind: 'output', change: 'added', before: null, after: { type: '.pdf' } },
    ],
    params: [
      { name: 'padj_cutoff', change: 'added', before: null,
        after: { type: 'float', default: 0.05, constraints: { min: 0, max: 1 } } },
    ],
    run: { executor: [null, 'python'], gpus: [null, false] },
    env: { before: null, after: 'tp132', rebuilt: false },
  },
  holds: { script: 'volcano_plot.py', helpers: [], ignored: ['README.md'] },
};

const SAME_PLAN = { ...CHANGED_PLAN, outcome: 'same', label: 2, token: 't-same',
                    changes: { ...CHANGED_PLAN.changes, slots: [], params: [],
                               code: { changed: [], added: [], removed: [] } } };

let dryRuns: any[] = [];
let writes: any[] = [];
let dryAnswer: () => Response = () => response({ ok: true, preChecks: PASSING, plan: CHANGED_PLAN });
let writeAnswer: () => Response = () => response({ ok: true, preChecks: PASSING, message: '' });

beforeEach(() => {
  dryRuns = [];
  writes = [];
  dryAnswer = () => response({ ok: true, preChecks: PASSING, plan: CHANGED_PLAN });
  writeAnswer = () => response({ ok: true, preChecks: PASSING, message: '' });
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/registry/methods?dryRun=true') {
      dryRuns.push(JSON.parse(String(init?.body)));
      return dryAnswer();
    }
    if (url === '/api/registry/methods' && init?.method === 'POST') {
      writes.push(JSON.parse(String(init?.body)));
      return writeAnswer();
    }
    if (url === '/api/registry/methods') return response({ methods: METHODS });
    if (url === '/api/registry/modules') return response({ modules: [{ name: 'tp132', description: '', contracts: [], methods: 2, source: '' }] });
    if (url === '/api/registry/samples') return response({ samples: [] });
    return response({});
  }) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function modal(props: Record<string, unknown> = {}) {
  const onClose = vi.fn();
  const onRegistered = vi.fn();
  const view = render(RegisterModal, {
    props: { initialKind: 'method', moduleNames: ['tp132'], methods: METHODS, onClose, onRegistered, ...props },
  });
  return { view, onClose, onRegistered };
}

async function choose(select: HTMLElement, value: string) {
  await fireEvent.change(select, { target: { value } });
}

async function fillVersion(view: ReturnType<typeof modal>['view']) {
  await fireEvent.click(view.getByTestId('mode-version'));
  await choose(view.getByTestId('register-module'), 'tp132');
  await choose(view.getByTestId('register-method'), 'plot_tf_activity');
  await fireEvent.input(view.getByTestId('register-folder'), { target: { value: 'src/tp132/plot_tf_activity' } });
}

describe('Register modal, Method kind', () => {
  it('opens on New method; New method version enables Module → Method → Folder and names now and next', async () => {
    const { view } = modal();
    expect(view.getByTestId('mode-new')).toHaveAttribute('aria-pressed', 'true');
    expect(view.getByTestId('register-primary')).toHaveTextContent('Register method');

    await fireEvent.click(view.getByTestId('mode-version'));
    expect(view.getByTestId('register-primary')).toHaveTextContent('Register version');
    expect(view.getByTestId('register-method')).toBeDisabled();
    expect(view.getByTestId('register-folder')).toBeDisabled();
    await choose(view.getByTestId('register-module'), 'tp132');
    expect(view.getByTestId('register-method')).not.toBeDisabled();
    const options = [...view.getByTestId('register-method').querySelectorAll('option')].map(o => o.textContent);
    expect(options).toContain('plot_tf_activity · v2');
    expect(view.getByTestId('register-folder')).toBeDisabled();
    await choose(view.getByTestId('register-method'), 'plot_tf_activity');
    expect(view.getByTestId('register-folder')).not.toBeDisabled();
    expect(view.getByTestId('register-now-next')).toHaveTextContent(/now\s*version\s*v2\s*next\s*version\s*v3/);
  });

  it('a New method name its module already has says so and switches with the choices kept', async () => {
    const { view } = modal();
    await fireEvent.input(view.getByTestId('register-folder'), { target: { value: 'src/tp132/plot_tf_activity' } });
    await choose(view.getByTestId('register-module'), 'tp132');
    expect(view.getByTestId('register-taken')).toHaveTextContent(
      'tp132 already has plot_tf_activity, latest v2.');

    await fireEvent.click(view.getByTestId('switch-to-version'));
    expect(view.getByTestId('mode-version')).toHaveAttribute('aria-pressed', 'true');
    expect(view.getByTestId('register-module')).toHaveValue('tp132');
    expect(view.getByTestId('register-method')).toHaveValue('plot_tf_activity');
    expect(view.getByTestId('register-folder')).toHaveValue('src/tp132/plot_tf_activity');
  });

  it('Browse lists a registered method\'s folder for New method version, not for New method', async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const answer = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).startsWith('/api/fs/browse')
        ? response({ path: 'methods', entries: [{ name: 'plot_tf_activity', kind: 'dir' },
                                                { name: 'volcano_plot', kind: 'dir' }] })
        : answer(input, init));
    const { view } = modal({ registeredMethodDirs: ['methods/plot_tf_activity'] });
    const listed = async () => {
      await fireEvent.click(view.getByRole('button', { name: 'Browse…' }));
      await view.findByText('volcano_plot');
      const names = view.queryAllByText(/^(plot_tf_activity|volcano_plot)$/).map(e => e.textContent);
      await fireEvent.keyDown(window, { key: 'Escape' });
      return names;
    };
    expect(await listed()).toEqual(['volcano_plot']);

    await fireEvent.click(view.getByTestId('mode-version'));
    await choose(view.getByTestId('register-module'), 'tp132');
    await choose(view.getByTestId('register-method'), 'plot_tf_activity');
    expect(await listed()).toEqual(['plot_tf_activity', 'volcano_plot']);
  });

  it('⋯ → Register a new version… opens New method version with the module and method chosen', async () => {
    const view = render(RegistryTab, { props: { visible: true } });
    await fireEvent.click(await view.findByRole('button', { name: /^Methods/ }));
    await waitFor(() => expect(view.queryAllByTestId('method-row')).toHaveLength(2));
    await fireEvent.click(within(view.getAllByTestId('method-row')[0]).getByTestId('method-menu-button'));
    await fireEvent.click(view.getByTestId('menu-register-version'));

    const dialog = within(await view.findByTestId('register-modal'));
    expect(dialog.getByTestId('mode-version')).toHaveAttribute('aria-pressed', 'true');
    expect(dialog.getByTestId('register-module')).toHaveValue('tp132');
    expect(dialog.getByTestId('register-method')).toHaveValue('plot_tf_activity');
    expect(dialog.getByTestId('register-now-next')).toHaveTextContent(/now\s*version\s*v2\s*next\s*version\s*v3/);
  });
});

describe('Register modal, check then confirm', () => {
  it('a refused check shows each refusal in red with what wfc expects, and Check again', async () => {
    dryAnswer = () => response({ ok: false, preChecks: [...PASSING, {
      status: 'fail', label: 'registration plan',
      detail: 'Cannot register from src/x: it holds more than one script (a.py, b.py) and method.yaml '
            + 'does not say which is the main one. Add `script: a.py` to method.yaml.' }] });
    const { view } = modal();
    await fillVersion(view);
    await fireEvent.click(view.getByTestId('register-primary'));

    const refusal = await view.findByTestId('register-refusal');
    expect(refusal).toHaveTextContent('more than one script');
    expect(refusal.querySelector('code')).toHaveTextContent('script: a.py');
    expect(view.getByTestId('register-primary')).toHaveTextContent('Check again');
    expect(dryRuns[0]).toEqual({ directory: 'src/tp132/plot_tf_activity', module: 'tp132',
                                 method_name: 'plot_tf_activity' });

    await fireEvent.click(view.getByTestId('register-back'));
    expect(view.getByTestId('register-folder')).toHaveValue('src/tp132/plot_tf_activity');
    expect(view.getByTestId('register-method')).toHaveValue('plot_tf_activity');
    expect(writes).toEqual([]);
  });

  it('a clean check shows the confirm card, and Confirm applies the checked plan', async () => {
    const { view, onClose, onRegistered } = modal();
    await fillVersion(view);
    await fireEvent.click(view.getByTestId('register-primary'));

    const card = within(await view.findByTestId('confirm-card'));
    expect(view.getByTestId('register-primary')).toHaveTextContent('Confirm: register v3');
    const part = (p: string) => card.getAllByTestId('confirm-row').find(r => r.dataset.part === p)!;
    expect(within(part('code')).getByTestId('confirm-count')).toHaveTextContent('1 changed · 1 added');
    expect(within(part('slots')).getByTestId('confirm-count')).toHaveTextContent('1 changed · 1 added');
    expect(within(part('params')).getByTestId('confirm-count')).toHaveTextContent('2 changed · 1 removed');
    expect(within(part('env')).getByTestId('confirm-count')).toHaveTextContent('unchanged');
    expect(card.queryAllByTestId('confirm-row').find(r => r.dataset.part === 'run')).toBeUndefined();

    await fireEvent.click(within(part('params')).getByTestId('confirm-count'));
    const table = within(part('params')).getByTestId('confirm-table');
    expect(table).toHaveTextContent('Before');
    expect(table).toHaveTextContent('After');
    const entry = (name: string) => within(table).getAllByTestId('confirm-entry').find(e => e.dataset.name === name)!;
    expect(entry('top').dataset.change).toBe('changed');
    const cells = within(table).getAllByTestId('ba-cell');
    const changedDefaults = cells.filter(c => c.dataset.field === 'default' && c.dataset.changed === 'true');
    // top's default changed (30 → 20), outlined on both sides; n_bins' did not.
    expect(changedDefaults.map(c => c.textContent?.trim())).toEqual(['30', '20']);
    expect(cells.filter(c => c.dataset.field === 'type' && c.className.includes('dimc')).length).toBeGreaterThan(0);
    // A removed entry is blank under After; its long default stays in its cell.
    expect(table).toHaveTextContent(`'${LONG}'`);

    const more = within(table).getAllByTestId('more-toggle')[0];
    expect(more).toHaveTextContent('constraints');
    await fireEvent.click(more);
    const diff = within(table).getByTestId('more-diff');
    expect(within(diff).getByTestId('more-before').querySelector('.del')).toHaveTextContent('"max": 100');
    expect(within(diff).getByTestId('more-after').querySelector('.ins')).toHaveTextContent('"max": 500');

    expect(card.getByTestId('holds-script')).toHaveTextContent('plot_tf_activity.py');
    expect(card.getByTestId('holds-helpers')).toHaveTextContent('utils.py');
    expect(card.getByTestId('holds-ignored')).toHaveTextContent('notes.md, test.csv');

    await fireEvent.click(view.getByTestId('register-primary'));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({ directory: 'src/tp132/plot_tf_activity', module: 'tp132',
                                method_name: 'plot_tf_activity', plan_token: 't-changed' });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onRegistered).toHaveBeenCalled();
  });

  it('a folder changed since the check is checked again, not registered', async () => {
    const { view } = modal();
    await fillVersion(view);
    await fireEvent.click(view.getByTestId('register-primary'));
    await view.findByTestId('confirm-card');
    writeAnswer = () => response({ detail: 'its source changed' }, 409);
    await fireEvent.click(view.getByTestId('register-primary'));

    await waitFor(() => expect(dryRuns).toHaveLength(2));
    expect(await view.findByTestId('register-notice')).toHaveTextContent('checked again');
    await view.findByTestId('confirm-card');
  });

  it('the same content reads nothing to register, with only Close and ← Back', async () => {
    dryAnswer = () => response({ ok: true, preChecks: PASSING, plan: SAME_PLAN });
    const { view, onClose } = modal();
    await fillVersion(view);
    await fireEvent.click(view.getByTestId('register-primary'));

    expect(await view.findByTestId('register-same')).toHaveTextContent('Same as v2nothing to register');
    expect(view.getByTestId('register-primary')).toHaveTextContent('Close');
    expect(view.queryByRole('button', { name: 'Cancel' })).toBeNull();
    await fireEvent.click(view.getByTestId('register-back'));
    expect(view.getByTestId('register-folder')).toHaveValue('src/tp132/plot_tf_activity');

    await fireEvent.click(view.getByTestId('register-primary'));
    await view.findByTestId('register-same');
    await fireEvent.click(view.getByTestId('register-primary'));
    expect(onClose).toHaveBeenCalled();
    expect(writes).toEqual([]);
  });

  it('a newest version git no longer holds says so, and claims no slot or param unchanged', async () => {
    const lost = { ...CHANGED_PLAN, changes: { ...CHANGED_PLAN.changes, code: null, slots: [], params: [] } };
    dryAnswer = () => response({ ok: true, preChecks: PASSING, plan: lost });
    const { view } = modal();
    await fillVersion(view);
    await fireEvent.click(view.getByTestId('register-primary'));

    const card = await view.findByTestId('confirm-card');
    const parts = within(card).getAllByTestId('confirm-row').map(r => r.dataset.part);
    expect(parts).not.toContain('slots');
    expect(parts).not.toContain('params');
    expect(within(card).getAllByTestId('confirm-row').find(r => r.dataset.part === 'code'))
      .toHaveTextContent("v2's files are no longer in git, so what changed in its code and contract cannot be named");
    expect(view.getByTestId('register-primary')).toHaveTextContent('Confirm: register v3');
  });

  it('a new method shows the Registering card with no Before / After, and Confirm: register v1', async () => {
    dryAnswer = () => response({ ok: true, preChecks: PASSING, plan: NEW_PLAN });
    const { view } = modal();
    await fireEvent.input(view.getByTestId('register-folder'), { target: { value: 'src/tp132/volcano_plot' } });
    await choose(view.getByTestId('register-module'), 'tp132');
    await fireEvent.click(view.getByTestId('register-primary'));

    const card = within(await view.findByTestId('confirm-card'));
    expect(view.getByTestId('confirm-card').dataset.outcome).toBe('new');
    expect(view.getByTestId('confirm-card')).toHaveTextContent('Registering');
    expect(view.getByTestId('confirm-card')).not.toHaveTextContent('Before');
    const part = (p: string) => card.getAllByTestId('confirm-row').find(r => r.dataset.part === p)!;
    expect(within(part('slots')).getByTestId('confirm-count')).toHaveTextContent('1 in · 1 out');
    expect(within(part('params')).getByTestId('confirm-count')).toHaveTextContent('1 param');
    // Open by default.
    expect(within(part('slots')).getByTestId('confirm-table')).toHaveTextContent('deseq2_results');
    expect(within(part('params')).getByTestId('confirm-table')).toHaveTextContent('0.05');
    expect(card.getByTestId('confirm-run')).toHaveTextContent('volcano_plot.py · python · gpus False');
    expect(card.getByTestId('holds-helpers')).toHaveTextContent('none');
    expect(view.getByTestId('register-primary')).toHaveTextContent('Confirm: register v1');
    expect(dryRuns[0]).toEqual({ directory: 'src/tp132/volcano_plot', module: 'tp132', method_name: null });
  });

  it('× closes the modal from the confirm state with nothing registered', async () => {
    const { view, onClose } = modal();
    await fillVersion(view);
    await fireEvent.click(view.getByTestId('register-primary'));
    await view.findByTestId('confirm-card');
    await fireEvent.click(view.getByTestId('register-close'));
    expect(onClose).toHaveBeenCalled();
    expect(writes).toEqual([]);
  });
});
