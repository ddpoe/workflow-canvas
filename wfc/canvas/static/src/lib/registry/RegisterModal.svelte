<script lang="ts">
  /**
   * RegisterModal — one modal handling module / method / sample registration.
   *
   * The Module and Sample kinds have a small form, a "Dry run" button (POSTs
   * ?dryRun=true and renders preChecks[]) and a "Register" button (POSTs
   * without dryRun; on success → onRegistered callback + close).
   *
   * The Method kind starts with a one-line toggle: New method (the default)
   * or New method version. New method asks for the folder, the module and an
   * optional name; given a name its module already has, it says so and
   * offers to switch to New method version with the choices kept. New
   * method version asks Module → Method → Folder, each field enabled once
   * the one above is chosen, and reads `now version | v2  next version | v3`
   * once the method is chosen; opened from a method's ⋯ menu it starts with
   * both chosen.
   *
   * One button, check then confirm: the button names the action (Register
   * method / Register version) and runs the check, the registration's dry
   * run, which writes nothing. Problems come back as refusals in red, each
   * saying what wfc expects, and the button becomes Check again. A clean
   * folder comes back as the confirm card (`RegisterConfirmCard.svelte`)
   * and the button reads Confirm: register vN; the same content as the
   * newest version reads "nothing to register" with only Close. Confirm
   * sends the plan's token back, so the server applies exactly what was
   * checked; a folder changed in between (409) is checked again. ← Back
   * returns to the form with every choice kept; × and Escape close the
   * modal from every state, writing nothing.
   *
   * Envs kind is not supported — there is no env register endpoint.
   */

  import { browseFilesystem, registerRecord } from './api.js';
  import type { FsEntry, MethodRow, RegisterKind, RegistrationPlan } from './api.js';
  import LabelTag from '../shared/LabelTag.svelte';
  import { getModuleColor } from '../history/historyUtils.js';
  import RegisterConfirmCard from './RegisterConfirmCard.svelte';

  type Kind = RegisterKind;
  type PreCheck = { status: 'ok' | 'warn' | 'fail'; label: string; detail?: string };
  type Mode = 'new' | 'version';
  type Step = 'form' | 'checking' | 'refused' | 'confirm' | 'same' | 'applying';

  let {
    initialKind,
    moduleNames = [],
    methods = [],
    initialTarget = null,
    registeredMethodDirs = [],
    onClose,
    onRegistered,
  }: {
    initialKind: Kind;
    moduleNames?: string[];
    /** The registered methods with their versions, for New method version. */
    methods?: MethodRow[];
    /** Opens on New method version with this method chosen. */
    initialTarget?: { module: string; method: string } | null;
    registeredMethodDirs?: string[];
    onClose: () => void;
    onRegistered: (kind: Kind, record: any) => void;
  } = $props();

  let kind = $state<Kind>(initialKind);

  // Per-kind form state (kept alive across kind-switches within the modal).
  let moduleForm = $state({ name: '', description: '' });
  let methodForm = $state({ directory: '', module: '', method_name: '' });
  let versionForm = $state({
    module: initialTarget?.module ?? '',
    method: initialTarget?.method ?? '',
    directory: '',
  });
  let sampleForm = $state({ name: '', source: '' });
  let mode = $state<Mode>(initialTarget ? 'version' : 'new');

  // Module and sample kinds: their pre-checks and error.
  let preChecks = $state<PreCheck[]>([]);
  let serverError = $state<string | null>(null);
  let submitting = $state(false);

  // Method kind: where the check is, and what it found.
  let step = $state<Step>('form');
  let plan = $state<RegistrationPlan | null>(null);
  let issues = $state<PreCheck[]>([]);
  let notice = $state<string | null>(null);

  // ---- Method kind: the form ----

  function baseName(path: string): string {
    const parts = path.replace(/\\/g, '/').replace(/\/+$/, '').split('/');
    return parts[parts.length - 1] ?? '';
  }

  /** The method a New method registration would name. */
  const newName = $derived(methodForm.method_name.trim() || baseName(methodForm.directory.trim()));
  /** The registered method of that name in the chosen module, if any. */
  const taken = $derived(methodForm.module && newName
    ? methods.find(m => m.module === methodForm.module && m.name === newName) ?? null
    : null);

  function newestLabel(m: MethodRow | null | undefined): number | null {
    const versions = m?.versions ?? [];
    return (versions.find(v => v.newest) ?? versions[versions.length - 1])?.label ?? null;
  }

  const versionModules = $derived(
    [...new Set([...moduleNames, ...methods.map(m => m.module)])]
      .filter(name => methods.some(m => m.module === name)));
  const moduleMethods = $derived(methods.filter(m => m.module === versionForm.module));
  const chosenMethod = $derived(
    moduleMethods.find(m => m.name === versionForm.method) ?? null);
  const nowLabel = $derived(newestLabel(chosenMethod));

  function switchToVersion() {
    versionForm = { module: methodForm.module, method: newName, directory: methodForm.directory };
    mode = 'version';
  }

  function setMode(next: Mode) {
    if (step !== 'form') return;
    mode = next;
  }

  /** The module, method and folder the method kind would register. */
  const target = $derived(mode === 'new'
    ? { module: methodForm.module.trim(), method: newName, directory: methodForm.directory.trim(),
        method_name: methodForm.method_name.trim() || null }
    : { module: versionForm.module, method: versionForm.method, directory: versionForm.directory.trim(),
        method_name: versionForm.method || null });

  const formReady = $derived(mode === 'new'
    ? !!(target.directory && target.module)
    : !!(target.module && target.method && target.directory));

  // ---- Method kind: check, then confirm ----

  function methodBody(token: string | null) {
    return {
      directory: target.directory,
      module: target.module,
      method_name: target.method_name,
      ...(token ? { plan_token: token } : {}),
    };
  }

  async function check(note: string | null = null) {
    step = 'checking';
    plan = null;
    issues = [];
    notice = note;
    try {
      const { ok, status, body } = await registerRecord('method', methodBody(null), true);
      const failed = (body.preChecks ?? []).filter((c: PreCheck) => c.status === 'fail');
      if (!ok || !body.ok) {
        issues = failed.length > 0 ? failed
          : [{ status: 'fail', label: 'Check refused', detail: body.detail ?? `HTTP ${status}` }];
        step = 'refused';
        return;
      }
      plan = body.plan as RegistrationPlan;
      step = plan.outcome === 'same' ? 'same' : 'confirm';
    } catch (err) {
      issues = [{ status: 'fail', label: 'Check failed', detail: String(err) }];
      step = 'refused';
    }
  }

  async function confirm() {
    if (!plan) return;
    step = 'applying';
    try {
      const { ok, status, body } = await registerRecord('method', methodBody(plan.token), false);
      if (ok && body.ok) {
        onRegistered('method', body);
        onClose();
        return;
      }
      if (status === 409) {
        // The folder changed since the check: check it again.
        await check('The folder changed since it was checked, so it was checked again.');
        return;
      }
      issues = [{ status: 'fail', label: 'Registration refused', detail: body.detail ?? `HTTP ${status}` }];
      step = 'refused';
    } catch (err) {
      issues = [{ status: 'fail', label: 'Registration failed', detail: String(err) }];
      step = 'refused';
    }
  }

  function back() {
    step = 'form';
    plan = null;
    issues = [];
    notice = null;
  }

  function primary() {
    if (step === 'confirm') void confirm();
    else if (step === 'same') onClose();
    else void check();
  }

  const primaryLabel = $derived(
    step === 'confirm' && plan ? `Confirm: register v${plan.label}`
    : step === 'same' ? 'Close'
    : step === 'refused' ? 'Check again'
    : step === 'checking' ? 'Checking…'
    : step === 'applying' ? 'Registering…'
    : mode === 'new' ? 'Register method' : 'Register version');

  /** A refusal's headline: the check that refused, in words. */
  function refusalTitle(c: PreCheck): string {
    if (c.label === 'registration plan') return 'This folder cannot be registered as it is';
    return c.label.charAt(0).toUpperCase() + c.label.slice(1);
  }

  /** A refusal's text, its `backticked` parts as code. */
  function codeParts(text: string): Array<{ code: boolean; text: string }> {
    return text.split('`').map((t, i) => ({ code: i % 2 === 1, text: t }));
  }

  // ---- Module and sample kinds ----

  function bodyFor(k: Kind): any {
    if (k === 'module') {
      return { name: moduleForm.name, description: moduleForm.description || null, contracts: [] };
    }
    return { name: sampleForm.name, source: sampleForm.source, registration_mode: 'copy' };
  }

  async function post(k: Kind, dryRun: boolean) {
    submitting = true;
    serverError = null;
    try {
      const { ok, status, body } = await registerRecord(k, bodyFor(k), dryRun);
      if (!ok) {
        serverError = body.detail ?? `HTTP ${status}`;
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

  // ---- Directory browse ----

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
      const listing = await browseFilesystem(path);
      if (!listing.ok) {
        browseError = listing.error;
        return;
      }
      browsePath = listing.path;
      // A new method's listing drops already-registered method dirs; a new
      // version is registered from just such a dir, so its listing keeps them.
      const registered = new Set(mode === 'new' ? registeredMethodDirs : []);
      browseEntries = listing.entries.filter((e: FsEntry) => {
        const full = listing.path ? `${listing.path}/${e.name}` : e.name;
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
    if (mode === 'version') versionForm.directory = browsePath;
    else methodForm.directory = browsePath;
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

  function onKeydown(e: KeyboardEvent) {
    if (e.key !== 'Escape') return;
    if (browseOpen) { browseOpen = false; return; }
    onClose();
  }

  function stop(e: Event) { e.stopPropagation(); }
</script>

<svelte:window onkeydown={onKeydown} />

{#snippet folderField(value: string, disabled: boolean, set: (v: string) => void)}
  <label class="field">
    <span class="label-text">Folder <span class="req">*</span></span>
    <div class="input-row">
      <input class="input mono" type="text" {value} {disabled} data-testid="register-folder"
        oninput={(e) => set((e.currentTarget as HTMLInputElement).value)}
        placeholder="the method's folder, holding its method.yaml" />
      <button type="button" class="btn-ghost-small" {disabled} onclick={openBrowse}>Browse…</button>
    </div>
  </label>
{/snippet}

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="overlay" onclick={onClose}>
  <div class="modal" class:wide={kind === 'method'} onclick={stop} data-testid="register-modal">
    <div class="modal-header">
      <div class="kind-switcher">
        <button class="kind-btn" class:active={kind === 'module'} onclick={() => (kind = 'module')}>Module</button>
        <button class="kind-btn" class:active={kind === 'method'} onclick={() => (kind = 'method')}>Method</button>
        <button class="kind-btn" class:active={kind === 'sample'} onclick={() => (kind = 'sample')}>Sample</button>
      </div>
      <button class="close-x" data-testid="register-close" onclick={onClose} title="Close (Esc)" aria-label="Close">×</button>
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
        <div class="modes" role="group" aria-label="What you are registering" data-testid="register-mode">
          <button type="button" class="mode" class:on={mode === 'new'} aria-pressed={mode === 'new'}
                  disabled={step !== 'form' && mode !== 'new'} data-testid="mode-new"
                  onclick={() => setMode('new')}>New method</button>
          <button type="button" class="mode" class:on={mode === 'version'} aria-pressed={mode === 'version'}
                  disabled={step !== 'form' && mode !== 'version'} data-testid="mode-version"
                  onclick={() => setMode('version')}>New method version</button>
        </div>

        {#if step === 'form'}
          {#if mode === 'new'}
            {@render folderField(methodForm.directory, false, v => { methodForm.directory = v; })}
            <label class="field">
              <span class="label-text">Module <span class="req">*</span></span>
              {#if moduleNames.length > 0}
                <select class="input mono" bind:value={methodForm.module} data-testid="register-module">
                  <option value="" disabled selected={!methodForm.module}>— select module —</option>
                  {#each moduleNames as name}
                    <option value={name}>{name}</option>
                  {/each}
                </select>
              {:else}
                <input class="input mono" type="text" bind:value={methodForm.module} placeholder="test_pipeline"
                       data-testid="register-module" />
              {/if}
            </label>
            <label class="field">
              <span class="label-text">Method name <span class="dim">(optional)</span></span>
              <input class="input mono" type="text" bind:value={methodForm.method_name}
                data-testid="register-method-name" placeholder="the folder's name" />
            </label>
            {#if taken}
              <div class="taken" data-testid="register-taken">
                <span><b>{methodForm.module}</b> already has <b>{taken.name}</b>, latest v{newestLabel(taken)}.</span>
                <button type="button" class="btn-ghost-small" data-testid="switch-to-version"
                        onclick={switchToVersion}>Register a new version instead</button>
              </div>
            {/if}
          {:else}
            <label class="field">
              <span class="label-text">Module <span class="req">*</span></span>
              <select class="input mono" data-testid="register-module"
                      value={versionForm.module}
                      onchange={(e) => { versionForm = { ...versionForm, module: (e.currentTarget as HTMLSelectElement).value, method: '' }; }}>
                <option value="" disabled selected={!versionForm.module}>— select module —</option>
                {#each versionModules as name}
                  <option value={name}>{name}</option>
                {/each}
              </select>
            </label>
            <label class="field">
              <span class="label-text">Method <span class="req">*</span></span>
              <select class="input mono" data-testid="register-method" disabled={!versionForm.module}
                      bind:value={versionForm.method}>
                <option value="" disabled selected={!versionForm.method}>— select method —</option>
                {#each moduleMethods as m (m.name)}
                  <option value={m.name}>{m.name} · v{newestLabel(m) ?? '?'}</option>
                {/each}
              </select>
              {#if chosenMethod && nowLabel !== null}
                <span class="now" data-testid="register-now-next">
                  now <LabelTag kind="version" label="version" value={`v${nowLabel}`} />
                  next <LabelTag kind="version" label="version" value={`v${nowLabel + 1}`} />
                </span>
              {/if}
            </label>
            {@render folderField(versionForm.directory, !versionForm.method, v => { versionForm.directory = v; })}
          {/if}
        {:else}
          <div class="target" data-testid="register-target">
            {#if target.module}<LabelTag kind="module" label="MODULE" value={target.module} color={getModuleColor(target.module)} />{/if}
            {#if target.method}<LabelTag kind="method" label="method" value={target.method} />{/if}
            <span class="mono dim folder">{target.directory}</span>
          </div>
          {#if notice}<div class="notice" data-testid="register-notice">{notice}</div>{/if}
          {#if step === 'checking' || step === 'applying'}
            <div class="dim">{step === 'checking' ? 'Checking the folder…' : 'Registering…'}</div>
          {:else if step === 'refused'}
            <div class="issues" data-testid="register-refusals">
              {#each issues as c}
                <div class="issue" data-testid="register-refusal">
                  <div class="ihd"><span class="ic">✕</span><b>{refusalTitle(c)}</b></div>
                  {#if c.detail}
                    <div class="idetail">{#each codeParts(c.detail) as part}{#if part.code}<code>{part.text}</code>{:else}{part.text}{/if}{/each}</div>
                  {/if}
                </div>
              {/each}
              <p class="hint">Nothing was written. Fix the folder, then Check again.</p>
            </div>
          {:else if step === 'confirm' && plan}
            {#key plan.token}
              <RegisterConfirmCard {plan} module={target.module} method={target.method} />
            {/key}
          {:else if step === 'same' && plan}
            <div class="same" data-testid="register-same">
              <b>Same as v{plan.label}</b><span class="dim">nothing to register</span>
            </div>
          {/if}
        {/if}

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

      {#if kind !== 'method'}
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
      {/if}
    </div>

    <div class="modal-footer">
      {#if kind === 'method'}
        {#if step === 'refused' || step === 'confirm' || step === 'same'}
          <button class="btn btn-back" data-testid="register-back" onclick={back}>← Back</button>
        {/if}
        <div class="spacer"></div>
        {#if step !== 'same'}
          <button class="btn btn-ghost" onclick={onClose}>Cancel</button>
        {/if}
        <button class="btn btn-primary" data-testid="register-primary"
          disabled={step === 'checking' || step === 'applying' || (step === 'form' && !formReady)}
          onclick={primary}>{primaryLabel}</button>
      {:else}
        <button class="btn btn-ghost" disabled={submitting} onclick={() => post(kind, true)}>Dry run</button>
        <div class="spacer"></div>
        <button class="btn btn-ghost" onclick={onClose}>Cancel</button>
        <button class="btn btn-primary" disabled={submitting}
          onclick={() => post(kind, false)}>Register {kind}</button>
      {/if}
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
        <button class="close-x" onclick={() => (browseOpen = false)} aria-label="Close">×</button>
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
    max-width: calc(100vw - 32px);
    max-height: 84vh;
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.5);
    display: flex;
    flex-direction: column;
  }
  .modal.wide { width: 680px; }
  /* The header bar holds the kinds and the ×; it never scrolls or overlaps
     the body. */
  .modal-header {
    display: flex;
    align-items: center;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
    gap: 10px;
    flex-shrink: 0;
  }
  .kind-switcher {
    display: flex;
    gap: 2px;
    background: var(--bg-input);
    padding: 3px;
    border-radius: 4px;
  }
  .kind-btn {
    padding: 5px 14px;
    color: var(--text-secondary);
    font-size: 12px;
    background: none;
    border: none;
    border-radius: 3px;
    cursor: pointer;
  }
  .kind-btn.active { background: var(--accent); color: white; }
  .close-x {
    margin-left: auto;
    flex-shrink: 0;
    background: none;
    border: none;
    color: var(--text-secondary);
    font-size: 18px;
    line-height: 1;
    cursor: pointer;
    padding: 0 4px;
  }
  .close-x:hover { color: var(--text-primary); }

  .modal-body {
    padding: 16px 18px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
    color: var(--text-primary);
    min-height: 0;
  }

  .modes {
    display: grid; grid-template-columns: 1fr 1fr; border: 1px solid var(--border);
    border-radius: 4px; overflow: hidden; flex-shrink: 0;
  }
  .mode {
    padding: 3px 10px; font-size: 11.5px; line-height: 16px; text-align: center;
    color: var(--text-secondary); background: var(--bg-input); border: none; cursor: pointer;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .mode + .mode { border-left: 1px solid var(--border); }
  .mode.on { background: color-mix(in srgb, var(--accent) 22%, var(--bg-panel)); color: white; }
  .mode:disabled { cursor: default; }

  .field { display: flex; flex-direction: column; gap: 4px; }
  .label-text { font-size: 11px; color: #e6e6e6; font-weight: 500; }
  .req { color: var(--color-failed); margin-left: 2px; }

  .input {
    background: var(--bg-input);
    border: 1px solid var(--border);
    color: var(--text-primary);
    font-size: 12.5px;
    padding: 6px 10px;
    border-radius: 4px;
    outline: none;
    font-family: "Mukta Vaani", system-ui, sans-serif;
    min-width: 0;
  }
  .input.mono { font-family: "JetBrains Mono", Consolas, monospace; }
  .input:focus { border-color: var(--accent); }
  .input:disabled { opacity: 0.5; }
  textarea.input { resize: vertical; }

  .now { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; font-size: 11px; color: var(--text-secondary); }
  .taken {
    display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 8px 10px; border-radius: 5px;
    border: 1px solid color-mix(in srgb, #7ea893 50%, transparent);
    background: color-mix(in srgb, #7ea893 10%, transparent); font-size: 11.5px;
  }
  .taken b { color: #e6e6e6; font-weight: 600; }
  .target { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; min-width: 0; }
  .folder { overflow-wrap: anywhere; min-width: 0; font-size: 11.5px; }
  .notice { color: var(--color-running); font-size: 11.5px; }

  .issues { display: flex; flex-direction: column; gap: 8px; }
  .issue {
    border-radius: 5px; padding: 8px 10px; border: 1px solid rgba(231, 76, 60, 0.45);
    background: rgba(231, 76, 60, 0.07); min-width: 0;
  }
  .ihd { display: flex; gap: 8px; align-items: baseline; }
  .ic { color: var(--color-failed); font-weight: 700; font-size: 12px; }
  .ihd b { color: #e6e6e6; font-weight: 600; }
  .idetail { margin: 4px 0 0 18px; font-size: 11.5px; line-height: 1.45; overflow-wrap: anywhere; }
  .idetail code { background: var(--bg-canvas); border: 1px solid var(--border); color: #7ec699; }
  .same {
    display: flex; gap: 8px; align-items: baseline; padding: 7px 10px; border: 1px solid var(--border);
    border-radius: 5px; background: color-mix(in srgb, var(--color-running) 8%, var(--bg-input));
  }
  .same b { color: var(--color-running); font-weight: 600; }

  .hint { font-size: 10.5px; color: var(--text-secondary); margin: 0; }
  .hint-inline { font-size: 10.5px; color: var(--text-secondary); }
  code { background: var(--bg-input); padding: 1px 4px; border-radius: 2px; font-size: 10.5px; }

  .error {
    background: rgba(231, 76, 60, 0.14);
    border: 1px solid rgba(231, 76, 60, 0.35);
    color: var(--color-failed);
    padding: 6px 10px;
    border-radius: 3px;
    font-size: 11.5px;
  }

  .checks {
    border-top: 1px solid var(--border);
    padding-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .section-label {
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    color: var(--text-secondary);
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
  .check-ok .dot   { background: var(--color-completed); }
  .check-warn .dot { background: var(--color-running); }
  .check-fail .dot { background: var(--color-failed); }
  .check-label { color: var(--text-primary); }
  .check-detail { font-size: 10.5px; }

  .modal-footer {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    border-top: 1px solid var(--border);
    flex-shrink: 0;
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
  .btn-ghost { background: transparent; color: var(--text-primary); border: 1px solid var(--border); }
  .btn-ghost:hover:not(:disabled) { background: var(--bg-header); }
  .btn-back { background: none; color: var(--text-secondary); padding: 6px 4px; }
  .btn-back:hover { color: var(--text-primary); }
  .btn-primary { background: var(--accent); color: white; font-weight: 500; }
  .btn-primary:hover:not(:disabled) { background: #5aa0e9; }
  .dim { color: var(--text-secondary); }
  .mono { font-family: "JetBrains Mono", Consolas, monospace; }

  .input-row {
    display: flex;
    gap: 6px;
    align-items: stretch;
  }
  .input-row .input { flex: 1; }
  .btn-ghost-small {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-primary);
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    white-space: nowrap;
  }
  .btn-ghost-small:hover:not(:disabled) { background: var(--bg-header); }
  .btn-ghost-small:disabled { opacity: 0.5; cursor: not-allowed; }

  select.input {
    background: var(--bg-input);
    padding: 6px 10px;
    cursor: pointer;
  }
  select.input option { background: var(--bg-panel); color: var(--text-primary); }

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
    color: var(--accent);
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 11.5px;
    padding: 2px 4px;
    border-radius: 3px;
    cursor: pointer;
  }
  .crumb:hover { background: rgba(74, 144, 217, 0.14); }
  .crumb-sep { color: var(--text-secondary); }

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
  .browse-entry.browse-dir:hover { background: var(--bg-header); }
  .ent-icon { font-size: 11px; width: 16px; display: inline-block; }
  .ent-name { flex: 1; color: var(--text-primary); }
  .ent-size { font-size: 10px; }
</style>
