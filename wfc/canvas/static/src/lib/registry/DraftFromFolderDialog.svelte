<script lang="ts">
  /**
   * "+ Draft from folder…": make a method folder a draft of a module.
   *
   * The module comes first, the same way registration asks for it; the
   * folder field opens once a module is chosen. Each folder entered is
   * dry-run (nothing written) and the dialog states what it becomes, as the
   * server words it: a DRAFT version of the module's method of the folder's
   * name (registering it adds vN+1), or a DRAFT method (registered as v1).
   * Create draft adds it; a refusal (the method already has a draft from
   * another folder) is shown as sent.
   */
  import LabelTag from '../shared/LabelTag.svelte';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import { addDraft } from './api.js';
  import type { DraftAddResponse, DraftEntry } from './api.js';

  let {
    moduleNames,
    onClose,
    onCreated,
  }: {
    moduleNames: string[];
    onClose: () => void;
    onCreated: (draft: DraftEntry | null) => void;
  } = $props();

  let module = $state('');
  let folder = $state('');
  let preview = $state<DraftAddResponse | null>(null);
  let error = $state<string | null>(null);
  let busy = $state(false);
  // The folder the preview answers for, so a stale answer is not shown.
  let previewedFor = $state('');

  async function check(): Promise<void> {
    const f = folder.trim();
    preview = null;
    error = null;
    if (!module || !f) { previewedFor = ''; return; }
    const key = `${module}\n${f}`;
    previewedFor = key;
    busy = true;
    const res = await addDraft(module, f, true).catch((err) => ({ ok: false as const, error: String(err) }));
    busy = false;
    if (previewedFor !== key) return;
    if (res.ok) preview = res.value;
    else error = res.error;
  }

  async function create(): Promise<void> {
    if (!preview?.ok) return;
    busy = true;
    error = null;
    const res = await addDraft(module, folder.trim(), false).catch((err) => ({ ok: false as const, error: String(err) }));
    busy = false;
    if (!res.ok) { error = res.error; return; }
    if (!res.value.ok) { error = res.value.message; return; }
    onCreated(res.value.draft ?? null);
    onClose();
  }

  const ready = $derived(!!preview?.ok && !busy && previewedFor === `${module}\n${folder.trim()}`);
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="draft-folder-title" tabindex="-1"
       data-testid="draft-folder-dialog"
       onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}>
    <h3 id="draft-folder-title">Draft from folder</h3>
    <label class="lbl" for="draft-module">MODULE</label>
    <div class="modrow">
      <select id="draft-module" data-testid="draft-module" bind:value={module} onchange={() => void check()}>
        <option value="" disabled>Choose a module…</option>
        {#each moduleNames as name (name)}<option value={name}>{name}</option>{/each}
      </select>
      {#if module}
        <LabelTag kind="module" label="MODULE" value={module} color={moduleColor(module)} />
      {/if}
    </div>
    <label class="lbl" for="draft-folder">METHOD FOLDER</label>
    <input id="draft-folder" class="folder" data-testid="draft-folder" type="text"
           placeholder={module ? 'The method folder, e.g. src/my_module/my_method' : 'Choose a module first'}
           disabled={!module} bind:value={folder}
           onchange={() => void check()}
           onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void check(); } }} />
    {#if preview}
      <div class="kind" data-testid="draft-kind-text">
        {preview.message}
        <div class="aside">
          {#if preview.kind === 'version'}
            If the module had no method by that name, the folder would become a DRAFT method, registered as v1.
          {:else}
            If the module had a method by that name, the folder would become its DRAFT version.
          {/if}
        </div>
      </div>
    {:else if busy}
      <div class="kind dim">Reading the folder…</div>
    {/if}
    {#if error}<div class="err" data-testid="draft-folder-error">{error}</div>{/if}
    <div class="foot">
      <button type="button" class="btn" onclick={onClose}>Cancel</button>
      <button type="button" class="btn pri" data-testid="draft-create" disabled={!ready}
              onclick={() => void create()}>Create draft</button>
    </div>
    <div class="cli">CLI: wfc try &lt;method-dir&gt; --module {module || '<module>'} ...</div>
  </div>
</div>

<style>
  .backdrop {
    position: fixed; inset: 0; z-index: 50; display: flex; align-items: flex-start; justify-content: center;
    padding-top: 12vh; background: rgba(0, 0, 0, 0.55);
  }
  .dialog {
    width: 520px; max-width: calc(100vw - 32px); background: #242424; border: 1px solid #444;
    border-radius: 8px; padding: 16px 18px; font-size: 13px; outline: none;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.8);
  }
  h3 { margin: 0 0 12px; font-size: 16px; font-weight: 600; color: #eee; }
  .lbl { display: block; font-size: 10.5px; letter-spacing: 0.08em; color: var(--text-secondary); margin-bottom: 4px; }
  .modrow { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  select, .folder {
    background: #1d1d1d; border: 1px solid #3a3a3a; color: var(--text-primary); border-radius: 4px;
    padding: 6px 10px; font-size: 12.5px;
  }
  select { min-width: 200px; }
  .folder { width: 100%; box-sizing: border-box; font-family: Consolas, monospace; font-size: 11.5px; margin-bottom: 10px; }
  .folder:disabled { opacity: 0.6; }
  .kind {
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: 5px; padding: 8px 10px;
    color: var(--text-primary); font-size: 12.5px; line-height: 1.5;
  }
  .kind .aside { color: var(--text-secondary); }
  .kind.dim { color: var(--text-secondary); }
  .err { margin-top: 8px; color: var(--status-failed-ink); font-size: 12.5px; }
  .foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
  .btn {
    background: transparent; border: 1px solid var(--border); color: var(--text-primary); font-size: 12.5px;
    padding: 5px 12px; border-radius: 4px; cursor: pointer;
  }
  .btn.pri { background: var(--accent); border-color: var(--accent); color: white; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .cli { margin-top: 10px; font-size: 11.5px; color: var(--text-secondary); font-family: Consolas, monospace; }
</style>
