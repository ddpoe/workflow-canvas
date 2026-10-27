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
  import RegistryMethodPanel from './RegistryMethodPanel.svelte';
  import VersionDetailsCard from './VersionDetailsCard.svelte';
  import PopMenu from './PopMenu.svelte';
  import type { MenuItem } from './PopMenu.svelte';
  import LabelTag from '../shared/LabelTag.svelte';
  import { getModuleColor } from '../history/historyUtils.js';
  import { tick } from 'svelte';
  import { registryFocus } from './registryFocus.js';
  import type { RegistryFocus } from './registryFocus.js';
  import {
    fetchMethodDetail,
    fetchRegistryModules,
    fetchRegistryMethods,
    fetchRegistrySamples,
    methodExportUrl,
  } from './api.js';
  import type {
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
    envs: null, // no env register endpoint — falls back to 'module' below
  };

  function openRegister() {
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
      const [mods, meths, sams] = await Promise.all([
        fetchRegistryModules(),
        fetchRegistryMethods(),
        fetchRegistrySamples(),
      ]);
      modules = mods;
      methods = meths;
      samples = sams;
    } catch (err) {
      loadError = String(err);
    } finally {
      loading = false;
    }
    // Every cached detail may be stale: drop them, and open rows fetch theirs again.
    methodDetails = {};
    detailError = {};
    await Promise.all(methods
      .filter(m => expandedMethods.has(methodKey(m.module, m.name)))
      .map(m => loadDetail(m, shownVersion(m))));
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
    collapsedModules = collapsed ? new Set(methods.map(m => m.module)) : new Set();
  }

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
  const q = $derived(query.trim().toLowerCase());
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
    {#if sub === 'methods'}
      <button class="btn-ghost" data-testid="collapse-all" onclick={() => setAllModules(true)}>Collapse all</button>
      <button class="btn-ghost" data-testid="expand-all" onclick={() => setAllModules(false)}>Expand all</button>
    {/if}
    <span class="spacer"></span>
    {#if sub !== 'envs'}
      <button class="btn-primary" onclick={openRegister}>+ Register {sub.slice(0, -1)}</button>
    {/if}
  </div>

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
          <table class="reg-table">
            <thead>
              <tr><th>Name</th><th>Contracts</th><th>Methods</th><th>Source</th></tr>
            </thead>
            <tbody>
              {#each filteredModules as mod}
                <tr>
                  <td>
                    <div class="name mono">{mod.name}</div>
                    {#if mod.description}<div class="sub">{mod.description}</div>{/if}
                  </td>
                  <td class="contracts-cell">
                    {#if mod.contracts.length === 0}
                      <span class="dim">—</span>
                    {:else}
                      {#each mod.contracts.slice(0, 3) as c}
                        <span class="chip chip-{c.type}">{c.type} · {c.name}</span>
                      {/each}
                      {#if mod.contracts.length > 3}
                        <span class="chip chip-more">+{mod.contracts.length - 3}</span>
                      {/if}
                    {/if}
                  </td>
                  <td>
                    <button
                      class="cross-link mono"
                      title="View methods in {mod.name}"
                      onclick={() => { sub = 'methods'; query = mod.name; }}
                    >{mod.methods} methods →</button>
                  </td>
                  <td class="mono dim">{mod.source}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      {:else if sub === 'methods'}
        {#if filteredMethods.length === 0}
          <div class="empty">No methods registered.</div>
        {:else}
          <div class="methods-list">
            {#each Object.entries(methodsByModule) as [modName, rows] (modName)}
              {@const isOpen = moduleOpen(modName)}
              <section class="module" data-testid="module-group" data-module={modName}
                       style="--m: {getModuleColor(modName)}">
                <button type="button" class="mod-row" data-testid="module-heading"
                        aria-expanded={isOpen} onclick={() => toggleModule(modName)}>
                  <span class="chev">{isOpen ? '▼' : '▶'}</span>
                  <LabelTag kind="module" label="MODULE" value={modName} color={getModuleColor(modName)} />
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
          </div>
        {/if}
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
  .error { color: var(--color-failed); }

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
  .methods-list { --reg-grid: 16px minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr) 30px; }
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

  .contracts-cell {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .chip {
    display: inline-block;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 2px;
    font-family: "JetBrains Mono", Consolas, monospace;
    border: 1px solid;
  }
  .chip-output { color: var(--color-running); background: rgba(233, 168, 71, 0.14); border-color: rgba(233, 168, 71, 0.35); }
  .chip-metric { color: var(--color-system); background: rgba(26, 188, 156, 0.14); border-color: rgba(26, 188, 156, 0.35); }
  .chip-input  { color: var(--color-system); background: rgba(26, 188, 156, 0.14); border-color: rgba(26, 188, 156, 0.35); }
  .chip-param  { color: var(--text-secondary); background: var(--bg-input); border-color: var(--border); }
  .chip-more   { color: var(--text-secondary); background: var(--bg-input); border-color: var(--border); }
  .chip-ok     { color: var(--color-completed); background: rgba(80, 200, 120, 0.14); border-color: rgba(80, 200, 120, 0.35); }
  .chip-warn   { color: var(--color-running); background: rgba(233, 168, 71, 0.14); border-color: rgba(233, 168, 71, 0.35); }

  .cross-link {
    background: none;
    border: none;
    color: var(--accent);
    font-size: 11.5px;
    font-family: "JetBrains Mono", Consolas, monospace;
    padding: 2px 6px;
    cursor: pointer;
    border-radius: 3px;
  }
  .cross-link:hover { background: rgba(74, 144, 217, 0.14); }
</style>
