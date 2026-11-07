<script lang="ts">
  /**
   * Registry tab — Modules, Methods, Envs and Samples sub-tabs over the
   * /api/registry/* endpoints, plus the Register modal. Styling follows the
   * Sidebar/Toolbar aesthetic.
   *
   * Endpoints used here:
   *   GET  /api/registry/modules
   *   GET  /api/registry/methods
   *   GET  /api/registry/samples
   *   GET  /api/registry/methods/:module/:method/detail
   *   GET  /api/registry/methods/:module/:method/export (a version's zip)
   *
   * Methods are grouped under collapsible module headings, open by default.
   * A row reads its method, newest version and env as labelled tags, with a
   * ⋯ menu; an open method's row holds its version picker, and the method
   * shows that version's contract and code (`RegistryMethodPanel.svelte`). Version details float over the row
   * (`VersionDetailsCard.svelte`). Refresh reloads the lists and drops every
   * cached detail, so open rows fetch theirs again.
   *
   * The Envs sub-tab (`EnvsSubTab.svelte`) fetches its own env list and
   * package manifests.
   */

  import RegisterModal from './RegisterModal.svelte';
  import EnvsSubTab from './EnvsSubTab.svelte';
  import MethodListRow from './MethodListRow.svelte';
  import ModuleListRow from './ModuleListRow.svelte';
  import ModulePanel from './ModulePanel.svelte';
  import EnvCliPointer from './EnvCliPointer.svelte';
  import RegistryMethodPanel from './RegistryMethodPanel.svelte';
  import VersionDetailsCard from './VersionDetailsCard.svelte';
  import PopMenu from './PopMenu.svelte';
  import type { MenuItem } from './PopMenu.svelte';
  import LabelTag from '../shared/LabelTag.svelte';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import { tick } from 'svelte';
  import { registryFocus } from './registryFocus.js';
  import type { RegistryFocus } from './registryFocus.js';
  import DraftListRow from './DraftListRow.svelte';
  import DraftPanel from './DraftPanel.svelte';
  import DraftFromFolderDialog from './DraftFromFolderDialog.svelte';
  import type { DraftTab } from './draftFormat.js';
  import { draftRunRef, nodeDraftRef } from './draftFormat.js';
  import { DRAFT_FOCUS_IDENTITY } from '../shared/draftLinks.js';
  import { tabRequest } from '../shared/uiState.js';
  import { refreshPalette } from '../builder/api.js';
  import { expandedPipelineIds, historyView, jumpToPipelineRun, selectRun } from '../history/historyStore.js';
  import {
    deleteDraftRun,
    planDraftDelete,
    type DraftDeletePlan,
    fetchDraftCheck,
    fetchDraftDetail,
    fetchDraftRun,
    fetchDrafts,
    fetchMethodDetail,
    fetchRegistryModules,
    fetchRegistryMethods,
    fetchRegistrySamples,
    methodExportUrl,
    removeDraft,
    updateDraft,
  } from './api.js';
  import type {
    DraftDetail,
    DraftEntry,
    DraftRunDetail,
    DraftRunNode,
    MethodDetail,
    ModuleRow,
    MethodRow,
    MethodVersionRow,
    SampleRow,
    RegisterKind,
  } from './api.js';

  let { visible = true }: { visible?: boolean } = $props();

  type SubTab = 'modules' | 'methods' | 'samples' | 'envs';
  let sub = $state<SubTab>('modules');
  let query = $state('');

  // Method-row expansion state. The response shapes are `api.ts`'s. A row
  // shows one version at a time: the detail and error maps are keyed per
  // version (`detailKey`), so switching versions never shows one version's
  // files under another.
  let expandedMethods = $state<Set<string>>(new Set());
  let selectedVersion = $state<Record<string, string>>({}); // key: methodKey -> identity
  let methodDetails = $state<Record<string, MethodDetail>>({});
  let detailError = $state<Record<string, string>>({});
  // Module headings collapsed on this page load; every module starts open.
  let collapsedModules = $state<Set<string>>(new Set());

  function methodKey(mod: string, meth: string): string {
    return `${mod}::${meth}`;
  }

  /** The newest version, or `undefined` for a row listing no versions. */
  function newestVersion(m: MethodRow): MethodVersionRow | undefined {
    const versions = m.versions ?? [];
    return versions.find(v => v.newest) ?? versions[versions.length - 1];
  }

  /** The newest version's identity, or `''` for a row listing no versions. */
  function newestIdentity(m: MethodRow): string {
    return newestVersion(m)?.identity ?? '';
  }

  /** The identity of the version a row shows: the chosen one, else the newest. */
  function shownVersion(m: MethodRow): string {
    return selectedVersion[methodKey(m.module, m.name)] ?? newestIdentity(m);
  }

  /** The per-version cache key of a row's detail. */
  function detailKey(key: string, identity: string): string {
    return identity ? `${key}@${identity}` : key;
  }

  async function toggleExpand(m: MethodRow) {
    const key = methodKey(m.module, m.name);
    const next = new Set(expandedMethods);
    if (next.has(key)) {
      next.delete(key);
      expandedMethods = next;
      return;
    }
    next.add(key);
    expandedMethods = next;
    await loadDetail(m, shownVersion(m));
  }

  /** Show another version in an expanded row: its files, contract and env. */
  async function selectVersion(m: MethodRow, identity: string) {
    selectedVersion = { ...selectedVersion, [methodKey(m.module, m.name)]: identity };
    await loadDetail(m, identity);
  }

  async function loadDetail(m: MethodRow, identity: string) {
    const dkey = detailKey(methodKey(m.module, m.name), identity);
    if (methodDetails[dkey]) return;
    try {
      // The newest version is the detail route's default; an older one is
      // named by its content identity.
      const res = await fetchMethodDetail(
        m.module, m.name, identity && identity !== newestIdentity(m) ? identity : undefined
      );
      if (!res.ok) {
        detailError = { ...detailError, [dkey]: res.error };
        return;
      }
      methodDetails = { ...methodDetails, [dkey]: res.detail };
    } catch (err) {
      detailError = { ...detailError, [dkey]: String(err) };
    }
  }

  // ---- Menus and the details card ----

  type Menu = { key: string; title: string; items: MenuItem[]; anchor: HTMLElement;
                align: 'right' | 'side'; testid: string };
  let menu = $state<Menu | null>(null);
  let details = $state<{ method: MethodRow; version: MethodVersionRow; anchor: HTMLElement } | null>(null);

  function closeMenus() {
    menu = null;
  }

  function rowElement(m: MethodRow): HTMLElement | null {
    return bodyEl?.querySelector(
      `[data-method-key="${CSS.escape(methodKey(m.module, m.name))}"]`) ?? null;
  }

  function openDetails(m: MethodRow, v: MethodVersionRow, fallback: HTMLElement) {
    // The card floats over the method's row; the anchor that opened the
    // menu may be gone (a picker entry closes with its list).
    details = { method: m, version: v, anchor: rowElement(m) ?? fallback };
  }

  function copyText(text: string) {
    void navigator.clipboard?.writeText(text).catch(() => {});
  }

  /** Download a version's script and contract as its zip. */
  function exportVersion(m: MethodRow, v: MethodVersionRow) {
    const a = document.createElement('a');
    a.href = methodExportUrl(m.module, m.name, v.identity);
    a.download = `${m.name}_v${v.label}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function methodMenu(m: MethodRow, anchor: HTMLElement) {
    const key = `method:${methodKey(m.module, m.name)}`;
    if (menu?.key === key) { closeMenus(); return; }
    const v = newestVersion(m);
    const items: MenuItem[] = v ? [
      { label: 'Version details…', icon: 'ⓘ', testid: 'menu-details',
        run: () => openDetails(m, v, anchor) },
      { label: 'Copy version identity', icon: '⧉', hint: `${v.identity.slice(0, 8)}…`,
        testid: 'menu-copy-identity', separated: true, run: () => copyText(v.identity) },
      { label: 'Export script and contract', icon: '⤓', testid: 'menu-export',
        run: () => exportVersion(m, v) },
      { label: 'Register a new version…', icon: '+', testid: 'menu-register-version',
        separated: true, run: () => openRegisterVersion(m) },
    ] : [
      { label: 'Register a new version…', icon: '+', testid: 'menu-register-version',
        run: () => openRegisterVersion(m) },
    ];
    menu = { key, title: v ? `${m.name} · latest v${v.label}` : m.name, items, anchor,
             align: 'right', testid: 'method-menu' };
  }

  function versionMenu(m: MethodRow, v: MethodVersionRow, anchor: HTMLElement) {
    const key = `version:${methodKey(m.module, m.name)}:${v.label}`;
    if (menu?.key === key) { closeMenus(); return; }
    menu = {
      key, title: `${m.name} · v${v.label}`, anchor, align: 'side', testid: 'version-menu',
      items: [
        { label: 'Version details…', icon: 'ⓘ', testid: 'menu-details',
          run: () => openDetails(m, v, anchor) },
        { label: 'Copy version identity', icon: '⧉', hint: `${v.identity.slice(0, 8)}…`,
          testid: 'menu-copy-identity', separated: true, run: () => copyText(v.identity) },
        { label: 'Copy commit', icon: '⧉', hint: v.commit ? v.commit.slice(0, 7) : 'not recorded',
          testid: 'menu-copy-commit', disabled: !v.commit,
          run: () => { if (v.commit) copyText(v.commit); } },
        { label: 'Export script and contract', icon: '⤓', testid: 'menu-export',
          separated: true, run: () => exportVersion(m, v) },
      ],
    };
  }

  // ---- Register modal ----

  let registerKind = $state<RegisterKind | null>(null);
  // Set when the modal opens on New method version with a method chosen.
  let registerTarget = $state<{ module: string; method: string } | null>(null);

  // Map plural sub-tab name -> singular register kind.
  const SUB_TO_KIND: Record<SubTab, RegisterKind | null> = {
    modules: 'module',
    methods: 'method',
    samples: 'sample',
    envs: null, // no env register endpoint: Register opens the CLI pointer
  };

  function openRegister() {
    if (sub === 'envs') { envPointerOpen = true; return; }
    registerTarget = null;
    registerKind = SUB_TO_KIND[sub] ?? 'module';
  }

  function openRegisterVersion(m: MethodRow) {
    registerTarget = { module: m.module, method: m.name };
    registerKind = 'method';
  }

  function onRegistered(_kind: RegisterKind) {
    // Refresh tables so the new row is visible immediately.
    void refresh();
  }

  // ---- Lists ----

  let modules = $state<ModuleRow[]>([]);
  let methods = $state<MethodRow[]>([]);
  let samples = $state<SampleRow[]>([]);
  let loading = $state(false);
  let loadError = $state<string | null>(null);
  // Set once a Refresh the user asked for has finished.
  let refreshedByUser = $state(false);

  async function refresh(byUser = false) {
    loading = true;
    loadError = null;
    if (byUser) refreshedByUser = false;
    closeMenus();
    details = null;
    try {
      const [mods, meths, sams, drs] = await Promise.all([
        fetchRegistryModules(),
        fetchRegistryMethods(),
        fetchRegistrySamples(),
        fetchDrafts(),
      ]);
      modules = mods;
      methods = meths;
      samples = sams;
      drafts = drs;
    } catch (err) {
      loadError = String(err);
    } finally {
      loading = false;
    }
    // Every cached detail may be stale: drop them, and open rows fetch theirs again.
    methodDetails = {};
    detailError = {};
    draftDetails = {};
    draftDetailError = {};
    await Promise.all([
      ...methods
        .filter(m => expandedMethods.has(methodKey(m.module, m.name)))
        .map(m => loadDetail(m, shownVersion(m))),
      ...drafts.filter(d => expandedDrafts.has(draftKey(d))).map(d => openDraft(d)),
    ]);
    if (byUser) refreshedByUser = true;
    // A link from another view waits for the rows it names.
    if (pendingFocus) {
      const focus = pendingFocus;
      pendingFocus = null;
      await applyFocus(focus);
    }
  }

  // Reload when the tab becomes visible.
  $effect(() => {
    if (visible) refresh();
  });

  // A link from another view (the run detail's Cache-key inputs card): taken
  // once, after the next load, so the rows it names are there. App switches
  // to this tab, which reloads; a request arriving while the tab is already
  // shown reloads here.
  let pendingFocus: RegistryFocus | null = null;
  let bodyEl = $state<HTMLDivElement | undefined>();
  // A method row opened on a version it no longer lists says so, by row.
  let versionNote = $state<Record<string, string>>({});

  $effect(() => registryFocus.subscribe(focus => {
    if (!focus) return;
    pendingFocus = focus;
    registryFocus.set(null);
    if (visible && !loading) void refresh();
  }));

  async function applyFocus(focus: RegistryFocus) {
    query = '';
    sub = focus.sub;
    versionNote = {};
    if (focus.sub === 'samples') {
      await tick();
      bodyEl?.scrollTo({ top: 0 });
      return;
    }
    // A link to a method's draft opens its draft row on the Draft run tab.
    if (focus.versionIdentity === DRAFT_FOCUS_IDENTITY) {
      await focusDraft(focus.module, focus.method);
      return;
    }
    const m = methods.find(r => r.module === focus.module && r.name === focus.method);
    if (!m) return;
    const key = methodKey(m.module, m.name);
    const versions = m.versions ?? [];
    const listed = focus.versionIdentity !== null
      && versions.some(v => v.identity === focus.versionIdentity);
    const identity = listed ? focus.versionIdentity! : newestIdentity(m);
    if (!listed && versions.length > 0) {
      const newest = versions.find(v => v.identity === identity);
      const ran = focus.versionLabel ? `version ${focus.versionLabel}` : 'version';
      versionNote = {
        ...versionNote,
        [key]: `The run's ${ran} is not listed; showing v${newest?.label ?? '?'}.`,
      };
    }
    // The link opens the module it lands in.
    if (collapsedModules.has(m.module)) {
      const next = new Set(collapsedModules);
      next.delete(m.module);
      collapsedModules = next;
    }
    selectedVersion = { ...selectedVersion, [key]: identity };
    expandedMethods = new Set(expandedMethods).add(key);
    await loadDetail(m, identity);
    await tick();
    rowElement(m)?.scrollIntoView?.({ block: 'start' });
  }

  function toggleModule(name: string) {
    const next = new Set(collapsedModules);
    if (next.has(name)) next.delete(name); else next.add(name);
    collapsedModules = next;
  }

  function setAllModules(collapsed: boolean) {
    if (sub === 'modules') {
      expandedModuleRows = collapsed ? new Set() : new Set(modules.map(m => m.name));
      if (collapsed) colourOpenFor = null;
      return;
    }
    collapsedModules = collapsed ? new Set(methods.map(m => m.module)) : new Set();
    collapsedDraftModules = collapsed ? new Set(drafts.map(d => d.module)) : new Set();
  }

  // ---- Modules sub-tab ----

  // Module rows open on this page load (every row starts closed), and the
  // module whose Colour picker is open.
  let expandedModuleRows = $state<Set<string>>(new Set());
  let colourOpenFor = $state<string | null>(null);

  function toggleModuleRow(name: string) {
    const next = new Set(expandedModuleRows);
    if (next.has(name)) {
      next.delete(name);
      if (colourOpenFor === name) colourOpenFor = null;
    } else {
      next.add(name);
    }
    expandedModuleRows = next;
  }

  /** Colour opens the module's row with its picker. */
  function openColour(name: string) {
    expandedModuleRows = new Set(expandedModuleRows).add(name);
    colourOpenFor = name;
  }

  /** The methods of one module, for its open row. */
  function methodsOf(name: string): MethodRow[] {
    return methods.filter(m => m.module === name);
  }

  // ---- Drafts ----

  // The Methods tab ends with the Drafts group, sub-grouped by module. A
  // draft row opens to its card, Registration check and Parameters | Draft
  // run tabs; the details are cached per draft until the next refresh.
  let drafts = $state<DraftEntry[]>([]);
  let draftsOpen = $state(true);
  let collapsedDraftModules = $state<Set<string>>(new Set());
  let expandedDrafts = $state<Set<string>>(new Set());
  let draftTab = $state<Record<string, DraftTab>>({});
  let draftDetails = $state<Record<string, DraftDetail>>({});
  let draftDetailError = $state<Record<string, string>>({});
  let draftRuns = $state<Record<string, DraftRunDetail | null>>({});
  let draftRunError = $state<Record<string, string>>({});
  let draftRunLoading = $state<Set<string>>(new Set());
  let updatingDraft = $state<string | null>(null);
  // The last draft action's outcome, by draft (Update's message, a refusal).
  let draftNotice = $state<Record<string, { text: string; bad: boolean }>>({});
  let draftDialogOpen = $state(false);
  let draftConfirm = $state<{ title: string; plan: DraftDeletePlan; action: string; run: () => Promise<void> } | null>(null);
  let registerDraft = $state<DraftEntry | null>(null);

  function draftKey(d: DraftEntry): string {
    return methodKey(d.module, d.method);
  }

  async function loadDraftDetail(d: DraftEntry): Promise<void> {
    const key = draftKey(d);
    if (draftDetails[key]) return;
    const res = await fetchDraftDetail(d.module, d.method).catch(err => ({ ok: false as const, error: String(err) }));
    if (res.ok) draftDetails = { ...draftDetails, [key]: res.value };
    else draftDetailError = { ...draftDetailError, [key]: res.error };
  }

  async function loadDraftRun(d: DraftEntry): Promise<void> {
    const key = draftKey(d);
    draftRunLoading = new Set(draftRunLoading).add(key);
    const res = await fetchDraftRun(d.module, d.method).catch(err => ({ ok: false as const, error: String(err) }));
    const loadingNow = new Set(draftRunLoading);
    loadingNow.delete(key);
    draftRunLoading = loadingNow;
    if (res.ok) {
      draftRuns = { ...draftRuns, [key]: res.value };
      const { [key]: _gone, ...rest } = draftRunError;
      draftRunError = rest;
    } else {
      draftRuns = { ...draftRuns, [key]: null };
      draftRunError = { ...draftRunError, [key]: res.error };
    }
  }

  async function openDraft(d: DraftEntry, tab: DraftTab | null = null): Promise<void> {
    const key = draftKey(d);
    expandedDrafts = new Set(expandedDrafts).add(key);
    if (tab) draftTab = { ...draftTab, [key]: tab };
    const loads: Promise<void>[] = [loadDraftDetail(d)];
    if ((draftTab[key] ?? 'parameters') === 'run') loads.push(loadDraftRun(d));
    await Promise.all(loads);
  }

  async function toggleDraft(d: DraftEntry): Promise<void> {
    const key = draftKey(d);
    if (expandedDrafts.has(key)) {
      const next = new Set(expandedDrafts);
      next.delete(key);
      expandedDrafts = next;
      return;
    }
    await openDraft(d);
  }

  function setDraftTab(d: DraftEntry, tab: DraftTab): void {
    draftTab = { ...draftTab, [draftKey(d)]: tab };
    if (tab === 'run') void loadDraftRun(d);
  }

  function toggleDraftModule(name: string): void {
    const next = new Set(collapsedDraftModules);
    if (next.has(name)) next.delete(name); else next.add(name);
    collapsedDraftModules = next;
  }

  /** Forget one draft's cached detail and run, and fetch them again if it is open. */
  async function reloadOpenDraft(key: string): Promise<void> {
    const { [key]: _d, ...details } = draftDetails;
    draftDetails = details;
    const { [key]: _e, ...errors } = draftDetailError;
    draftDetailError = errors;
    const d = drafts.find(x => draftKey(x) === key);
    if (d && expandedDrafts.has(key)) await openDraft(d);
  }

  /** After a draft is added, updated or removed: the list here and the Builder's palette. */
  async function draftsChanged(key: string | null = null): Promise<void> {
    drafts = await fetchDrafts();
    void refreshPalette();
    if (key) await reloadOpenDraft(key);
  }

  async function runUpdate(d: DraftEntry): Promise<void> {
    const key = draftKey(d);
    updatingDraft = key;
    const res = await updateDraft(d.module, d.method).catch(err => ({ ok: false as const, error: String(err) }));
    updatingDraft = null;
    draftNotice = { ...draftNotice, [key]: res.ok ? { text: res.value.message, bad: false }
                                                  : { text: res.error, bad: true } };
    if (res.ok) {
      drafts = drafts.map(x => (draftKey(x) === key ? res.value.draft : x));
      void refreshPalette();
      await reloadOpenDraft(key);
    }
  }

  /** Refresh from disk: read the folder and its Registration check again; nothing is written. */
  async function refreshFromDisk(d: DraftEntry): Promise<void> {
    const key = draftKey(d);
    const res = await fetchDraftCheck(d.module, d.method).catch(err => ({ ok: false as const, error: String(err) }));
    if (!res.ok) draftNotice = { ...draftNotice, [key]: { text: res.error, bad: true } };
    drafts = await fetchDrafts();
    if (res.ok) {
      drafts = drafts.map(x => (draftKey(x) === key
        ? { ...x, check: res.value, valid: res.value.ok,
            reasons: res.value.lines.filter(l => !l.ok).map(l => l.text) }
        : x));
    }
    void refreshPalette();
    await reloadOpenDraft(key);
  }

  /**
   * Ask the server what Remove draft or Delete outputs would delete, then
   * confirm with its plan; confirming sends the plan's token back.
   */
  async function planAndConfirm(d: DraftEntry, outputsOnly: boolean,
                                confirm: (plan: DraftDeletePlan) => NonNullable<typeof draftConfirm>): Promise<void> {
    const key = draftKey(d);
    const res = await planDraftDelete(d.module, d.method, outputsOnly)
      .catch(err => ({ ok: false as const, error: String(err) }));
    if (!res.ok) { draftNotice = { ...draftNotice, [key]: { text: res.error, bad: true } }; return; }
    draftConfirm = confirm(res.value);
  }

  function askRemove(d: DraftEntry): void {
    void planAndConfirm(d, false, plan => ({
      title: `Remove the draft of ${d.method}?`,
      plan,
      action: 'Remove draft',
      run: async () => {
        const key = draftKey(d);
        const res = await removeDraft(d.module, d.method, plan.token).catch(err => ({ ok: false as const, error: String(err) }));
        if (!res.ok) { draftNotice = { ...draftNotice, [key]: { text: res.error, bad: true } }; return; }
        const next = new Set(expandedDrafts);
        next.delete(key);
        expandedDrafts = next;
        await draftsChanged();
      },
    }));
  }

  function askDeleteOutputs(d: DraftEntry): void {
    void planAndConfirm(d, true, plan => ({
      title: `Delete the outputs of ${d.method}'s draft run?`,
      plan,
      action: 'Delete outputs',
      run: async () => {
        const key = draftKey(d);
        const res = await deleteDraftRun(d.module, d.method, plan.token).catch(err => ({ ok: false as const, error: String(err) }));
        draftNotice = { ...draftNotice, [key]: res.ok ? { text: res.value.message, bad: !res.value.ok }
                                                      : { text: res.error, bad: true } };
        if (res.ok) {
          drafts = await fetchDrafts();
          await loadDraftRun(d);
        }
      },
    }));
  }

  async function confirmDraftAction(): Promise<void> {
    const pending = draftConfirm;
    draftConfirm = null;
    if (pending) await pending.run();
  }

  function draftMenu(d: DraftEntry, anchor: HTMLElement) {
    const key = `draft:${draftKey(d)}`;
    if (menu?.key === key) { closeMenus(); return; }
    menu = {
      key, title: `${d.method} · DRAFT`, anchor, align: 'right', testid: 'draft-menu',
      items: [
        { label: 'Copy path', icon: '⧉', testid: 'menu-copy-path', run: () => copyText(d.folder) },
        { label: 'Refresh from disk', icon: '↻', testid: 'menu-refresh-draft',
          run: () => void refreshFromDisk(d) },
        { label: 'Remove draft', icon: '✕', testid: 'menu-remove-draft', separated: true,
          run: () => askRemove(d) },
      ],
    };
  }

  /** Show a draft run's pipeline in History's Pipelines view, its row open. */
  function showPipelineInHistory(run: DraftRunDetail): void {
    historyView.set('pipelines');
    expandedPipelineIds.update(s => new Set(s).add(run.pipelineId));
    tabRequest.set('history');
  }

  /**
   * Show a draft run in History, naming its steps by draft string: the
   * Pipelines view with the run's pipeline open and its step highlighted.
   * With a node, that node's draft step is highlighted and its run panel
   * opened; a node with no draft step yet shows the draft run as a whole.
   */
  function showDraftRunInHistory(run: DraftRunDetail, node?: DraftRunNode): void {
    const stepRef = node ? nodeDraftRef(node) : null;
    const ref = stepRef ?? draftRunRef(run);
    if (!ref) { showPipelineInHistory(run); return; }
    jumpToPipelineRun(run.pipelineId, ref);
    if (stepRef) selectRun(stepRef);
    tabRequest.set('history');
  }

  /** Open a draft's row on its Draft run tab: a link from the Builder or Inspector. */
  async function focusDraft(module: string, method: string): Promise<void> {
    const d = drafts.find(x => x.module === module && x.method === method);
    if (!d) return;
    draftsOpen = true;
    if (collapsedDraftModules.has(module)) toggleDraftModule(module);
    await openDraft(d, 'run');
    await tick();
    bodyEl?.querySelector(`[data-draft-key="${CSS.escape(draftKey(d))}"]`)?.scrollIntoView?.({ block: 'start' });
  }

  // The search box's text, trimmed and lower-cased: what every sub-tab's
  // filter (drafts here, modules/methods/samples below) matches against.
  const q = $derived(query.trim().toLowerCase());
  const filteredDrafts = $derived(
    q === '' ? drafts : drafts.filter(d => d.method.toLowerCase().includes(q) || d.module.toLowerCase().includes(q))
  );
  const draftsByModule = $derived.by(() => {
    const groups: Record<string, DraftEntry[]> = {};
    for (const d of filteredDrafts) (groups[d.module] ??= []).push(d);
    return groups;
  });

  // ---- Envs pointer ----

  // Envs are registered from the command line: Register on the Envs tab
  // opens a pointer to the command, not a form.
  let envPointerOpen = $state(false);

  function fmtSize(n: number | null): string {
    if (n == null) return '—';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function fmtHash(h: string | null): string {
    if (!h) return '—';
    return h.length > 12 ? h.slice(0, 10) + '…' : h;
  }

  // Case-insensitive substring filter over `name` per sub-tab.
  const filteredModules = $derived(
    q === '' ? modules : modules.filter(m => m.name.toLowerCase().includes(q))
  );
  const filteredMethods = $derived(
    q === '' ? methods : methods.filter(m => m.name.toLowerCase().includes(q) || m.module.toLowerCase().includes(q))
  );
  const filteredSamples = $derived(
    q === '' ? samples : samples.filter(s => s.name.toLowerCase().includes(q))
  );

  // Group methods by module for rendering.
  const methodsByModule = $derived.by(() => {
    const groups: Record<string, MethodRow[]> = {};
    for (const m of filteredMethods) {
      (groups[m.module] ??= []).push(m);
    }
    return groups;
  });

  /** A module shows its methods unless collapsed; a filter match always opens it. */
  function moduleOpen(name: string): boolean {
    return q !== '' || !collapsedModules.has(name);
  }
</script>

<div class="registry" style:display={visible ? 'flex' : 'none'}>
  <div class="sub-tabs">
    <div class="sub-tab-group">
      <button class="sub-tab" class:active={sub === 'modules'}  onclick={() => (sub = 'modules')}>Modules <span class="count">{modules.length}</span></button>
      <button class="sub-tab" class:active={sub === 'methods'}  onclick={() => (sub = 'methods')}>Methods <span class="count">{methods.length}</span></button>
      <button class="sub-tab" class:active={sub === 'envs'}     onclick={() => (sub = 'envs')}>Envs</button>
      <button class="sub-tab" class:active={sub === 'samples'}  onclick={() => (sub = 'samples')}>Samples <span class="count">{samples.length}</span></button>
    </div>
    <input class="search" type="search" placeholder="Filter {sub}…" bind:value={query} />
    <button class="btn-ghost refresh" data-testid="registry-refresh" disabled={loading}
            title="Reload the lists and every open method" onclick={() => refresh(true)}>
      <span class="spin" class:on={loading}>↻</span> Refresh
    </button>
    <span class="refresh-note" data-testid="refresh-note" aria-live="polite">
      {loading ? 'Reloading…' : refreshedByUser ? 'Updated just now' : ''}
    </span>
    {#if sub === 'methods' || sub === 'modules'}
      <button class="btn-ghost" data-testid="collapse-all" onclick={() => setAllModules(true)}>Collapse all</button>
      <button class="btn-ghost" data-testid="expand-all" onclick={() => setAllModules(false)}>Expand all</button>
    {/if}
    <span class="spacer"></span>
    <button class="btn-primary" onclick={openRegister}>+ Register {sub.slice(0, -1)}</button>
  </div>

  {#if envPointerOpen}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <div class="pointer-backdrop" onclick={(e) => { if (e.target === e.currentTarget) envPointerOpen = false; }}>
      <div class="pointer-dialog" role="dialog" aria-modal="true" aria-labelledby="env-pointer-title"
           tabindex="-1" onkeydown={(e) => { if (e.key === 'Escape') envPointerOpen = false; }}>
        <EnvCliPointer headingId="env-pointer-title" onClose={() => (envPointerOpen = false)} />
      </div>
    </div>
  {/if}

  {#if registerKind !== null}
    <RegisterModal
      initialKind={registerKind}
      moduleNames={modules.map(m => m.name)}
      {methods}
      initialTarget={registerTarget}
      registeredMethodDirs={methods.map(m => `methods/${m.name}`)}
      onClose={() => { registerKind = null; registerTarget = null; }}
      onRegistered={onRegistered}
    />
  {/if}

  {#if registerDraft}
    <!-- A draft's Register as vN: the existing check-then-confirm flow, on its folder. -->
    <RegisterModal
      initialKind="method"
      moduleNames={modules.map(m => m.name)}
      {methods}
      initialTarget={{ module: registerDraft.module, method: registerDraft.method }}
      initialDirectory={registerDraft.folder}
      initialNewMethod={registerDraft.kind === 'method'}
      registeredMethodDirs={methods.map(m => `methods/${m.name}`)}
      onClose={() => { registerDraft = null; }}
      onRegistered={() => { void refresh(); void refreshPalette(); }}
    />
  {/if}

  {#if draftDialogOpen}
    <DraftFromFolderDialog moduleNames={modules.map(m => m.name)}
                           onClose={() => (draftDialogOpen = false)}
                           onCreated={() => void draftsChanged()} />
  {/if}

  {#if draftConfirm}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <div class="pointer-backdrop" onclick={(e) => { if (e.target === e.currentTarget) draftConfirm = null; }}>
      <div class="pointer-dialog" role="dialog" aria-modal="true" aria-labelledby="draft-confirm-title"
           tabindex="-1" data-testid="draft-confirm"
           onkeydown={(e) => { if (e.key === 'Escape') draftConfirm = null; }}>
        <h3 id="draft-confirm-title" class="confirm-title">{draftConfirm.title}</h3>
        <!-- Execution's plan, rendered as given (Deletes / Keeps / hint);
             a plan with nothing to delete has only its message. -->
        <div class="confirm-text" data-testid="draft-confirm-plan">
          {#if draftConfirm.plan.deletes.length > 0}
            <div class="confirm-list-head">Deletes</div>
            <ul class="confirm-list" data-testid="draft-confirm-deletes">
              {#each draftConfirm.plan.deletes as line}<li>{line}</li>{/each}
            </ul>
            {#if draftConfirm.plan.keeps.length > 0}
              <div class="confirm-list-head">Keeps</div>
              <ul class="confirm-list" data-testid="draft-confirm-keeps">
                {#each draftConfirm.plan.keeps as line}<li>{line}</li>{/each}
              </ul>
            {/if}
            {#if draftConfirm.plan.hint}
              <p class="confirm-hint" data-testid="draft-confirm-hint">{draftConfirm.plan.hint}</p>
            {/if}
          {:else}
            <p class="confirm-message">{draftConfirm.plan.message}</p>
          {/if}
        </div>
        <div class="confirm-foot">
          <button type="button" class="btn-ghost" onclick={() => (draftConfirm = null)}>Cancel</button>
          <button type="button" class="btn-ghost danger" data-testid="draft-confirm-action"
                  onclick={() => void confirmDraftAction()}>{draftConfirm.action}</button>
        </div>
      </div>
    </div>
  {/if}

  {#if loadError}
    <div class="error">Failed to load registry: {loadError}</div>
  {:else if loading && modules.length + methods.length + samples.length === 0}
    <div class="empty">Loading…</div>
  {:else}
    <div class="body" bind:this={bodyEl}>
      {#if sub === 'modules'}
        {#if filteredModules.length === 0}
          <div class="empty">No modules registered.</div>
        {:else}
          <div class="modules-list">
            {#each filteredModules as mod (mod.name)}
              {@const open = expandedModuleRows.has(mod.name)}
              <section class="module" style="--m: {moduleColor(mod.name)}">
                <ModuleListRow module={mod} expanded={open}
                               onToggle={() => toggleModuleRow(mod.name)}
                               onMethods={() => { sub = 'methods'; query = mod.name; }}
                               onColour={() => openColour(mod.name)} />
                {#if open}
                  <ModulePanel module={mod} methods={methodsOf(mod.name)}
                               colourOpen={colourOpenFor === mod.name}
                               onColourClose={() => (colourOpenFor = null)} />
                {/if}
              </section>
            {/each}
          </div>
        {/if}
      {:else if sub === 'methods'}
          <div class="methods-list">
            {#if filteredMethods.length === 0}
              <div class="empty">No methods registered.</div>
            {/if}
            {#each Object.entries(methodsByModule) as [modName, rows] (modName)}
              {@const isOpen = moduleOpen(modName)}
              <section class="module" data-testid="module-group" data-module={modName}
                       style="--m: {moduleColor(modName)}">
                <button type="button" class="mod-row" data-testid="module-heading"
                        aria-expanded={isOpen} onclick={() => toggleModule(modName)}>
                  <span class="chev">{isOpen ? '▼' : '▶'}</span>
                  <LabelTag kind="module" label="MODULE" value={modName} color={moduleColor(modName)} />
                  <span class="mod-count" data-testid="module-count">{rows.length} {rows.length === 1 ? 'method' : 'methods'}</span>
                </button>
                {#if isOpen}
                  {#each rows as m (m.name)}
                    {@const key = methodKey(m.module, m.name)}
                    {@const vid = shownVersion(m)}
                    {@const dkey = detailKey(key, vid)}
                    {@const expanded = expandedMethods.has(key)}
                    <MethodListRow method={m} {expanded} shown={vid}
                                   menuOpen={menu?.key === `method:${key}`}
                                   onToggle={() => toggleExpand(m)}
                                   onMenu={(anchor) => methodMenu(m, anchor)}
                                   onSelect={(identity) => selectVersion(m, identity)}
                                   onVersionMenu={(v, anchor) => versionMenu(m, v, anchor)} />
                    {#if expanded}
                      <div class="panel">
                        <div class="inner">
                          <RegistryMethodPanel
                            detail={methodDetails[dkey] ?? null}
                            error={detailError[dkey] ?? null}
                            note={versionNote[key] ?? null}
                          />
                        </div>
                      </div>
                    {/if}
                  {/each}
                {/if}
              </section>
            {/each}
            <!-- The Drafts group: laid out like a module group, sub-grouped by module. -->
            <section class="module drafts" data-testid="drafts-group">
              <div class="drafts-head">
                <button type="button" class="drafts-toggle" data-testid="drafts-heading"
                        aria-expanded={draftsOpen} onclick={() => (draftsOpen = !draftsOpen)}>
                  <span class="chev">{draftsOpen ? '▼' : '▶'}</span>
                  <b>Drafts</b>
                  <span class="mod-count" data-testid="drafts-count">{filteredDrafts.length} · each runs its folder as it is now</span>
                </button>
                <button type="button" class="btn-ghost sm" data-testid="draft-from-folder"
                        onclick={() => (draftDialogOpen = true)}>+ Draft from folder…</button>
              </div>
              {#if draftsOpen}
                {#if filteredDrafts.length === 0}
                  <div class="drafts-empty">No drafts. A draft runs a method folder as it is now, without registering it.</div>
                {/if}
                {#each Object.entries(draftsByModule) as [modName, rows] (modName)}
                  {@const dOpen = q !== '' || !collapsedDraftModules.has(modName)}
                  <div class="draft-module" data-testid="draft-module-group" data-module={modName}
                       style="--m: {moduleColor(modName)}">
                    <button type="button" class="mod-row msub" data-testid="draft-module-heading"
                            aria-expanded={dOpen} onclick={() => toggleDraftModule(modName)}>
                      <span class="chev">{dOpen ? '▼' : '▶'}</span>
                      <LabelTag kind="module" label="MODULE" value={modName} color={moduleColor(modName)} />
                      <span class="mod-count">{rows.length} {rows.length === 1 ? 'draft' : 'drafts'} · a draft version is matched to its method in this module; a new method joins this module when registered</span>
                    </button>
                    {#if dOpen}
                      {#each rows as d (d.method)}
                        {@const dkey = draftKey(d)}
                        <DraftListRow draft={d} expanded={expandedDrafts.has(dkey)}
                                      menuOpen={menu?.key === `draft:${dkey}`}
                                      updating={updatingDraft === dkey}
                                      onToggle={() => toggleDraft(d)}
                                      onMenu={(anchor) => draftMenu(d, anchor)}
                                      onUpdate={() => runUpdate(d)} />
                        {#if draftNotice[dkey]}
                          <div class="draft-notice" class:bad={draftNotice[dkey].bad} data-testid="draft-notice">{draftNotice[dkey].text}</div>
                        {/if}
                        {#if expandedDrafts.has(dkey)}
                          <div class="panel">
                            <div class="inner">
                              <DraftPanel draft={d} detail={draftDetails[dkey] ?? null}
                                          detailError={draftDetailError[dkey] ?? null}
                                          tab={draftTab[dkey] ?? 'parameters'}
                                          run={draftRuns[dkey] ?? null}
                                          runLoading={draftRunLoading.has(dkey)}
                                          runError={draftRunError[dkey] ?? null}
                                          onTab={(t) => setDraftTab(d, t)}
                                          onRegister={() => (registerDraft = d)}
                                          onOpenHistory={(run) => showDraftRunInHistory(run)}
                                          onViewPipeline={showPipelineInHistory}
                                          onOpenStep={showDraftRunInHistory}
                                          onDeleteOutputs={() => askDeleteOutputs(d)} />
                            </div>
                          </div>
                        {/if}
                      {/each}
                    {/if}
                  </div>
                {/each}
              {/if}
            </section>
          </div>
      {:else if sub === 'envs'}
        <EnvsSubTab visible={sub === 'envs'} filter={query} />
      {:else if sub === 'samples'}
        {#if filteredSamples.length === 0}
          <div class="empty">No samples registered.</div>
        {:else}
          <table class="reg-table">
            <thead>
              <tr><th>Name</th><th>Source</th><th>Size</th><th>Hash</th><th>Pushed</th><th>Runs</th></tr>
            </thead>
            <tbody>
              {#each filteredSamples as s}
                <tr>
                  <td class="mono">{s.name}</td>
                  <td class="mono dim">{s.source}</td>
                  <td class="mono dim">{fmtSize(s.size)}</td>
                  <td class="mono dim">{fmtHash(s.hash)}</td>
                  <td>
                    {#if s.pushed}
                      <span class="chip chip-ok">pushed</span>
                    {:else}
                      <span class="chip chip-warn">local-only</span>
                    {/if}
                  </td>
                  <td class="mono dim">{s.runCount}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      {/if}
    </div>
  {/if}
</div>

{#if menu}
  <PopMenu anchor={menu.anchor} items={menu.items} title={menu.title} align={menu.align}
           testid={menu.testid} onClose={closeMenus} />
{/if}
{#if details}
  <VersionDetailsCard method={details.method} version={details.version}
                      anchor={details.anchor} onClose={() => (details = null)} />
{/if}

<style>
  .registry {
    flex: 1;
    display: flex;
    flex-direction: column;
    background: var(--bg-canvas);
    color: var(--text-primary);
    overflow: hidden;
  }
  .sub-tabs {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    padding: 10px 14px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .sub-tab-group {
    display: flex;
    gap: 2px;
    background: var(--bg-input);
    padding: 4px;
    border-radius: 6px;
  }
  .sub-tab {
    padding: 5px 12px;
    color: var(--text-secondary);
    font-size: 12.5px;
    background: none;
    border: none;
    border-radius: 3px;
    cursor: pointer;
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  .sub-tab.active { background: var(--accent); color: white; }
  .sub-tab .count {
    font-size: 10px;
    color: inherit;
    opacity: 0.7;
    font-family: "JetBrains Mono", Consolas, monospace;
  }

  .search {
    background: var(--bg-input);
    border: 1px solid var(--border);
    color: var(--text-primary);
    font-size: 12.5px;
    padding: 5px 10px;
    border-radius: 4px;
    width: 220px;
    outline: none;
  }

  .btn-ghost {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-primary);
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 3px;
    cursor: pointer;
  }
  .btn-ghost:hover { background: var(--bg-header); }
  .btn-ghost:disabled { opacity: 0.6; cursor: default; }
  .spin { display: inline-block; }
  .spin.on { animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .refresh-note { color: var(--text-secondary); font-size: 11px; min-width: 90px; }
  .spacer { flex: 1; }

  .btn-primary {
    background: var(--accent);
    border: none;
    color: white;
    font-size: 12.5px;
    padding: 5px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-weight: 500;
  }
  .btn-primary:hover { background: #5aa0e9; }

  .body {
    flex: 1;
    overflow: auto;
    padding: 14px;
  }

  .empty, .error {
    padding: 28px 14px;
    color: var(--text-secondary);
    font-size: 12.5px;
  }
  .error { color: var(--status-failed-ink); }

  .reg-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }
  .reg-table thead th {
    text-align: left;
    color: var(--text-secondary);
    font-weight: 600;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
  }
  .reg-table tbody td {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
    vertical-align: top;
  }
  .reg-table tbody tr:hover { background: var(--bg-header); }

  /* One grid for the method rows and the open panel: the method two shares
     of the width, version and env one each, so they line up row to row. */
  .methods-list { --reg-grid: 16px minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr) 96px 30px; }
  /* The Drafts group: a plain group (no module colour) whose module
     sub-headings take their module's colour, as module groups do. */
  .drafts-head {
    display: flex; align-items: center; gap: 10px; padding: 7px 10px 7px 7px; background: var(--bg-header);
  }
  .drafts-toggle {
    display: flex; align-items: center; gap: 10px; flex: 1; background: none; border: none; padding: 0;
    color: inherit; font: inherit; cursor: pointer; text-align: left;
  }
  .drafts-toggle .chev { color: var(--text-secondary); font-size: 9px; width: 16px; text-align: center; }
  .drafts-toggle b { color: #e6e6e6; }
  .btn-ghost.sm { font-size: 11.5px; padding: 3px 9px; }
  .btn-ghost.danger {
    color: var(--status-failed-ink); border-color: color-mix(in srgb, var(--status-failed) 55%, transparent);
  }
  .mod-row.msub { padding-left: 20px; border-top: 1px solid rgba(62, 62, 66, 0.5); background: #262628; }
  .drafts-empty { padding: 10px 14px 12px 33px; color: var(--text-secondary); font-size: 12px; }
  .draft-notice { padding: 4px 10px 6px 54px; font-size: 12px; color: var(--text-primary); }
  .draft-notice.bad { color: var(--status-failed-ink); }
  .confirm-title { margin: 0 0 8px; font-size: 15px; color: #eee; }
  .confirm-text { margin: 0 0 14px; color: var(--text-primary); font-size: 12.5px; line-height: 1.5; }
  .confirm-list-head { font-weight: 600; margin: 6px 0 2px; }
  .confirm-list { margin: 0 0 6px; padding-left: 18px; }
  .confirm-list li { margin: 2px 0; }
  .confirm-hint { margin: 8px 0 0; color: var(--text-secondary); }
  .confirm-message { margin: 0; white-space: pre-wrap; }
  .confirm-foot { display: flex; justify-content: flex-end; gap: 8px; }
  .module {
    margin-top: 10px;
    border: 1px solid var(--border);
    border-radius: 5px;
    overflow: hidden;
  }
  .module:first-child { margin-top: 0; }
  .mod-row {
    display: flex; align-items: center; gap: 10px; width: 100%;
    padding: 7px 10px 7px 7px; background: var(--bg-header); border: none;
    border-left: 3px solid var(--m); color: inherit; font: inherit; cursor: pointer; text-align: left;
  }
  .mod-row:hover { background: #333337; }
  .mod-row .chev { color: var(--text-secondary); font-size: 9px; width: 16px; text-align: center; }
  .mod-count { color: var(--text-secondary); font-size: 11px; }
  .panel {
    display: grid; grid-template-columns: var(--reg-grid); column-gap: 10px;
    background: var(--bg-input); border-top: 1px solid rgba(62, 62, 66, 0.5);
    padding: 12px 10px 14px 28px;
  }
  .panel > .inner { grid-column: 2 / -1; min-width: 0; }

  .mono { font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px; }
  .name { color: #e6e6e6; font-weight: 500; }
  .sub { font-size: 10.5px; color: var(--text-secondary); margin-top: 2px; }
  .dim { color: var(--text-secondary); }

  /* Module rows: one grid so the tag, description, methods link and Colour
     line up row to row, as the Methods rows do. */
  .modules-list { --mod-grid: 16px minmax(0, 260px) minmax(0, 1fr) 130px 110px; }
  .modules-list .module { margin-top: 10px; }
  .modules-list .module:first-child { margin-top: 0; }

  .pointer-backdrop {
    position: fixed; inset: 0; z-index: 50; display: flex; align-items: flex-start; justify-content: center;
    padding-top: 12vh; background: rgba(0, 0, 0, 0.55);
  }
  .pointer-dialog {
    width: 560px; max-width: calc(100vw - 32px); background: #242424; border: 1px solid #555;
    border-radius: 8px; padding: 18px 20px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.8); outline: none;
  }

  .chip {
    display: inline-block;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 2px;
    font-family: "JetBrains Mono", Consolas, monospace;
    border: 1px solid;
  }
  /* Pushed is a passed check (ok green); local-only is a notice, neutral. */
  .chip-ok     { color: var(--status-ok); background: color-mix(in srgb, var(--status-ok) 14%, transparent); border-color: color-mix(in srgb, var(--status-ok) 35%, transparent); }
  .chip-warn   { color: var(--text-primary); background: transparent; border-color: var(--marker-light); }
</style>
