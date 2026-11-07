<script lang="ts">
  /**
   * The artifact viewer: one dialog for every viewable kind (image, table,
   * JSON, text, PDF), stepping through the viewable files of one folder or
   * of the top level.
   *
   * The artifact list hands over the files to step through (download-only
   * files already left out) and the one to open. ‹ › , ← → and the strip
   * step; Esc closes. Focus is trapped inside while it is open and the list
   * puts it back on the row when it closes. Image and PDF have Full screen
   * (F), on the viewer box, so it is kept while stepping. The image body
   * owns its zoom; the header holds its controls, and keys + − 0 reach it.
   * An image too large to preview, or unreadable, has no zoom and no Full
   * screen.
   * Only the next file, when it is an image, is fetched ahead.
   */
  import { onMount, untrack } from 'svelte';
  import { artifactDownloadUrl, artifactImageUrl, prefetchArtifactImage } from './historyApi.js';
  import { formatBytes } from './historyUtils.js';
  import type { ViewerItem } from './viewerFormat.js';
  import ViewerImage from './ViewerImage.svelte';
  import ViewerTable from './ViewerTable.svelte';
  import ViewerDocument from './ViewerDocument.svelte';

  interface Props {
    runId: string;
    /** The folder the files are in (`qc_plots/`), or '' for the top level. */
    folder: string;
    items: ViewerItem[];
    /** The index of the file to open. */
    start: number;
    onClose: () => void;
  }

  let { runId, folder, items, start, onClose }: Props = $props();

  let index = $state(untrack(() => start));
  let wrap = $state(false);
  let jsonRaw = $state(false);
  let zoomLabel = $state('Fit');
  let fullscreen = $state(false);
  let box = $state<HTMLDivElement | null>(null);
  let closeBtn = $state<HTMLButtonElement | null>(null);
  let strip = $state<HTMLDivElement | null>(null);
  /** The image body's zoom controls (its exported functions). */
  let imageView = $state<{ zoomIn(): void; zoomOut(): void; fit(): void; actualSize(): void } | null>(null);

  /** Whether the image body has an image to zoom: false when it is too large or unreadable. */
  let imageShows = $state(true);

  let item = $derived(items[index]);
  let multi = $derived(items.length > 1);
  let imageControls = $derived(item.viewer === 'image' && imageShows);
  let canFullscreen = $derived(imageControls || item.viewer === 'pdf');

  function step(delta: number): void {
    const next = index + delta;
    if (next < 0 || next >= items.length) return;
    index = next;
  }

  function goTo(i: number): void {
    index = i;
  }

  function toggleFullscreen(): void {
    if (!box) return;
    if (document.fullscreenElement === box) void document.exitFullscreen?.();
    else void box.requestFullscreen?.();
  }

  // The next file, when it is an image, so stepping to it does not wait.
  $effect(() => {
    const next = items[index + 1];
    if (next?.viewer === 'image') prefetchArtifactImage(runId, next.path, 'view');
  });

  // Keep the current chip in sight.
  $effect(() => {
    void index;
    const chip = strip?.querySelector('.chip.on') as HTMLElement | null;
    chip?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  });

  onMount(() => {
    closeBtn?.focus();
    return () => {
      if (box && document.fullscreenElement === box) void document.exitFullscreen?.();
    };
  });

  const FOCUSABLE = 'a[href], button:not([disabled]), input, select, iframe, [tabindex]:not([tabindex="-1"])';

  /** Keep Tab inside the dialog: past the last control it wraps to the first. */
  function trapTab(e: KeyboardEvent): void {
    if (!box) return;
    const els = [...box.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (els.length === 0) { e.preventDefault(); return; }
    const first = els[0];
    const last = els[els.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (!active || !box.contains(active)) {
      e.preventDefault();
      first.focus();
    } else if (e.shiftKey && (active === first || active === box)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === 'Tab') {
      trapTab(e);
      return;
    }
    const tag = (e.target as HTMLElement | null)?.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    else if ((e.key === 'f' || e.key === 'F') && canFullscreen) { e.preventDefault(); toggleFullscreen(); }
    else if (imageControls && imageView) {
      if (e.key === '+' || e.key === '=') { e.preventDefault(); imageView.zoomIn(); }
      else if (e.key === '-') { e.preventDefault(); imageView.zoomOut(); }
      else if (e.key === '0') { e.preventDefault(); imageView.fit(); }
    }
  }

  function chipLabel(it: ViewerItem): string {
    if (it.viewer === 'json') return '{ }';
    return (it.extension || it.viewer).toUpperCase();
  }
</script>

<svelte:window onkeydown={onKeydown} />
<svelte:document onfullscreenchange={() => { fullscreen = !!box && document.fullscreenElement === box; }} />

<!-- A click on the backdrop closes; keys are the window handler's (Esc). -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="overlay" role="presentation" data-testid="artifact-viewer-overlay" onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
  <div
    class="viewer"
    role="dialog"
    aria-modal="true"
    aria-label={`${folder}${item.name}`}
    tabindex="-1"
    bind:this={box}
    data-testid="artifact-viewer"
  >
    <div class="v-head">
      {#if folder}<span class="v-path" data-testid="viewer-path">{folder}</span>{/if}
      <span class="v-name" data-testid="viewer-name">{item.name}</span>
      <span class="v-meta">· {formatBytes(item.size)}</span>
      <span class="v-count" data-testid="viewer-count">{index + 1} / {items.length}</span>
      <span class="v-extra">
        {#if imageControls}
          <span class="zoom">
            <button class="v-btn" title="Zoom out (−)" aria-label="Zoom out" onclick={() => imageView?.zoomOut()}>−</button>
            <span class="zl" data-testid="viewer-zoom">{zoomLabel}</span>
            <button class="v-btn" title="Zoom in (+)" aria-label="Zoom in" onclick={() => imageView?.zoomIn()}>+</button>
            <button class="v-btn" class:sel={zoomLabel === 'Fit'} title="Fit to window (0)" onclick={() => imageView?.fit()}>Fit</button>
            <button class="v-btn" class:sel={zoomLabel === '100%'} title="Actual size" onclick={() => imageView?.actualSize()}>1:1</button>
          </span>
        {:else if item.viewer === 'json'}
          <button class="v-btn" class:sel={!jsonRaw} aria-pressed={!jsonRaw} onclick={() => { jsonRaw = false; }}>Tree</button>
          <button class="v-btn" class:sel={jsonRaw} aria-pressed={jsonRaw} onclick={() => { jsonRaw = true; }}>Raw</button>
        {:else if item.viewer === 'text'}
          <button class="v-btn" class:sel={wrap} aria-pressed={wrap} onclick={() => { wrap = !wrap; }}>Wrap</button>
        {/if}
        {#if canFullscreen}
          <button class="v-btn" title="Full screen (F)" data-testid="viewer-fullscreen" onclick={toggleFullscreen}>
            {fullscreen ? '⤡ Exit full screen' : '⤢ Full screen'}
          </button>
        {/if}
      </span>
      <a class="v-btn" href={artifactDownloadUrl(runId, item.path)} download={item.name}>⤓ Download</a>
      <button class="v-btn" aria-label="Close" bind:this={closeBtn} onclick={onClose}>✕</button>
    </div>

    <div class="v-main">
      {#if multi}
        <button class="nav prev" aria-label="Previous file" disabled={index === 0} onclick={() => step(-1)}>‹</button>
      {/if}
      <div class="v-content">
        {#key item.path}
          {#if item.viewer === 'image'}
            <ViewerImage {runId} path={item.path} name={item.name} bind:zoomLabel bind:shows={imageShows} bind:this={imageView} />
          {:else if item.viewer === 'table'}
            <ViewerTable {runId} path={item.path} />
          {:else if item.viewer === 'json' || item.viewer === 'text'}
            <ViewerDocument {runId} path={item.path} viewer={item.viewer} extension={item.extension} {wrap} {jsonRaw} />
          {:else if item.viewer === 'pdf'}
            <iframe class="pdf" title={item.name} src={artifactDownloadUrl(runId, item.path)} data-testid="viewer-pdf"></iframe>
          {/if}
        {/key}
      </div>
      {#if multi}
        <button class="nav next" aria-label="Next file" disabled={index === items.length - 1} onclick={() => step(1)}>›</button>
      {/if}
    </div>

    {#if multi}
      <div class="strip" bind:this={strip} data-testid="viewer-strip">
        {#each items as it, i (it.path)}
          <button
            class="chip"
            class:on={i === index}
            aria-label={`Show ${it.name}`}
            aria-current={i === index ? 'true' : undefined}
            onclick={() => goTo(i)}
          >
            {#if it.viewer === 'image'}
              <img src={artifactImageUrl(runId, it.path, 'thumb')} alt="" loading="lazy" />
            {:else}
              <span class="big" style="color: {it.color};">{chipLabel(it)}</span>
            {/if}
            <span class="k">{it.name}</span>
          </button>
        {/each}
        <span class="hint">← → · Esc</span>
      </div>
    {/if}
  </div>
</div>

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.74);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }
  .viewer {
    width: min(1100px, 90vw);
    height: min(760px, 88vh);
    background: var(--bg-panel);
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
    outline: none;
  }
  .viewer:fullscreen { width: 100vw; height: 100vh; border: none; }

  .v-head {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-header);
    min-width: 0;
  }
  .v-name {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }
  .v-path {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    color: var(--text-muted);
    margin-right: -10px;
    white-space: nowrap;
  }
  .v-meta {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10px;
    color: var(--text-muted);
    white-space: nowrap;
  }
  .v-count {
    margin-left: auto;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    color: var(--text-secondary);
    white-space: nowrap;
  }
  .v-extra { display: flex; gap: 4px; align-items: center; }
  .v-btn {
    background: none;
    border: 1px solid var(--border);
    color: var(--text-primary);
    border-radius: 3px;
    padding: 3px 9px;
    font-size: 11px;
    font-family: inherit;
    cursor: pointer;
    white-space: nowrap;
    text-decoration: none;
  }
  .v-btn:hover { background: #3a3a3d; }
  .v-btn.sel {
    border-color: var(--accent);
    color: #fff;
    background: color-mix(in oklab, var(--accent) 22%, transparent);
  }
  .zoom { display: flex; align-items: center; gap: 3px; margin-right: 6px; }
  .zoom .v-btn { padding: 3px 7px; }
  .zl {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    color: var(--text-primary);
    min-width: 40px;
    text-align: center;
  }

  .v-main { flex: 1; position: relative; min-height: 0; display: flex; }
  .v-content { flex: 1; overflow: auto; min-width: 0; }
  .nav {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    width: 36px;
    height: 58px;
    background: rgba(30, 30, 30, 0.82);
    border: 1px solid var(--border);
    color: #eee;
    font-size: 22px;
    cursor: pointer;
    z-index: 2;
    border-radius: 4px;
  }
  .nav:hover { background: var(--accent); }
  .nav.prev { left: 8px; }
  .nav.next { right: 8px; }
  .nav:disabled { opacity: 0.25; cursor: default; background: rgba(30, 30, 30, 0.82); }

  .pdf { width: 100%; height: 100%; border: none; background: #525659; display: block; }

  .strip {
    display: flex;
    gap: 6px;
    padding: 7px 12px;
    border-top: 1px solid var(--border);
    background: var(--bg-header);
    overflow-x: auto;
  }
  .chip {
    flex: none;
    width: 76px;
    height: 56px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    padding: 3px;
    font: inherit;
  }
  .chip img { max-width: 100%; max-height: 36px; background: #fcfcfb; }
  .chip .k {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 8.5px;
    color: var(--text-secondary);
    max-width: 70px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chip .big { font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; font-weight: 700; }
  .chip.on { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  .hint {
    font-size: 10px;
    color: var(--text-muted);
    align-self: center;
    margin-left: auto;
    flex: none;
    padding-left: 6px;
  }
</style>
