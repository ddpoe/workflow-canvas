<script lang="ts">
  /**
   * Registry tab — scoped Phase 1 read-only view over the new
   * /api/registry/* endpoints. No drawer, no forms, no primitive library —
   * those ship later. Styling follows the existing Sidebar/Toolbar aesthetic.
   *
   * Endpoints used:
   *   GET /api/registry/modules
   *   GET /api/registry/methods
   *   GET /api/registry/samples
   *
   * Envs sub-tab is stubbed — the env-registry backend is deferred until the
   * lock-file-based code-fingerprinting story lands.
   */

  import { highlight } from './shikiHighlight.js';
  import RegisterModal from './RegisterModal.svelte';
  import EnvsSubTab from './EnvsSubTab.svelte';

  let { visible = true }: { visible?: boolean } = $props();

  type SubTab = 'modules' | 'methods' | 'samples' | 'envs';
  let sub = $state<SubTab>('modules');
  let query = $state('');

  // Method-row expansion state.
  type FileEntry = { name: string; language: string; content: string };
  type SlotSpec = { type?: string; required?: boolean; multiple?: boolean; description?: string };
  type Contract = {
    input_slots: Record<string, SlotSpec>;
    output_slots: Record<string, SlotSpec>;
    params_schema: Record<string, any>;
    executor: string;
  };
  type MethodDetail = { files: FileEntry[]; contract: Contract };

  let expandedMethods = $state<Set<string>>(new Set());
  let methodDetails = $state<Record<string, MethodDetail>>({});
  let activeFileName = $state<Record<string, string>>({});
  let highlightedHtml = $state<Record<string, string>>({}); // key: `${module}::${method}::${fileName}`
  let detailError = $state<Record<string, string>>({});

  function methodKey(mod: string, meth: string): string {
    return `${mod}::${meth}`;
  }

  async function toggleExpand(mod: string, meth: string) {
    const key = methodKey(mod, meth);
    const next = new Set(expandedMethods);
    if (next.has(key)) {
      next.delete(key);
      expandedMethods = next;
      return;
    }
    next.add(key);
    expandedMethods = next;

    if (methodDetails[key]) {
      // Already cached — nothing to fetch, but re-trigger highlight for active file.
      const active = activeFileName[key];
      if (active) void renderFile(key, methodDetails[key].files.find(f => f.name === active));
      return;
    }

    try {
      const resp = await fetch(`/api/registry/methods/${encodeURIComponent(mod)}/${encodeURIComponent(meth)}/detail`);
      if (!resp.ok) {
        detailError = { ...detailError, [key]: `HTTP ${resp.status}: ${await resp.text()}` };
        return;
      }
      const body: MethodDetail = await resp.json();
      methodDetails = { ...methodDetails, [key]: body };
      // Pre-select the first file (or the one matching the method's script).
      const preferred =
        body.files.find(f => f.name === `${meth}.py`) ??
        body.files.find(f => f.language === 'python') ??
        body.files[0];
      if (preferred) {
        activeFileName = { ...activeFileName, [key]: preferred.name };
        void renderFile(key, preferred);
      }
    } catch (err) {
      detailError = { ...detailError, [key]: String(err) };
    }
  }

  async function selectFile(mod: string, meth: string, file: FileEntry) {
    const key = methodKey(mod, meth);
    activeFileName = { ...activeFileName, [key]: file.name };
    await renderFile(key, file);
  }

  async function renderFile(methodKey_: string, file: FileEntry | undefined) {
    if (!file) return;
    const htmlKey = `${methodKey_}::${file.name}`;
    if (highlightedHtml[htmlKey]) return; // cached
    try {
      const html = await highlight(file.content, file.language);
      highlightedHtml = { ...highlightedHtml, [htmlKey]: html };
    } catch (err) {
      // Fall back to plain escaped text.
      const escaped = file.content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      highlightedHtml = { ...highlightedHtml, [htmlKey]: `<pre class="shiki"><code>${escaped}</code></pre>` };
    }
  }

  function slotEntries(slots: Record<string, SlotSpec>): Array<[string, SlotSpec]> {
    return Object.entries(slots ?? {});
  }

  // Register modal state.
  type RegisterKind = 'module' | 'method' | 'sample';
  let registerKind = $state<RegisterKind | null>(null);

  // Map plural sub-tab name -> singular register kind.
  const SUB_TO_KIND: Record<SubTab, RegisterKind | null> = {
    modules: 'module',
    methods: 'method',
    samples: 'sample',
    envs: null, // no backend yet — falls back to 'module' below
  };

  function openRegister() {
    registerKind = SUB_TO_KIND[sub] ?? 'module';
  }

  function onRegistered(_kind: RegisterKind) {
    // Refresh tables so the new row is visible immediately.
    void refresh();
  }

  type ContractRow = { type: string; name: string; value_type: string | null; required: boolean };
  type ModuleRow = { name: string; description: string; contracts: ContractRow[]; methods: number; source: string };
  type MethodRow = { name: string; module: string; env: string; validated: boolean | null; runCount: number; source: string };
  type SampleRow = {
    name: string;
    source: string;
    size: number | null;
    hash: string | null;
    pushed: boolean;
    runCount: number;
    registered_at: string | null;
    file_type: string;
  };

  let modules = $state<ModuleRow[]>([]);
  let methods = $state<MethodRow[]>([]);
  let samples = $state<SampleRow[]>([]);
  let loading = $state(false);
  let loadError = $state<string | null>(null);

  async function refresh() {
    loading = true;
    loadError = null;
    try {
      const [mods, meths, sams] = await Promise.all([
        fetch('/api/registry/modules').then(r => r.json()),
        fetch('/api/registry/methods').then(r => r.json()),
        fetch('/api/registry/samples').then(r => r.json()),
      ]);
      modules = mods.modules ?? [];
      methods = meths.methods ?? [];
      samples = sams.samples ?? [];
    } catch (err) {
      loadError = String(err);
    } finally {
      loading = false;
    }
  }

  // Reload when the tab becomes visible.
  $effect(() => {
    if (visible) refresh();
  });

  // Per-method Validate state so a failed check surfaces inline on the row
  // instead of a disembodied alert() that didn't even say which method
  // failed. ``validateInFlight`` gates the button + acts as a spinner.
  let validateInFlight = $state<Set<string>>(new Set());
  let validateError = $state<Record<string, string>>({});

  async function validateMethod(mod: string, method: string) {
    const key = methodKey(mod, method);
    validateInFlight = new Set(validateInFlight).add(key);
    const nextErr = { ...validateError };
    delete nextErr[key];
    validateError = nextErr;

    try {
      const resp = await fetch('/api/registry/methods/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ module: mod, method }),
      });
      if (!resp.ok) {
        const text = await resp.text();
        // Prefer JSON-body .detail when available (our HTTPException shape);
        // fall back to raw text for plain-text error bodies.
        let detail = text;
        try {
          const parsed = JSON.parse(text);
          if (parsed && typeof parsed.detail === 'string') detail = parsed.detail;
        } catch { /* not JSON */ }
        throw new Error(`HTTP ${resp.status}: ${detail}`);
      }
      const body = await resp.json();
      // Update just that row's `validated` field.
      methods = methods.map(m =>
        m.module === mod && m.name === method ? { ...m, validated: body.validated } : m
      );
    } catch (err) {
      validateError = { ...validateError, [key]: String(err) };
    } finally {
      const next = new Set(validateInFlight);
      next.delete(key);
      validateInFlight = next;
    }
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
    <button class="btn-ghost" onclick={refresh} title="Reload">↻</button>
    {#if sub !== 'envs'}
      <button class="btn-primary" onclick={openRegister}>+ Register {sub.slice(0, -1)}</button>
    {/if}
  </div>

  {#if registerKind !== null}
    <RegisterModal
      initialKind={registerKind}
      moduleNames={modules.map(m => m.name)}
      registeredMethodDirs={methods.map(m => `methods/${m.name}`)}
      onClose={() => (registerKind = null)}
      onRegistered={onRegistered}
    />
  {/if}

  {#if loadError}
    <div class="error">Failed to load registry: {loadError}</div>
  {:else if loading && modules.length + methods.length + samples.length === 0}
    <div class="empty">Loading…</div>
  {:else}
    <div class="body">
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
          {#each Object.entries(methodsByModule) as [modName, rows]}
            <div class="group-header mono">{modName} <span class="dim">({rows.length})</span></div>
            <table class="reg-table nested">
              <!-- Shared fixed column widths: without these each per-module
                   table auto-sizes from its own content and the env /
                   validated / runs columns drift between groups. -->
              <colgroup>
                <col />
                <col class="col-env" />
                <col class="col-validated" />
                <col class="col-runs" />
              </colgroup>
              <tbody>
                {#each rows as m}
                  {@const key = methodKey(m.module, m.name)}
                  {@const expanded = expandedMethods.has(key)}
                  {@const inFlight = validateInFlight.has(key)}
                  {@const vErr = validateError[key]}
                  <tr class="method-row" class:expanded onclick={() => toggleExpand(m.module, m.name)}>
                    <td class="mono expand-cell">
                      <span class="chevron">{expanded ? '▼' : '▶'}</span>
                      {m.name}
                    </td>
                    <td><span class="chip chip-env">{m.env}</span></td>
                    <td onclick={(e) => e.stopPropagation()}>
                      {#if m.validated === true}
                        <span class="chip chip-ok">valid</span>
                      {:else if m.validated === false}
                        <span class="chip chip-fail">invalid</span>
                      {:else if vErr}
                        <span class="chip chip-fail" title={vErr}>check failed</span>
                        <button class="btn-ghost validate-retry"
                                disabled={inFlight}
                                onclick={() => validateMethod(m.module, m.name)}>retry</button>
                        <div class="validate-err-detail mono" title={vErr}>{vErr}</div>
                      {:else}
                        <button class="btn-ghost"
                                disabled={inFlight}
                                onclick={() => validateMethod(m.module, m.name)}>
                          {inFlight ? '…' : 'check'}
                        </button>
                      {/if}
                    </td>
                    <td class="mono dim">{m.runCount} runs</td>
                  </tr>
                  {#if expanded}
                    <tr class="method-detail-row">
                      <td colspan="4">
                        {#if detailError[key]}
                          <div class="error">Failed to load: {detailError[key]}</div>
                        {:else if !methodDetails[key]}
                          <div class="dim">Loading…</div>
                        {:else}
                          {@const detail = methodDetails[key]}
                          <div class="detail-grid">
                            <div class="detail-contract">
                              <div class="section-label">Contract</div>
                              <div class="slot-grid">
                                {#if slotEntries(detail.contract.input_slots).length > 0}
                                  <div class="slot-group-label">Inputs</div>
                                  {#each slotEntries(detail.contract.input_slots) as [name, spec]}
                                    <div class="slot-name mono">{name}</div>
                                    <div><span class="chip chip-input">{spec.type ?? '—'}</span></div>
                                    <div class="slot-info dim">{spec.required === false ? 'optional' : 'required'}{spec.multiple ? ' · multi' : ''}</div>
                                  {/each}
                                {/if}
                                {#if slotEntries(detail.contract.output_slots).length > 0}
                                  <div class="slot-group-label">Outputs</div>
                                  {#each slotEntries(detail.contract.output_slots) as [name, spec]}
                                    <div class="slot-name mono">{name}</div>
                                    <div><span class="chip chip-output">{spec.type ?? '—'}</span></div>
                                    <div class="slot-info dim">{spec.required === false ? 'optional' : 'required'}</div>
                                  {/each}
                                {/if}
                                {#if Object.keys(detail.contract.params_schema ?? {}).length > 0}
                                  <div class="slot-group-label">Params</div>
                                  {#each Object.entries(detail.contract.params_schema) as [name, spec]}
                                    {@const s = spec as SlotSpec & { default?: any }}
                                    <div class="slot-name mono">{name}</div>
                                    <div><span class="chip chip-param">{s.type ?? '—'}</span></div>
                                    <div class="slot-info dim">
                                      {s.required === false ? 'optional' : 'required'}{#if s.default !== undefined} · default <span class="mono">{JSON.stringify(s.default)}</span>{/if}
                                    </div>
                                  {/each}
                                {/if}
                              </div>
                            </div>

                            <div class="detail-files">
                              <div class="section-label">Files</div>
                              <div class="file-tabs">
                                {#each detail.files as f}
                                  <button
                                    class="file-tab"
                                    class:active={activeFileName[key] === f.name}
                                    onclick={() => selectFile(m.module, m.name, f)}
                                  >{f.name}</button>
                                {/each}
                              </div>
                              {#if activeFileName[key]}
                                {@const htmlKey = `${key}::${activeFileName[key]}`}
                                <div class="code-block">
                                  {#if highlightedHtml[htmlKey]}
                                    {@html highlightedHtml[htmlKey]}
                                  {:else}
                                    <div class="dim">Highlighting…</div>
                                  {/if}
                                </div>
                              {/if}
                            </div>
                          </div>
                        {/if}
                      </td>
                    </tr>
                  {/if}
                {/each}
              </tbody>
            </table>
          {/each}
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

<style>
  .registry {
    flex: 1;
    display: flex;
    flex-direction: column;
    background: #1a1a1a;
    color: #ccc;
    overflow: hidden;
  }
  .sub-tabs {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    background: #252526;
    border-bottom: 1px solid #3e3e42;
    flex-shrink: 0;
  }
  .sub-tab-group {
    display: flex;
    gap: 2px;
    background: #1e1e1e;
    padding: 4px;
    border-radius: 6px;
  }
  .sub-tab {
    padding: 5px 12px;
    color: #888;
    font-size: 12.5px;
    background: none;
    border: none;
    border-radius: 3px;
    cursor: pointer;
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  .sub-tab.active { background: #4A90D9; color: white; }
  .sub-tab .count {
    font-size: 10px;
    color: inherit;
    opacity: 0.7;
    font-family: "JetBrains Mono", Consolas, monospace;
  }

  .search {
    background: #1e1e1e;
    border: 1px solid #3e3e42;
    color: #ccc;
    font-size: 12.5px;
    padding: 5px 10px;
    border-radius: 4px;
    width: 220px;
    outline: none;
  }

  .btn-ghost {
    background: transparent;
    border: 1px solid #3e3e42;
    color: #ccc;
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 3px;
    cursor: pointer;
  }
  .btn-ghost:hover { background: #2d2d30; }
  .btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }
  .validate-retry { margin-left: 4px; }
  .validate-err-detail {
    margin-top: 3px;
    color: #e89090;
    font-size: 10.5px;
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .btn-primary {
    background: #4A90D9;
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
    color: #888;
    font-size: 12.5px;
  }
  .error { color: #E74C3C; }

  .reg-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }
  .reg-table thead th {
    text-align: left;
    color: #888;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    padding: 6px 10px;
    border-bottom: 1px solid #3e3e42;
  }
  .reg-table tbody td {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
    vertical-align: top;
  }
  .reg-table tbody tr:hover { background: #2d2d30; }

  .reg-table.nested { margin: 2px 0 14px 0; table-layout: fixed; }
  .reg-table.nested .col-env { width: 150px; }
  .reg-table.nested .col-validated { width: 170px; }
  .reg-table.nested .col-runs { width: 90px; }
  .reg-table.nested .expand-cell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .group-header {
    margin-top: 14px;
    padding: 6px 10px;
    background: #2d2d30;
    border-radius: 4px 4px 0 0;
    color: #e6e6e6;
    font-size: 11.5px;
    font-weight: 600;
    border-bottom: 1px solid #3e3e42;
  }
  .group-header:first-child { margin-top: 0; }

  .mono { font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px; }
  .name { color: #e6e6e6; font-weight: 500; }
  .sub { font-size: 10.5px; color: #888; margin-top: 2px; }
  .dim { color: #888; }

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
  .chip-output { color: #E9A847; background: rgba(233, 168, 71, 0.14); border-color: rgba(233, 168, 71, 0.35); }
  .chip-metric { color: #1ABC9C; background: rgba(26, 188, 156, 0.14); border-color: rgba(26, 188, 156, 0.35); }
  .chip-input  { color: #1ABC9C; background: rgba(26, 188, 156, 0.14); border-color: rgba(26, 188, 156, 0.35); }
  .chip-param  { color: #888;    background: #1e1e1e;                  border-color: #3e3e42; }
  .chip-more   { color: #888;    background: #1e1e1e;                  border-color: #3e3e42; }
  .chip-env    { color: #9b59b6; background: rgba(155, 89, 182, 0.14); border-color: rgba(155, 89, 182, 0.35); }
  .chip-ok     { color: #50C878; background: rgba(80, 200, 120, 0.14); border-color: rgba(80, 200, 120, 0.35); }
  .chip-fail   { color: #E74C3C; background: rgba(231, 76, 60, 0.14);  border-color: rgba(231, 76, 60, 0.35); }
  .chip-warn   { color: #E9A847; background: rgba(233, 168, 71, 0.14); border-color: rgba(233, 168, 71, 0.35); }

  /* Expandable method-row styles */
  .method-row { cursor: pointer; }
  .method-row .chevron { color: #888; font-size: 9px; margin-right: 8px; display: inline-block; width: 8px; }
  .method-row.expanded { background: #2d2d30; }
  .expand-cell { user-select: none; }
  .method-detail-row td {
    background: #1e1e1e;
    padding: 14px 18px 18px 28px;
    border-bottom: 1px solid #3e3e42;
  }
  .detail-grid {
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: 18px;
  }
  .section-label {
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    color: #888;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .slot-grid {
    display: grid;
    grid-template-columns: auto auto 1fr;
    column-gap: 12px;
    row-gap: 3px;
    align-items: center;
    font-size: 11.5px;
  }
  .slot-group-label {
    grid-column: 1 / -1;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: #e6e6e6;
    font-weight: 600;
    margin-top: 10px;
    margin-bottom: 2px;
    padding-bottom: 2px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
  }
  .slot-group-label:first-child { margin-top: 0; }
  .slot-name { color: #cccccc; }
  .slot-info { font-size: 10.5px; white-space: nowrap; }

  .cross-link {
    background: none;
    border: none;
    color: #4A90D9;
    font-size: 11.5px;
    font-family: "JetBrains Mono", Consolas, monospace;
    padding: 2px 6px;
    cursor: pointer;
    border-radius: 3px;
  }
  .cross-link:hover { background: rgba(74, 144, 217, 0.14); }
  .file-tabs {
    display: flex;
    gap: 2px;
    padding: 3px;
    background: #252526;
    border-radius: 4px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .file-tab {
    padding: 4px 10px;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 10.5px;
    color: #888;
    background: none;
    border: none;
    border-radius: 3px;
    cursor: pointer;
  }
  .file-tab.active { background: #4A90D9; color: white; }
  .code-block {
    background: #1e1e1e;
    border: 1px solid #3e3e42;
    border-radius: 4px;
    overflow: auto;
    max-height: 420px;
  }
  /* Shiki outputs its own <pre class="shiki"> with inline colors.
     Tighten the spacing to fit our dense UI. */
  .code-block :global(pre.shiki) {
    margin: 0;
    padding: 10px 14px;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 11.5px;
    line-height: 1.5;
    background: transparent !important;
  }
  .code-block :global(pre.shiki code) {
    font-family: inherit;
    font-size: inherit;
  }
</style>
