/**
 * Component-mounting Vitest tests for the Registry's Drafts group.
 *
 * The real RegistryTab over a stubbed `fetch` in task 1's draft shapes: the
 * Methods tab ends with the Drafts group, sub-grouped by module; a draft row
 * reads like a method row with the grey `version | DRAFT | …` chip, Update
 * and ⋯; an open draft shows its card, the Registration check and the
 * Parameters | Draft run tabs; a link for `versionIdentity: 'draft'` opens
 * the draft's Draft run tab; Open in History ↗ and a node line hand the
 * draft run to History by draft string; "+ Draft from folder…" dry-runs the
 * folder.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import RegistryTab from '../RegistryTab.svelte';
import { openDraftInRegistry } from '../../shared/draftLinks.js';
import { clipMiddle } from '../draftFormat.js';
import { get } from 'svelte/store';
import { tabRequest } from '../../shared/uiState.js';
import {
  expandedPipelineIds, highlightedRunId, historyView, selectRun, selectedRunId,
} from '../../history/historyStore.js';

const refreshPalette = vi.fn(async () => {});
vi.mock('../../builder/api.js', () => ({ refreshPalette: () => refreshPalette() }));
vi.mock('../../shared/shikiHighlight.js', () => ({
  highlight: async (content: string) => `<pre class="shiki"><code>${content}</code></pre>`,
}));

const FOLDER = 'C:\\Users\\dap182\\Documents\\projects\\lab-analysis-2026\\src\\__demo__\\__demo__label';

function check(ok: boolean) {
  return ok
    ? { ok: true, lines: [{ ok: true, text: '`method.yaml` read · script `label.py` found' }] }
    : { ok: false, lines: [
        { ok: true, text: '`method.yaml` read · script `qc_report.py` found' },
        { ok: false, text: 'env `wfc-qc-env` is not registered: register it with `wfc register-env`' },
      ] };
}

const LABEL = {
  module: '__demo__', method: '__demo__label', kind: 'version', folder: FOLDER, env: 'wfc-demo-env',
  newestLabel: 3, registersAs: 4, filesUpdated: true, stale: true, valid: true, reasons: [],
  contract: { inputs: { data: { type: '.csv' } }, outputs: { labeled: { type: '.csv' } },
              params_schema: { k: { type: 'int', default: 3 } }, executor: 'python' },
  check: check(true),
  draftRun: { startedAt: '2026-11-04T14:31:00Z', pipelineId: 'p_demo', pipelineName: 'demo', status: 'running', running: true },
};
const QC = {
  module: '__demo__', method: 'qc_report', kind: 'method', folder: 'src/__demo__/qc_report', env: 'wfc-qc-env',
  newestLabel: null, registersAs: 1, filesUpdated: false, stale: false, valid: false,
  reasons: ['env wfc-qc-env is not registered'], contract: {}, check: check(false), draftRun: null,
};
/** One step of a Draft run node, as `draft_run_detail` spells it. */
function step(node: string, sample: string, status: string, sizeBytes: number | null = null) {
  return { sample, status, sizeBytes, draftString: `draft:__demo__.__demo__label/${node}@${sample}` };
}

// A draft run still running, in `draft_run_detail`'s shape: every node lists
// one step per sample, the ones that have not run yet as pending. Sample s1
// has not run anywhere; n3 has run nowhere.
const RUN = {
  module: '__demo__', method: '__demo__label', startedAt: new Date(Date.now() - 120_000).toISOString(),
  pipelineId: 'p_demo', pipelineName: 'demo', samples: ['s1', 's2', 's3'], status: 'running',
  tally: { pending: 5, success: 2, failed: 1, running: 1 }, totalBytes: 2516583,
  keptUntil: "__demo__label's draft runs again",
  nodes: [
    { nodeId: 'n1', module: '__demo__', method: '__demo__label', isDraft: true, status: 'pending',
      sizeBytes: 1153434,
      steps: [step('n1', 's1', 'pending'), step('n1', 's2', 'success', 1153434), step('n1', 's3', 'failed')] },
    { nodeId: 'n2', module: '__demo__', method: '__demo__summarize', isDraft: false, status: 'running',
      sizeBytes: 1363149,
      steps: [step('n2', 's1', 'pending'), step('n2', 's2', 'success', 1363149), step('n2', 's3', 'running')] },
    { nodeId: 'n3', module: '__demo__', method: '__demo__plot', isDraft: false, status: 'pending',
      sizeBytes: 0,
      steps: [step('n3', 's1', 'pending'), step('n3', 's2', 'pending'), step('n3', 's3', 'pending')] },
  ],
};

const METHODS = [
  { name: '__demo__label', module: '__demo__', env: 'wfc-demo-env', runCount: 0, source: null,
    versions: [{ label: 3, identity: 'a'.repeat(64), registered_at: null, env: 'wfc-demo-env', newest: true,
                 runCount: 0, commit: null, envFingerprint: '', image: '', codeTree: '' }] },
];

const calls: Array<{ method: string; url: string; body: unknown }> = [];

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response;
}

beforeEach(() => {
  calls.length = 0;
  refreshPalette.mockClear();
  // Delete outputs' dry run: the server's plan and its token (`DraftDeletePlan`).
  const PLAN = {
    module: '__demo__', method: '__demo__label', outputsOnly: true, folder: '.wfc/drafts/__demo__/__demo__label',
    source: 'src/__demo__/__demo__label', draftRun: 'the draft run of 2026-11-04 14:31', laterDrafts: [], pipelines: [],
    token: 'tok-1',
    deletes: ['the draft run of 2026-11-04 14:31 and its results (2.4 MB, 3 steps)',
              'draft results shown in pipelines: demo'],
    keeps: ['the draft itself (its script and method.yaml) — run it again any time',
            'registered versions v1..v3 and their recorded runs',
            'your method folder src/__demo__/__demo__label'],
    hint: "Want wfc to forget this draft's script and contract too? Use Remove draft instead.",
    message: 'Delete the outputs of __demo__/__demo__label\nDeletes:\n  - the draft run of 2026-11-04 14:31 ...',
  };
  // Remove draft's dry run.
  const REMOVE_PLAN = {
    ...PLAN, outputsOnly: false, token: 'tok-2',
    deletes: ['the draft and everything wfc keeps for it: its link to your method folder, its draft-run record of 2026-11-04 14:31 and its outputs (2.4 MB)',
              "draft results shown in pipelines: demo (they read 'draft removed')"],
    keeps: ['your method folder and every file the draft used src/__demo__/__demo__label (never touched — start a draft from it again any time)',
            'registered versions and their recorded runs'],
    hint: 'Only want to free the results? Use Delete outputs.',
    message: 'Remove the draft of __demo__/__demo__label\nDeletes:\n  - the draft and ...',
  };
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url === '/api/registry/drafts') {
      if (method === 'POST') return json({ ok: true, module: '__demo__', method: 'x', kind: 'method', message: 'never', registersAs: 1 });
      return json({ drafts: [LABEL, QC] });
    }
    if (url === '/api/registry/drafts?dryRun=true') {
      return json({ ok: true, module: '__demo__', method: '__demo__label', kind: 'version', newestLabel: 3, registersAs: 4,
                    message: 'Module __demo__ already has a method called __demo__label (newest version v3). This folder becomes its DRAFT version; registering it adds v4.' });
    }
    if (url.endsWith('/__demo__label/update')) return json({ draft: { ...LABEL, stale: false }, changed: true, message: 'The contract changed: 1 param added.' });
    if (url.endsWith('/__demo__label/draft-run?dryRun=true') && method === 'DELETE') {
      return json({ ok: true, message: PLAN.message, plan: PLAN });
    }
    if (url.endsWith('/__demo__label?dryRun=true') && method === 'DELETE') {
      return json({ ok: true, message: REMOVE_PLAN.message, plan: REMOVE_PLAN });
    }
    if (url.endsWith('/__demo__label/draft-run')) return json(RUN);
    if (url.endsWith('/qc_report/draft-run')) return json({ detail: 'qc_report has no draft run yet.' }, 404);
    if (url.includes('/api/registry/drafts/') && url.endsWith('/detail')) {
      return json({ draft: LABEL, files: [{ name: 'label.py', language: 'python', content: 'print(1)' }], contract: LABEL.contract });
    }
    if (url === '/api/registry/methods') return json({ methods: METHODS });
    if (url === '/api/registry/modules') return json({ modules: [{ name: '__demo__', description: '', contracts: [], methods: 1, source: '', color: '#6e5cd6' }] });
    if (url === '/api/registry/samples') return json({ samples: [] });
    return json({});
  }) as unknown as typeof fetch;
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn(async () => {}) }, configurable: true });
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function methodsView() {
  const view = render(RegistryTab, { props: { visible: true } });
  await fireEvent.click(await view.findByRole('button', { name: /^Methods/ }));
  await waitFor(() => expect(view.queryAllByTestId('draft-row')).toHaveLength(2));
  return view;
}

describe('Registry Drafts group', () => {
  it('ends the Methods tab, sub-grouped by module; draft rows read like method rows with the grey DRAFT chip, Update and ⋯', async () => {
    const view = await methodsView();
    const list = view.container.querySelector('.methods-list')!;
    expect(list.lastElementChild?.getAttribute('data-testid')).toBe('drafts-group');
    const group = view.getByTestId('drafts-group');
    expect(within(group).getByTestId('drafts-count')).toHaveTextContent('2 · each runs its folder as it is now');
    expect(within(group).getAllByTestId('draft-module-group').map(g => g.dataset.module)).toEqual(['__demo__']);
    const [label, qc] = view.getAllByTestId('draft-row');
    expect(within(label).getByTestId('draft-version-chip')).toHaveTextContent(/^versionDRAFTversion$/);
    expect(within(qc).getByTestId('draft-version-chip')).toHaveTextContent(/^versionDRAFTmethod$/);
    expect(within(label).getByTestId('draft-update')).toBeInTheDocument();
    expect(within(label).queryByTestId('draft-invalid')).toBeNull();
    expect(within(qc).getByTestId('draft-invalid')).toHaveAttribute('title', expect.stringContaining('env wfc-qc-env is not registered'));
    // The ⋯ menu: Copy path, Refresh from disk, Remove draft.
    await fireEvent.click(within(label).getByTestId('draft-menu-button'));
    const menu = await view.findByTestId('draft-menu');
    expect(within(menu).getByTestId('menu-copy-path')).toHaveTextContent('Copy path');
    expect(within(menu).getByTestId('menu-refresh-draft')).toHaveTextContent('Refresh from disk');
    expect(within(menu).getByTestId('menu-remove-draft')).toHaveTextContent('Remove draft');
    // Escape closes it while ⋯ still has the focus, and does not open the row.
    await fireEvent.keyDown(within(label).getByTestId('draft-menu-button'), { key: 'Escape' });
    expect(view.queryByTestId('draft-menu')).toBeNull();
    expect(view.queryByTestId('draft-panel')).toBeNull();
  });

  it("Remove draft's confirmation lists the plan's Deletes and Keeps and its hint; red is only on the Remove button", async () => {
    const view = await methodsView();
    const [label] = view.getAllByTestId('draft-row');
    await fireEvent.click(within(label).getByTestId('draft-menu-button'));
    await fireEvent.click(within(await view.findByTestId('draft-menu')).getByTestId('menu-remove-draft'));
    const plan = await view.findByTestId('draft-confirm-plan');
    expect([...within(plan).getByTestId('draft-confirm-deletes').querySelectorAll('li')].map(li => li.textContent)).toEqual([
      'the draft and everything wfc keeps for it: its link to your method folder, its draft-run record of 2026-11-04 14:31 and its outputs (2.4 MB)',
      "draft results shown in pipelines: demo (they read 'draft removed')",
    ]);
    const keeps = within(plan).getByTestId('draft-confirm-keeps');
    expect([...keeps.querySelectorAll('li')].map(li => li.textContent)).toEqual([
      'your method folder and every file the draft used src/__demo__/__demo__label (never touched — start a draft from it again any time)',
      'registered versions and their recorded runs',
    ]);
    // Keeps reads neutral: no check marks beside what stays.
    expect(keeps).not.toHaveTextContent('✓');
    expect(within(plan).getByTestId('draft-confirm-hint')).toHaveTextContent('Only want to free the results? Use Delete outputs.');
    const dialog = view.getByTestId('draft-confirm');
    expect([...dialog.querySelectorAll('.danger')]).toEqual([view.getByTestId('draft-confirm-action')]);
    expect(view.getByTestId('draft-confirm-action')).toHaveTextContent('Remove draft');
    expect(calls.filter(c => c.method === 'DELETE').map(c => c.url))
      .toEqual(['/api/registry/drafts/__demo__/__demo__label?dryRun=true']);
  });

  it('opens to the draft card, the Registration check and Parameters | Draft run; a failed check disables Register', async () => {
    const view = await methodsView();
    const [label, qc] = view.getAllByTestId('draft-row');
    await fireEvent.click(label);
    let panel = await view.findByTestId('draft-panel');
    const card = within(panel).getByTestId('draft-card');
    expect(card).toHaveTextContent('DRAFT VERSION OF __demo__label');
    expect(card).toHaveTextContent('registers as v4');
    expect(within(card).getByTestId('draft-files-updated')).toHaveTextContent('Files updated since v3.');
    // A changed method.yaml is read again by the next Run or Validate: no refusal.
    expect(within(card).getByTestId('draft-stale')).toHaveTextContent(
      'method.yaml changed since it was last read. The next Run or Validate reads it again; Update checks it now without running.');
    expect(within(card).getByTestId('draft-stale')).not.toHaveTextContent(/refuse|press update/i);
    expect(within(card).getByTestId('draft-path')).toHaveTextContent('C:\\…\\src\\__demo__\\__demo__label');
    expect(within(card).getByTestId('draft-path')).toHaveAttribute('title', FOLDER);
    expect(within(card).getByTestId('draft-register')).toBeEnabled();
    expect(within(panel).getByTestId('draft-check-summary')).toHaveTextContent('✓ 1 ok · nothing written');
    expect(within(panel).getByTestId('draft-tab-parameters')).toHaveAttribute('aria-selected', 'true');
    await fireEvent.click(label);

    await fireEvent.click(qc);
    panel = await view.findByTestId('draft-panel');
    expect(within(panel).getByTestId('draft-card')).toHaveTextContent('DRAFT METHOD');
    expect(within(panel).getByTestId('draft-register')).toBeDisabled();
    expect(within(panel).getByTestId('draft-check-summary')).toHaveTextContent('✓ 1 ok · ✗ 1 failed · nothing written');
    // A failed check starts open, its lines ✓ / ✗.
    expect(within(panel).getAllByTestId('draft-check-line').map(l => l.dataset.ok)).toEqual(['true', 'false']);
    await fireEvent.click(within(panel).getByTestId('draft-tab-run'));
    expect(await within(panel).findByTestId('draft-run-none')).toHaveTextContent('qc_report has no draft run yet.');
    // Nothing was written by opening and checking.
    expect(calls.filter(c => c.method !== 'GET')).toEqual([]);
  });

  it('a link for the draft opens its row on the Draft run tab: status, size, node lines and actions', async () => {
    const view = await methodsView();
    openDraftInRegistry('__demo__', '__demo__label');
    expect(await view.findByTestId('draft-run-when')).toHaveTextContent('2 min ago · pipeline demo · 3 samples');
    const tab = view.getByTestId('draft-run-tab');
    expect(view.getByTestId('draft-tab-run')).toHaveAttribute('aria-selected', 'true');
    expect(within(tab).getByTestId('draft-run-status')).toHaveTextContent(
      '✓ 2 completed · ✗ 1 failed · ● 1 running · ○ 5 pending · latest kept, not recorded');
    expect(within(tab).getByTestId('draft-run-size')).toHaveTextContent("2.4 MB · kept until __demo__label's draft runs again");
    const nodes = within(tab).getAllByTestId('draft-run-node');
    expect(nodes.map(n => n.dataset.node)).toEqual(['n1', 'n2', 'n3']);
    expect(nodes[0]).toHaveTextContent('DRAFT');
    // The copy holds the node's draft strings, one per sample.
    expect(within(nodes[0]).getByTestId('draft-string-copy')).toHaveAttribute('data-copy',
      ['s1', 's2', 's3'].map(s => `draft:__demo__.__demo__label/n1@${s}`).join('\n'));
    expect(within(tab).getByTestId('draft-run-zip')).toHaveAttribute('href', '/api/registry/drafts/__demo__/__demo__label/draft-run/zip');
    for (const id of ['draft-open-history', 'draft-view-pipeline', 'draft-delete-outputs']) {
      expect(within(tab).getByTestId(id)).toBeInTheDocument();
    }
    // Delete outputs asks the server for its plan, lists its Deletes and Keeps
    // lines and its hint as the server words them, and sends its token back.
    await fireEvent.click(within(tab).getByTestId('draft-delete-outputs'));
    const plan = await view.findByTestId('draft-confirm-plan');
    expect(within(plan).getAllByRole('listitem').map(li => li.textContent)).toEqual([
      'the draft run of 2026-11-04 14:31 and its results (2.4 MB, 3 steps)',
      'draft results shown in pipelines: demo',
      'the draft itself (its script and method.yaml) — run it again any time',
      'registered versions v1..v3 and their recorded runs',
      'your method folder src/__demo__/__demo__label',
    ]);
    expect(within(plan).getByTestId('draft-confirm-deletes').querySelectorAll('li')).toHaveLength(2);
    expect(within(plan).getByTestId('draft-confirm-keeps').querySelectorAll('li')).toHaveLength(3);
    expect(within(plan).getByTestId('draft-confirm-hint')).toHaveTextContent(
      "Want wfc to forget this draft's script and contract too? Use Remove draft instead.");
    expect(calls.filter(c => c.method === 'DELETE').map(c => c.url))
      .toEqual(['/api/registry/drafts/__demo__/__demo__label/draft-run?dryRun=true']);
    await fireEvent.click(await view.findByTestId('draft-confirm-action'));
    await waitFor(() => expect(calls.some(c => c.method === 'DELETE'
      && c.url === `/api/registry/drafts/__demo__/__demo__label/draft-run?planned=tok-1`)).toBe(true));
  });

  it("a plan that deletes nothing shows the plan's message alone, with no Deletes or Keeps", async () => {
    // The draft run went after the tab loaded (say `wfc delete-draft` in a
    // terminal): Delete outputs' plan is Execution's nothing-to-delete plan.
    const message = '__demo__.__demo__label has no draft run: nothing is deleted. The draft is kept.';
    const empty = {
      module: '__demo__', method: '__demo__label', outputsOnly: true, folder: '.wfc/drafts/__demo__/__demo__label',
      source: 'src/__demo__/__demo__label', draftRun: null, laterDrafts: [], pipelines: [], token: 'tok-3',
      deletes: [], keeps: [], hint: '', message,
    };
    const served = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.endsWith('/__demo__label/draft-run?dryRun=true') && init?.method === 'DELETE') {
        calls.push({ method: 'DELETE', url, body: null });
        return json({ ok: true, message, plan: empty });
      }
      return served(input, init);
    }) as unknown as typeof fetch;
    const view = await methodsView();
    openDraftInRegistry('__demo__', '__demo__label');
    await fireEvent.click(await view.findByTestId('draft-delete-outputs'));
    const plan = await view.findByTestId('draft-confirm-plan');
    expect(plan.querySelector('.confirm-message')!.textContent).toBe(message);
    expect(within(plan).queryByTestId('draft-confirm-deletes')).toBeNull();
    expect(within(plan).queryByTestId('draft-confirm-keeps')).toBeNull();
    expect(within(plan).queryByTestId('draft-confirm-hint')).toBeNull();
    expect(plan.querySelectorAll('li')).toHaveLength(0);
  });

  it('Open in History ↗ highlights the draft run in History; a node line opens that draft step in History', async () => {
    historyView.set('descendants');
    expandedPipelineIds.set(new Set());
    highlightedRunId.set(null);
    selectRun(null);
    tabRequest.set(null);
    const view = await methodsView();
    openDraftInRegistry('__demo__', '__demo__label');
    await view.findByTestId('draft-run-when');
    const tab = view.getByTestId('draft-run-tab');

    // Open in History ↗: History's Pipelines view, the pipeline open and the
    // draft's own first step that has run highlighted, named by its draft
    // string (its s1 step is still pending); no panel.
    await fireEvent.click(within(tab).getByTestId('draft-open-history'));
    expect(get(tabRequest)).toBe('history');
    expect(get(historyView)).toBe('pipelines');
    expect(get(expandedPipelineIds).has('p_demo')).toBe(true);
    expect(get(highlightedRunId)).toBe('draft:__demo__.__demo__label/n1@s2');
    expect(get(selectedRunId)).toBeNull();

    // A node line opens that node's first step that has run: highlighted and
    // its run panel open.
    tabRequest.set(null);
    historyView.set('lineages');
    const nodes = within(tab).getAllByTestId('draft-run-node');
    await fireEvent.click(nodes[1]);
    expect(get(tabRequest)).toBe('history');
    expect(get(historyView)).toBe('pipelines');
    expect(get(highlightedRunId)).toBe('draft:__demo__.__demo__label/n2@s2');
    expect(get(selectedRunId)).toBe('draft:__demo__.__demo__label/n2@s2');

    // A node none of whose steps has run opens the draft run as Open in History does.
    selectRun(null);
    await fireEvent.keyDown(nodes[2], { key: 'Enter' });
    expect(get(highlightedRunId)).toBe('draft:__demo__.__demo__label/n1@s2');
    expect(get(selectedRunId)).toBeNull();

    // View pipeline ↗ only opens the pipeline's row.
    highlightedRunId.set(null);
    expandedPipelineIds.set(new Set());
    tabRequest.set(null);
    await fireEvent.click(within(tab).getByTestId('draft-view-pipeline'));
    expect(get(tabRequest)).toBe('history');
    expect(get(expandedPipelineIds).has('p_demo')).toBe(true);
    expect(get(highlightedRunId)).toBeNull();
    expect(get(selectedRunId)).toBeNull();
  });

  it('Update reads method.yaml again, says what changed and refetches the Builder palette', async () => {
    const view = await methodsView();
    await fireEvent.click(within(view.getAllByTestId('draft-row')[0]).getByTestId('draft-update'));
    expect(await view.findByTestId('draft-notice')).toHaveTextContent('The contract changed: 1 param added.');
    expect(calls.some(c => c.method === 'POST' && c.url === '/api/registry/drafts/__demo__/__demo__label/update')).toBe(true);
    expect(refreshPalette).toHaveBeenCalled();
  });

  it('+ Draft from folder… asks for the module first, then states what the folder becomes', async () => {
    const view = await methodsView();
    await fireEvent.click(view.getByTestId('draft-from-folder'));
    const dialog = await view.findByTestId('draft-folder-dialog');
    expect(within(dialog).getByTestId('draft-folder')).toBeDisabled();
    await fireEvent.change(within(dialog).getByTestId('draft-module'), { target: { value: '__demo__' } });
    const folder = within(dialog).getByTestId('draft-folder');
    expect(folder).toBeEnabled();
    await fireEvent.input(folder, { target: { value: 'src/__demo__/__demo__label' } });
    await fireEvent.change(folder);
    expect(await within(dialog).findByTestId('draft-kind-text')).toHaveTextContent(
      'This folder becomes its DRAFT version; registering it adds v4.');
    expect(calls.find(c => c.url === '/api/registry/drafts?dryRun=true')?.body)
      .toEqual({ module: '__demo__', folder: 'src/__demo__/__demo__label' });
    expect(within(dialog).getByTestId('draft-create')).toBeEnabled();
  });
});

describe('clipMiddle', () => {
  it('keeps the first part and as many last parts as fit', () => {
    expect(clipMiddle(FOLDER)).toBe('C:\\…\\src\\__demo__\\__demo__label');
    expect(clipMiddle('src/__demo__/qc_report')).toBe('src/__demo__/qc_report');
    expect(clipMiddle('/home/me/a/very/long/path/to/the/method/folder', 30)).toBe('/home/…/to/the/method/folder');
  });
});
