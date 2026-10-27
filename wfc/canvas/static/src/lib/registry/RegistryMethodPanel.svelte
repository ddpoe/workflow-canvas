<script lang="ts">
  /**
   * An open method in the Registry: the shown version's contract beside
   * its code (the version picker and the version's env are in the method's
   * row). The detail lists the main script first, so the code opens on it;
   * the file tabs switch files. A new detail (another version, or a refresh)
   * starts again from its first file.
   *
   * The divider between the contract and the code drags (or, focused, moves
   * with the arrow keys) the contract's share of the width, one share for
   * every open method (`contractWidth.ts`); what the contract cuts follows
   * the width it is given.
   */
  import { untrack } from 'svelte';
  import { highlight } from './shikiHighlight.js';
  import ContractTables from './ContractTables.svelte';
  import { CONTRACT_MAX, CONTRACT_MIN, clampWidth, contractWidth } from './contractWidth.js';
  import type { FileEntry, MethodDetail } from './api.js';

  let {
    detail,
    error = null,
    note = null,
  }: {
    detail: MethodDetail | null;
    error?: string | null;
    note?: string | null;
  } = $props();

  let activeFile = $state<string | null>(null);
  let html = $state<Record<string, string>>({});

  // A new detail starts on its first file (the main script) with nothing
  // highlighted. Only `detail` is tracked: the highlight cache it resets is not.
  $effect(() => {
    const d = detail;
    untrack(() => {
      html = {};
      activeFile = d?.files[0]?.name ?? null;
      if (d?.files[0]) void render(d.files[0]);
    });
  });

  async function render(file: FileEntry): Promise<void> {
    if (html[file.name]) return;
    let out: string;
    try {
      out = await highlight(file.content, file.language);
    } catch {
      const escaped = file.content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      out = `<pre class="shiki"><code>${escaped}</code></pre>`;
    }
    html = { ...html, [file.name]: out };
  }

  function selectFile(file: FileEntry): void {
    activeFile = file.name;
    void render(file);
  }

  // ---- The divider ----

  let splitEl = $state<HTMLDivElement | undefined>();
  let dragging = $state(false);

  function setFromPointer(clientX: number): void {
    if (!splitEl) return;
    const box = splitEl.getBoundingClientRect();
    if (box.width > 0) contractWidth.set(clampWidth(((clientX - box.left) / box.width) * 100));
  }

  function onPointerDown(e: PointerEvent): void {
    e.preventDefault();
    dragging = true;
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  }

  function onPointerMove(e: PointerEvent): void {
    if (dragging) setFromPointer(e.clientX);
  }

  function onPointerUp(e: PointerEvent): void {
    dragging = false;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
  }

  function onKey(e: KeyboardEvent): void {
    const step = e.key === 'ArrowLeft' ? -2 : e.key === 'ArrowRight' ? 2 : 0;
    if (!step) return;
    e.preventDefault();
    contractWidth.update(w => clampWidth(w + step));
  }
</script>

<div class="panel-inner" data-testid="method-panel">
  {#if note}<div class="version-note" data-testid="version-note">{note}</div>{/if}
  {#if error}
    <div class="error">Failed to load: {error}</div>
  {:else if !detail}
    <div class="dim">Loading…</div>
  {:else}
    <div class="split" class:dragging bind:this={splitEl} style="--contract-w: {$contractWidth}%">
      <ContractTables contract={detail.contract} />
      <div class="divider" role="separator" tabindex="0" aria-orientation="vertical"
           aria-label="Contract and code width" aria-valuenow={$contractWidth}
           aria-valuemin={CONTRACT_MIN} aria-valuemax={CONTRACT_MAX} data-testid="split-divider"
           title="Drag to change the contract's width"
           onpointerdown={onPointerDown} onpointermove={onPointerMove}
           onpointerup={onPointerUp} onpointercancel={onPointerUp} onkeydown={onKey}></div>
      <div class="files">
        <div class="file-tabs">
          {#each detail.files as f (f.name)}
            <button type="button" class="file-tab mono" class:active={activeFile === f.name}
                    data-testid="file-tab" onclick={() => selectFile(f)}>{f.name}</button>
          {/each}
        </div>
        {#if activeFile}
          <div class="code-block">
            {#if html[activeFile]}
              {@html html[activeFile]}
            {:else}
              <div class="dim">Highlighting…</div>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .panel-inner { min-width: 0; }
  .version-note { margin-bottom: 8px; font-size: 11px; color: var(--color-running); }
  .error { color: var(--color-failed); font-size: 12px; }
  .dim { color: var(--text-secondary); }
  .mono { font-family: "JetBrains Mono", Consolas, monospace; }
  /* Contract | divider | code: the contract its dragged share (30% to start),
     the code the rest; the contract never runs into the code beside it. */
  .split {
    display: grid; grid-template-columns: minmax(220px, var(--contract-w, 30%)) 14px minmax(0, 1fr);
  }
  .split.dragging { cursor: col-resize; user-select: none; }
  .divider { cursor: col-resize; position: relative; touch-action: none; }
  .divider::after {
    content: ''; position: absolute; top: 0; bottom: 0; left: 6px; width: 2px; border-radius: 1px;
    background: transparent; transition: background 0.12s;
  }
  .divider:hover::after, .divider:focus-visible::after, .dragging .divider::after { background: var(--accent); }
  .divider:focus-visible { outline: none; }
  /* The code box fills the height of the contract beside it, however short its file. */
  .files { min-width: 0; display: flex; flex-direction: column; }
  .file-tabs {
    display: flex; flex-wrap: wrap; gap: 2px; padding: 3px; background: var(--bg-panel);
    border-radius: 4px; margin-bottom: 8px; max-width: 100%; width: max-content;
  }
  .file-tab {
    padding: 4px 10px; font-size: 10.5px; color: var(--text-secondary);
    background: none; border: none; border-radius: 3px; cursor: pointer; overflow-wrap: anywhere;
  }
  .file-tab.active { background: var(--accent); color: white; }
  .code-block {
    flex: 1 1 auto; min-height: 120px; max-height: 640px;
    background: var(--bg-canvas); border: 1px solid var(--border); border-radius: 4px;
    overflow: auto;
  }
  /* Shiki outputs its own <pre class="shiki"> with inline colors. */
  .code-block :global(pre.shiki) {
    margin: 0; padding: 10px 14px; font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 11.5px; line-height: 1.5; background: transparent !important;
  }
  .code-block :global(pre.shiki code) { font-family: inherit; font-size: inherit; }
  @media (max-width: 900px) {
    .split { grid-template-columns: minmax(0, 1fr); row-gap: 14px; }
    .divider { display: none; }
  }
</style>
