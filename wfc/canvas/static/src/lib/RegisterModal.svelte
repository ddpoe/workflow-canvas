<script lang="ts">
  /**
   * RegisterModal — one modal handling module / method / sample registration.
   *
   * Each kind has a small form, a "Dry Run" button (POSTs ?dryRun=true and
   * renders preChecks[]), and a "Register" button (POSTs without dryRun;
   * on success → onRegistered callback + close).
   *
   * Envs kind is not supported — the backend endpoint is deferred.
   */

  type Kind = 'module' | 'method' | 'sample';
  type PreCheck = { status: 'ok' | 'warn' | 'fail'; label: string; detail?: string };

  let {
    initialKind,
    moduleNames = [],
    registeredMethodDirs = [],
    onClose,
    onRegistered,
  }: {
    initialKind: Kind;
    moduleNames?: string[];
    registeredMethodDirs?: string[];
    onClose: () => void;
    onRegistered: (kind: Kind, record: any) => void;
  } = $props();

  let kind = $state<Kind>(initialKind);

  // Per-kind form state (kept alive across kind-switches within the modal).
  let moduleForm = $state({ name: '', description: '' });
  let methodForm = $state({ directory: '', module: '', method_name: '', env: 'inherit' });
  let sampleForm = $state({ name: '', source: '' });

  let preChecks = $state<PreCheck[]>([]);
  let serverError = $state<string | null>(null);
  let submitting = $state(false);

  // Directory-browse modal state (only used for method form).
  type FsEntry = { name: string; kind: 'dir' | 'file'; size?: number };
  let browseOpen = $state(false);
  let browsePath = $state('');
  let browseEntries = $state<FsEntry[]>([]);
  let browseError = $state<string | null>(null);

  async function openBrowse() {
    browseOpen = true;
    browsePath = '';
    await loadBrowse('');
  }

  async function loadBrowse(path: string) {
    browseError = null;
    try {
      const resp = await fetch(`/api/fs/browse?path=${encodeURIComponent(path)}`);
      if (!resp.ok) {
        browseError = `HTTP ${resp.status}: ${await resp.text()}`;
        return;
      }
      const body = await resp.json();
      browsePath = body.path;
      // Drop already-registered method dirs from the listing — registering
      // one of them would fail with a duplicate-name error anyway.
      const registered = new Set(registeredMethodDirs);
      browseEntries = body.entries.filter((e: FsEntry) => {
        const full = body.path ? `${body.path}/${e.name}` : e.name;
        return !(e.kind === 'dir' && registered.has(full));
      });
    } catch (err) {
      browseError = String(err);
    }
  }

  function goInto(name: string) {
    const next = browsePath ? `${browsePath}/${name}` : name;
    void loadBrowse(next);
  }

  function goUp() {
    const parent = browsePath.includes('/') ? browsePath.slice(0, browsePath.lastIndexOf('/')) : '';
    void loadBrowse(parent);
  }

  function selectCurrent() {
    methodForm.directory = browsePath;
    browseOpen = false;
  }

  const browseCrumbs = $derived.by(() => {
    if (!browsePath) return [];
    const parts = browsePath.split('/');
    const out: Array<{ name: string; path: string }> = [];
    let acc = '';
    for (const p of parts) {
      acc = acc ? `${acc}/${p}` : p;
      out.push({ name: p, path: acc });
    }
    return out;
  });

  function endpointFor(k: Kind): string {
    if (k === 'module') return '/api/registry/modules';
    if (k === 'method') return '/api/registry/methods';
    return '/api/registry/samples';
  }

  function bodyFor(k: Kind): any {
    if (k === 'module') {
      return { name: moduleForm.name, description: moduleForm.description || null, contracts: [] };
    }
    if (k === 'method') {
      return {
        directory: methodForm.directory,
        module: methodForm.module,
        method_name: methodForm.method_name || null,
        env: methodForm.env || 'inherit',
      };
    }
    return { name: sampleForm.name, source: sampleForm.source, registration_mode: 'copy' };
  }

  async function post(k: Kind, dryRun: boolean) {
    submitting = true;
    serverError = null;
    try {
      const url = endpointFor(k) + (dryRun ? '?dryRun=true' : '');
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bodyFor(k)),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        serverError = body.detail ?? `HTTP ${resp.status}`;
        preChecks = body.preChecks ?? [];
        return;
      }
      preChecks = body.preChecks ?? [];
      if (!dryRun && body.ok) {
        onRegistered(k, body);
        onClose();
      }
    } catch (err) {
      serverError = String(err);
    } finally {
      submitting = false;
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onClose();
  }

  function stop(e: Event) { e.stopPropagation(); }
</script>

<svelte:window onkeydown={onKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="overlay" onclick={onClose}>
  <div class="modal" onclick={stop}>
    <div class="modal-header">
      <div class="kind-switcher">
        <button class="kind-btn" class:active={kind === 'module'} onclick={() => (kind = 'module')}>Module</button>
        <button class="kind-btn" class:active={kind === 'method'} onclick={() => (kind = 'method')}>Method</button>
        <button class="kind-btn" class:active={kind === 'sample'} onclick={() => (kind = 'sample')}>Sample</button>
      </div>
      <button class="close-x" onclick={onClose} title="Close (Esc)">×</button>
    </div>

    <div class="modal-body">
      {#if kind === 'module'}
        <label class="field">
          <span class="label-text">Name <span class="req">*</span></span>
          <input class="input mono" type="text" bind:value={moduleForm.name} placeholder="preprocessing" />
        </label>
        <label class="field">
          <span class="label-text">Description</span>
          <textarea class="input" rows="3" bind:value={moduleForm.description}
            placeholder="Tile export, normalization, and batch correction…"></textarea>
        </label>
        <p class="hint">Contracts can be added later by editing the module's <code>module.yaml</code>.</p>

      {:else if kind === 'method'}
        <label class="field">
          <span class="label-text">Directory <span class="req">*</span></span>
          <div class="input-row">
            <input class="input mono" type="text" bind:value={methodForm.directory}
              placeholder="methods/transform or any path containing method.yaml" />
            <button type="button" class="btn-ghost-small" onclick={openBrowse}>Browse…</button>
          </div>
          <span class="hint-inline">Any absolute or project-relative path containing <code>method.yaml</code>. <code>wfc</code> copies it into <code>methods/{"{name}"}/</code> on registration.</span>
        </label>
        <label class="field">
          <span class="label-text">Module <span class="req">*</span></span>
          {#if moduleNames.length > 0}
            <select class="input mono" bind:value={methodForm.module}>
              <option value="" disabled selected={!methodForm.module}>— select module —</option>
              {#each moduleNames as name}
                <option value={name}>{name}</option>
              {/each}
            </select>
          {:else}
            <input class="input mono" type="text" bind:value={methodForm.module} placeholder="test_pipeline" />
          {/if}
        </label>
        <label class="field">
          <span class="label-text">Method name <span class="dim">(optional)</span></span>
          <input class="input mono" type="text" bind:value={methodForm.method_name}
            placeholder="auto-derived from directory name" />
        </label>
        <label class="field">
          <span class="label-text">Env</span>
          <input class="input mono" type="text" bind:value={methodForm.env} placeholder="inherit" />
        </label>

      {:else}
        <label class="field">
          <span class="label-text">Name <span class="req">*</span></span>
          <input class="input mono" type="text" bind:value={sampleForm.name} placeholder="CFPAC_ERKi" />
        </label>
        <label class="field">
          <span class="label-text">Source file <span class="req">*</span></span>
          <input class="input mono" type="text" bind:value={sampleForm.source}
            placeholder="/path/to/sample.csv" />
          <span class="hint-inline">Absolute path to the source data file. DVC must be configured.</span>
        </label>
      {/if}

      {#if serverError}
        <div class="error">Error: {serverError}</div>
      {/if}

      {#if preChecks.length > 0}
        <div class="checks">
          <div class="section-label">Pre-checks</div>
          {#each preChecks as c}
            <div class="check check-{c.status}">
              <span class="dot"></span>
              <span class="check-label">{c.label}</span>
              {#if c.detail}<span class="check-detail dim mono">{c.detail}</span>{/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <div class="modal-footer">
      <button class="btn btn-ghost" disabled={submitting} onclick={() => post(kind, true)}>Dry run</button>
      <div class="spacer"></div>
      <button class="btn btn-ghost" onclick={onClose}>Cancel</button>
      <button class="btn btn-primary" disabled={submitting} onclick={() => post(kind, false)}>Register {kind}</button>
    </div>
  </div>
</div>

{#if browseOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="overlay browse-overlay" onclick={() => (browseOpen = false)}>
    <div class="modal browse-modal" onclick={stop}>
      <div class="modal-header">
        <div class="mono browse-crumbs">
          <button class="crumb" onclick={() => loadBrowse('')}>project</button>
          {#each browseCrumbs as c}
            <span class="crumb-sep">/</span>
            <button class="crumb" onclick={() => loadBrowse(c.path)}>{c.name}</button>
          {/each}
        </div>
        <button class="close-x" onclick={() => (browseOpen = false)}>×</button>
      </div>

      <div class="browse-list">
        {#if browseError}
          <div class="error">{browseError}</div>
        {:else}
          {#if browsePath}
            <div class="browse-entry" onclick={goUp}>
              <span class="ent-icon">↑</span>
              <span class="ent-name dim">.. (up one)</span>
            </div>
          {/if}
          {#each browseEntries as e}
            <div
              class="browse-entry"
              class:browse-dir={e.kind === 'dir'}
              onclick={() => e.kind === 'dir' && goInto(e.name)}
            >
              <span class="ent-icon">{e.kind === 'dir' ? '📁' : '📄'}</span>
              <span class="ent-name mono">{e.name}</span>
              {#if e.kind === 'file' && e.size != null}
                <span class="ent-size dim">{e.size} B</span>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <div class="modal-footer">
        <div class="hint-inline dim mono">{browsePath || '(project root)'}</div>
        <div class="spacer"></div>
        <button class="btn btn-ghost" onclick={() => (browseOpen = false)}>Cancel</button>
        <button class="btn btn-primary" onclick={selectCurrent}>Select this folder</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .modal {
    width: 520px;
    max-height: 84vh;
    background: #252526;
    border: 1px solid #3e3e42;
    border-radius: 6px;
    box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.5);
    display: flex;
    flex-direction: column;
  }
  .modal-header {
    display: flex;
    align-items: center;
    padding: 12px 14px;
    border-bottom: 1px solid #3e3e42;
    gap: 10px;
  }
  .kind-switcher {
    display: flex;
    gap: 2px;
    background: #1e1e1e;
    padding: 3px;
    border-radius: 4px;
  }
  .kind-btn {
    padding: 5px 14px;
    color: #888;
    font-size: 12px;
    background: none;
    border: none;
    border-radius: 3px;
    cursor: pointer;
  }
  .kind-btn.active { background: #4A90D9; color: white; }
  .close-x {
    margin-left: auto;
    background: none;
    border: none;
    color: #888;
    font-size: 18px;
    cursor: pointer;
    padding: 0 4px;
  }
  .close-x:hover { color: #ccc; }

  .modal-body {
    padding: 16px 18px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
    color: #ccc;
  }

  .field { display: flex; flex-direction: column; gap: 4px; }
  .label-text { font-size: 11px; color: #e6e6e6; font-weight: 500; }
  .req { color: #E74C3C; margin-left: 2px; }

  .input {
    background: #1e1e1e;
    border: 1px solid #3e3e42;
    color: #ccc;
    font-size: 12.5px;
    padding: 6px 10px;
    border-radius: 4px;
    outline: none;
    font-family: "Mukta Vaani", system-ui, sans-serif;
  }
  .input.mono { font-family: "JetBrains Mono", Consolas, monospace; }
  .input:focus { border-color: #4A90D9; }
  textarea.input { resize: vertical; }

  .hint { font-size: 10.5px; color: #888; margin: 0; }
  .hint-inline { font-size: 10.5px; color: #888; }
  code { background: #1e1e1e; padding: 1px 4px; border-radius: 2px; font-size: 10.5px; }

  .error {
    background: rgba(231, 76, 60, 0.14);
    border: 1px solid rgba(231, 76, 60, 0.35);
    color: #E74C3C;
    padding: 6px 10px;
    border-radius: 3px;
    font-size: 11.5px;
  }

  .checks {
    border-top: 1px solid #3e3e42;
    padding-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .section-label {
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    color: #888;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11.5px;
  }
  .check .dot {
    width: 6px; height: 6px; border-radius: 3px; flex-shrink: 0;
  }
  .check-ok .dot   { background: #50C878; }
  .check-warn .dot { background: #E9A847; }
  .check-fail .dot { background: #E74C3C; }
  .check-label { color: #ccc; }
  .check-detail { font-size: 10.5px; }

  .modal-footer {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    border-top: 1px solid #3e3e42;
  }
  .modal-footer .spacer { flex: 1; }

  .btn {
    padding: 6px 14px;
    border-radius: 4px;
    font-size: 12.5px;
    border: none;
    cursor: pointer;
  }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-ghost { background: transparent; color: #ccc; border: 1px solid #3e3e42; }
  .btn-ghost:hover:not(:disabled) { background: #2d2d30; }
  .btn-primary { background: #4A90D9; color: white; font-weight: 500; }
  .btn-primary:hover:not(:disabled) { background: #5aa0e9; }
  .dim { color: #888; }

  .input-row {
    display: flex;
    gap: 6px;
    align-items: stretch;
  }
  .input-row .input { flex: 1; }
  .btn-ghost-small {
    background: transparent;
    border: 1px solid #3e3e42;
    color: #ccc;
    font-size: 11px;
    padding: 0 10px;
    border-radius: 4px;
    cursor: pointer;
    white-space: nowrap;
  }
  .btn-ghost-small:hover { background: #2d2d30; }

  select.input {
    background: #1e1e1e;
    padding: 6px 10px;
    cursor: pointer;
  }
  select.input option { background: #252526; color: #ccc; }

  .browse-overlay { z-index: 1001; }
  .browse-modal {
    width: 560px;
    height: 500px;
  }
  .browse-crumbs {
    display: flex;
    gap: 2px;
    align-items: center;
    font-size: 11.5px;
    overflow-x: auto;
    flex: 1;
  }
  .crumb {
    background: none;
    border: none;
    color: #4A90D9;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 11.5px;
    padding: 2px 4px;
    border-radius: 3px;
    cursor: pointer;
  }
  .crumb:hover { background: rgba(74, 144, 217, 0.14); }
  .crumb-sep { color: #888; }

  .browse-list {
    flex: 1;
    overflow-y: auto;
    padding: 6px 10px;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .browse-entry {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    border-radius: 3px;
    font-size: 11.5px;
  }
  .browse-entry.browse-dir { cursor: pointer; }
  .browse-entry.browse-dir:hover { background: #2d2d30; }
  .ent-icon { font-size: 11px; width: 16px; display: inline-block; }
  .ent-name { flex: 1; color: #ccc; }
  .ent-size { font-size: 10px; }
</style>
