<script lang="ts">
  /**
   * The run detail panel's Artifacts tab: artifacts grouped by category
   * under collapsible headers, one row per file, image previews on demand.
   *
   * The panel owns fetching; this component owns the grouping, which folders
   * and previews are open, and which file the viewer shows. Category collapse
   * is the session store (`artifactCollapse.ts`), shared by every run. What a
   * row offers keys off the server's `viewer` field only: an image row has a
   * preview caret, a viewable row's name opens the viewer, a file with no
   * viewer is download only (a ringed download icon). Outputs wfc found
   * changed after the run wrote them are listed first as unavailable, with no
   * link: they are never served.
   */
  import {
    artifactDownloadUrl,
    artifactImageUrl,
    deriveArtifactType,
    fetchImageInfo,
  } from './historyApi.js';
  import type { Artifact, ArtifactChild, ViewerKind } from './historyApi.js';
  import { formatBytes } from './historyUtils.js';
  import { tooLargeText, type ViewerItem } from './viewerFormat.js';
  import ArtifactViewer from './ArtifactViewer.svelte';
  import {
    categoryOf,
    CHANGED_OUTPUT_TEXT,
    pillKind,
    splitNameForCut,
    type CatKey,
    type PillKind,
  } from './runDetailFormat.js';
  import { collapsedCategories, toggleCategory } from './artifactCollapse.js';

  interface Props {
    runId: string;
    artifacts: Artifact[];
    artifactState: 'idle' | 'loading' | 'done';
    /** Names of the run's outputs that changed after the run wrote them. */
    changedOutputs?: string[];
  }

  let { runId, artifacts, artifactState, changedOutputs = [] }: Props = $props();

  /** What the viewer shows: the viewable files of one folder (or the top level). */
  interface ViewerTarget {
    folder: string;
    items: ViewerItem[];
    index: number;
    returnFocus: HTMLElement | null;
  }

  // ----- Folders, previews and the viewer -----
  let expandedDirs = $state<Set<string>>(new Set());
  /** Open inline previews, by displayed path. */
  let openPreviews = $state<Set<string>>(new Set());
  /** Previews whose image failed to load: the text shown in their place. */
  let previewFailures = $state<Record<string, string>>({});
  /** Previews whose image has loaded (the rest show "making preview…"). */
  let previewLoaded = $state<Set<string>>(new Set());
  let viewing = $state<ViewerTarget | null>(null);

  function toggleDir(name: string): void {
    const next = new Set(expandedDirs);
    if (next.has(name)) next.delete(name); else next.add(name);
    expandedDirs = next;
  }

  function togglePreview(path: string): void {
    const next = new Set(openPreviews);
    if (next.has(path)) next.delete(path); else next.add(path);
    openPreviews = next;
  }

  /** Open every preview in `paths`, or close them all when every one is open. */
  function toggleAll(paths: string[]): void {
    const allOpen = paths.every(p => openPreviews.has(p));
    const next = new Set(openPreviews);
    for (const p of paths) if (allOpen) next.delete(p); else next.add(p);
    openPreviews = next;
  }

  function onPreviewLoad(path: string): void {
    previewLoaded = new Set(previewLoaded).add(path);
  }

  /** An image the route would not answer: say whether it is too large or unreadable. */
  async function onPreviewError(path: string): Promise<void> {
    let text = 'No preview: download to view.';
    try {
      const info = await fetchImageInfo(runId, path, 'thumb');
      if (info.refused) text = tooLargeText(info);
    } catch {
      // The facts are unavailable too: keep the plain text.
    }
    previewFailures = { ...previewFailures, [path]: text };
  }

  function openViewer(event: Event, folder: string, items: ViewerItem[], path: string): void {
    const index = items.findIndex(i => i.path === path);
    if (index < 0) return;
    viewing = { folder, items, index, returnFocus: event.currentTarget as HTMLElement | null };
  }

  /** Close the viewer and put focus back on the row it was opened from. */
  function closeViewer(): void {
    const back = viewing?.returnFocus ?? null;
    viewing = null;
    back?.focus();
  }

  // ----- Tooltip (a cut name, a download icon), kept inside the viewport -----
  const TIP_MAX = 320;
  let tip = $state<{ text: string; left: number; top: number } | null>(null);

  function showTip(anchor: HTMLElement, text: string): void {
    const rect = anchor.getBoundingClientRect();
    tip = {
      text,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - TIP_MAX - 8)),
      top: rect.bottom + 4,
    };
  }

  function hideTip(): void {
    tip = null;
  }

  /** The handlers that show `text` while an element is hovered or focused. */
  function hover(text: string) {
    return {
      onmouseenter: (e: Event) => showTip(e.currentTarget as HTMLElement, text),
      onmouseleave: hideTip,
      onfocus: (e: Event) => showTip(e.currentTarget as HTMLElement, text),
      onblur: hideTip,
    };
  }

  /** Show `name` in full beside `e`'s element when the name inside it is cut. */
  function showIfCut(e: Event, name: string): void {
    const el = e.currentTarget as HTMLElement;
    const head = el.querySelector('.n1') as HTMLElement | null;
    if (head && head.scrollWidth > head.clientWidth) showTip(el, name);
  }

  /** A name's full text on hover, only when the row has actually cut it. */
  function nameHover(name: string) {
    return { onmouseenter: (e: Event) => showIfCut(e, name), onmouseleave: hideTip };
  }

  /** The same on keyboard focus, for the button that holds a viewable name. */
  function nameFocus(name: string) {
    return { onfocus: (e: Event) => showIfCut(e, name), onblur: hideTip };
  }

  const DOWNLOAD_ONLY_TEXT = 'Download only: no preview for this file type';

  // ----- Artifact grouping and colours -----
  const CAT_ORDER: CatKey[] = ['data', 'images', 'directories', 'other'];
  const CAT_ICON: Record<CatKey, string> = {
    data: '▣', images: '◐', directories: '▤', other: '○',
  };
  const FILE_ICON: Record<ViewerKind, string> = {
    image: '◐', table: '▦', json: '{}', text: '≡', pdf: '▤',
  };
  // Artifact kinds carry no run state; the side-panel decoration colours of
  // the mockup: blue for images and PDFs, green for data, purple for folders,
  // a neutral grey for the rest. The grey is written out because the panel
  // remaps the text tokens to the primary text colour; it is the mockup's
  // --text-secondary grey lightened until pill text passes 4.5:1 on every row
  // it sits on (5.25:1 on the card, 4.68:1 on a folder member's hover).
  const CAT_COLOR: Record<CatKey, string> = {
    data:        '#50C878',
    images:      'var(--accent)',
    directories: '#B084CC',
    other:       '#a0a0a0',
  };
  const PILL_COLOR: Record<PillKind, string> = {
    image: CAT_COLOR.images, data: CAT_COLOR.data, other: CAT_COLOR.other,
  };
  const CAT_LABEL: Record<CatKey, string> = {
    data: 'Data', images: 'Images', directories: 'Directories', other: 'Other',
  };

  let grouped = $derived.by(() => {
    const g: Record<CatKey, Artifact[]> = { data: [], images: [], directories: [], other: [] };
    for (const a of artifacts) g[categoryOf(a)].push(a);
    return g;
  });

  /** The top level's viewable files, in list order: the viewer steps through these. */
  let topItems = $derived.by(() => {
    const items: ViewerItem[] = [];
    for (const cat of CAT_ORDER) {
      for (const a of grouped[cat]) {
        if (deriveArtifactType(a) === 'file' && a.viewer) {
          items.push(viewerItem(a, a.name, a.viewer));
        }
      }
    }
    return items;
  });

  /** A folder's viewable members, in the folder's order. */
  function folderItems(dir: Artifact): ViewerItem[] {
    return (dir.children ?? [])
      .filter(c => !c.name.endsWith('/') && c.viewer)
      .map(c => viewerItem(c, dir.name + c.name, c.viewer as ViewerKind));
  }

  function viewerItem(file: Artifact | ArtifactChild, path: string, viewer: ViewerKind): ViewerItem {
    return {
      path, name: file.name, size: file.size, viewer,
      extension: file.extension ?? '', color: PILL_COLOR[pillKind(file)],
    };
  }

  function folderImagePaths(dir: Artifact): string[] {
    return (dir.children ?? []).filter(c => c.viewer === 'image').map(c => dir.name + c.name);
  }

  let topImagePaths = $derived(
    grouped.images.filter(a => a.viewer === 'image').map(a => a.name),
  );

  let totalSize = $derived(artifacts.reduce((s, a) => s + (a.size || 0), 0));
  let totalFiles = $derived(
    artifacts.reduce((s, a) => s + (deriveArtifactType(a) === 'dir' ? (a.count || 0) : 1), 0),
  );
</script>

{#snippet cutName(name: string, cls: string)}
  {@const parts = splitNameForCut(name)}
  <span class={cls} {...nameHover(name)}><span class="n1">{parts.head}</span>{#if parts.tail}<span class="n2">{parts.tail}</span>{/if}</span>
{/snippet}

{#snippet showAllPill(paths: string[], onToggle: () => void)}
  {@const allOpen = paths.every(p => openPreviews.has(p))}
  <button
    class="hdr-act"
    data-testid="show-all"
    onclick={(e: MouseEvent) => { e.stopPropagation(); onToggle(); }}
  >{allOpen ? 'hide all' : 'show all'}</button>
{/snippet}

{#snippet fileRow(file: Artifact | ArtifactChild, path: string, folder: string, items: ViewerItem[], child: boolean)}
  {@const viewer = file.viewer ?? null}
  {@const pill = PILL_COLOR[pillKind(file)]}
  <div class="row" class:child data-testid="artifact-row">
    {#if viewer === 'image'}
      <button
        class="rcaret"
        data-testid="preview-toggle"
        aria-expanded={openPreviews.has(path)}
        aria-label={`${openPreviews.has(path) ? 'Hide' : 'Show'} preview of ${file.name}`}
        onclick={() => togglePreview(path)}
      >{openPreviews.has(path) ? '▾' : '▸'}</button>
    {:else}
      <span class="rcaret blank"></span>
    {/if}
    <span class="slot">{viewer ? FILE_ICON[viewer] : '▪'}</span>
    {#if viewer}
      <button class="name-btn" {...nameFocus(file.name)} onclick={(e: MouseEvent) => openViewer(e, folder, items, path)}>
        {@render cutName(file.name, 'fname')}
      </button>
    {:else}
      {@render cutName(file.name, 'fname')}
    {/if}
    <span class="tailbox">
      <span class="file-ext" style="color: {pill}; border-color: color-mix(in oklab, {pill} 40%, transparent);">{file.extension || '—'}</span>
      <span class="size">{formatBytes(file.size)}</span>
      <a
        class="dl"
        class:ring={!viewer}
        href={artifactDownloadUrl(runId, path)}
        download={file.name}
        aria-label={`Download ${file.name}`}
        data-testid="artifact-download"
        {...hover(viewer ? 'Download' : DOWNLOAD_ONLY_TEXT)}
      >⤓</a>
    </span>
  </div>
  {#if viewer === 'image' && openPreviews.has(path)}
    <button
      class="prev-card"
      class:in-dir={child}
      data-testid="artifact-preview"
      aria-label={`Open ${file.name}`}
      onclick={(e: MouseEvent) => openViewer(e, folder, items, path)}
    >
      {#if previewFailures[path]}
        <span class="prev-note">{previewFailures[path]}</span>
      {:else}
        {#if !previewLoaded.has(path)}<span class="prev-note">making preview…</span>{/if}
        <img
          class="thumb-img"
          class:loading={!previewLoaded.has(path)}
          src={artifactImageUrl(runId, path, 'thumb')}
          alt={file.name}
          onload={() => onPreviewLoad(path)}
          onerror={() => onPreviewError(path)}
        />
      {/if}
    </button>
  {/if}
{/snippet}

{#if artifactState !== 'done'}
  <div class="empty">Loading artifacts…</div>
{:else if artifacts.length === 0 && changedOutputs.length === 0}
  <div class="empty">No artifacts found.</div>
{:else}
  <div class="artifacts">
    {#if changedOutputs.length > 0}
      <div class="card cat-card" data-testid="unavailable-outputs">
        <div class="cat-header static">
          <span class="cat-icon unavailable-icon">!</span>
          <span class="cat-name">Unavailable</span>
          <span class="cat-count">{changedOutputs.length}</span>
        </div>
        {#each changedOutputs as name (name)}
          <div class="file-row unavailable-row" data-testid="unavailable-output">
            <span class="file-bullet">▪</span>
            <span class="file-name">{name}</span>
            <span class="unavailable-reason">{CHANGED_OUTPUT_TEXT}</span>
          </div>
        {/each}
      </div>
    {/if}
    {#if artifacts.length > 0}
    <div class="artifacts-summary">
      {artifacts.length} artifacts · {totalFiles} files · {formatBytes(totalSize)}
    </div>
    {/if}
    {#each CAT_ORDER as cat}
      {#if grouped[cat].length > 0}
        {@const items = grouped[cat]}
        {@const catTotal = items.reduce((s, a) => s + (a.size || 0), 0)}
        {@const open = !$collapsedCategories[cat]}
        <div class="card cat-card" class:closed={!open} data-testid={`category-${cat}`}>
          <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
          <div class="cat-header" onclick={() => toggleCategory(cat)}>
            <button
              class="rcaret"
              aria-expanded={open}
              aria-label={`${open ? 'Collapse' : 'Expand'} ${CAT_LABEL[cat]}`}
              data-testid="category-toggle"
              onclick={(e: MouseEvent) => { e.stopPropagation(); toggleCategory(cat); }}
            >{open ? '▾' : '▸'}</button>
            <span
              class="cat-icon"
              style="color: {CAT_COLOR[cat]}; background: color-mix(in oklab, {CAT_COLOR[cat]} 18%, transparent);"
            >{CAT_ICON[cat]}</span>
            <span class="cat-name">{CAT_LABEL[cat]}</span>
            <span class="grow"></span>
            {#if cat === 'images' && open && topImagePaths.length > 0}
              {@render showAllPill(topImagePaths, () => toggleAll(topImagePaths))}
            {/if}
            <span class="tailbox"><span class="cat-count">{items.length} · {formatBytes(catTotal)}</span></span>
          </div>
          {#if open}
            {#each items as a (a.name)}
              {#if deriveArtifactType(a) === 'dir'}
                {@const dirOpen = expandedDirs.has(a.name)}
                {@const imagePaths = folderImagePaths(a)}
                {@const members = folderItems(a)}
                <div class="dir-row">
                  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
                  <div class="row dir-head" onclick={() => toggleDir(a.name)}>
                    <button
                      class="rcaret"
                      aria-expanded={dirOpen}
                      aria-label={`${dirOpen ? 'Close' : 'Open'} folder ${a.name}`}
                      data-testid="dir-toggle"
                      onclick={(e: MouseEvent) => { e.stopPropagation(); toggleDir(a.name); }}
                    >{dirOpen ? '▾' : '▸'}</button>
                    <span class="slot dir-icon" style="color: {CAT_COLOR.directories};">▤</span>
                    {@render cutName(a.name, 'dname')}
                    <span class="grow"></span>
                    {#if imagePaths.length > 0}
                      {@render showAllPill(imagePaths, () => {
                        const opening = !imagePaths.every(p => openPreviews.has(p));
                        toggleAll(imagePaths);
                        if (opening && !expandedDirs.has(a.name)) toggleDir(a.name);
                      })}
                    {/if}
                    <span class="tailbox">
                      <span
                        class="file-ext dir-pill"
                        style="color: {CAT_COLOR.directories}; border-color: color-mix(in oklab, {CAT_COLOR.directories} 30%, transparent); background: color-mix(in oklab, {CAT_COLOR.directories} 15%, transparent);"
                      >DIR{a.count != null ? ` · ${a.count}` : ''}</span>
                      <span class="size">{formatBytes(a.size)}</span>
                      <span class="dl blank"></span>
                    </span>
                  </div>
                  {#if dirOpen && a.children}
                    <div class="dir-children">
                      {#each a.children as c (c.name)}
                        {#if c.name.endsWith('/')}
                          <!-- A subdirectory: no drill-down, a static row. -->
                          <div class="row child static" data-testid="artifact-subdir">
                            <span class="rcaret blank"></span>
                            <span class="slot dir-icon" style="color: {CAT_COLOR.directories};">▤</span>
                            {@render cutName(c.name, 'fname')}
                            <span class="tailbox">
                              <span
                                class="file-ext dir-pill"
                                style="color: {CAT_COLOR.directories}; border-color: color-mix(in oklab, {CAT_COLOR.directories} 30%, transparent); background: color-mix(in oklab, {CAT_COLOR.directories} 15%, transparent);"
                              >DIR</span>
                            </span>
                          </div>
                        {:else}
                          {@render fileRow(c, a.name + c.name, a.name, members, true)}
                        {/if}
                      {/each}
                    </div>
                  {/if}
                </div>
              {:else}
                {@render fileRow(a, a.name, '', topItems, false)}
              {/if}
            {/each}
          {/if}
        </div>
      {/if}
    {/each}
  </div>
{/if}

{#if viewing}
  <ArtifactViewer
    {runId}
    folder={viewing.folder}
    items={viewing.items}
    start={viewing.index}
    onClose={closeViewer}
  />
{/if}

{#if tip}
  <div class="artifact-tip" data-testid="artifact-tip" style="left: {tip.left}px; top: {tip.top}px; max-width: {TIP_MAX}px;">{tip.text}</div>
{/if}

<style>
  .empty {
    color: var(--text-muted);
    font-size: 11px;
    text-align: center;
    padding: 14px;
  }

  .card {
    background: var(--bg-header);
    border: 1px solid var(--border);
    overflow: hidden;
  }

  .artifacts { display: flex; flex-direction: column; gap: 8px; }
  .artifacts-summary {
    padding: 0 4px;
    font-size: 10px;
    color: var(--text-muted);
    font-family: 'Consolas', 'Courier New', monospace;
  }

  /* One column grid: caret 14px, icon 18px, then the name; the right-hand
     column is at least a fixed width so pills end at the same x everywhere,
     and grows for a wider pill (a 3-digit folder count) rather than overlap. */
  .cat-header {
    padding: 8px 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    user-select: none;
  }
  .cat-header:hover { background: #333337; }
  .cat-header.static { cursor: default; }
  .cat-header.static:hover { background: var(--bg-header); }
  .card.closed .cat-header { border-bottom: none; }
  .cat-icon {
    width: 18px;
    height: 18px;
    flex: none;
    border-radius: 2px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
  }
  .cat-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-primary);
  }
  .cat-count {
    font-size: 10px;
    color: var(--text-muted);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .grow { flex: 1; }

  .row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
    color: var(--text-primary);
    min-width: 0;
  }
  .row:hover { background: var(--bg-input); }
  .row.child {
    padding: 3px 12px 3px 38px;
    border-bottom: none;
    font-size: 10.5px;
    color: var(--text-secondary);
  }
  .row.child:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.04); }
  .row.static:hover { background: none; }
  .dir-head { cursor: pointer; }

  .rcaret {
    width: 14px;
    flex: none;
    padding: 0;
    border: none;
    background: none;
    color: var(--text-muted);
    font-size: 9px;
    text-align: center;
    cursor: pointer;
    border-radius: 2px;
  }
  .rcaret:hover { color: #fff; background: #3a3a3d; }
  .rcaret.blank { cursor: default; }
  .rcaret.blank:hover { background: none; }
  /* A row's arrow (an image preview, a folder) sits halfway between the arrow
     above it and the row's own icon, centre to centre, so it never lines up
     with its category header's arrow; icons and names keep the grid. A top
     row: header arrow at 19 px, icon at 43 px, arrow at 31 px (12 px right of
     its column). A folder member: folder arrow at 31 px, icon at 69 px, arrow
     at 50 px (5 px right of its column). */
  .row > .rcaret { position: relative; left: 12px; }
  .row.child > .rcaret { left: 5px; }
  .slot {
    width: 18px;
    flex: none;
    text-align: center;
    font-size: 10px;
    color: var(--text-muted);
  }

  .name-btn {
    flex: 1;
    min-width: 0;
    display: flex;
    padding: 0;
    border: none;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .name-btn:hover .fname { text-decoration: underline; }
  /* Middle cut: the head shrinks behind an ellipsis, the tail always shows. */
  .fname, .dname {
    display: flex;
    min-width: 0;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
  }
  .fname { flex: 1; }
  .row.child .fname { font-size: 10.5px; }
  .dname { flex: 0 1 auto; color: var(--accent); }
  .n1 { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .n2 { flex: none; white-space: pre; }

  .tailbox {
    flex: none;
    min-width: 136px;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
  }
  .file-ext {
    font-size: 9px;
    padding: 1px 5px;
    border: 1px solid;
    border-radius: 2px;
    text-transform: uppercase;
    font-family: 'Consolas', 'Courier New', monospace;
    white-space: nowrap;
  }
  .dir-pill { letter-spacing: 0.3px; }
  .size {
    color: var(--text-muted);
    font-size: 10px;
    min-width: 54px;
    text-align: right;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .dl {
    width: 18px;
    height: 18px;
    flex: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    color: var(--text-muted);
    font-size: 12px;
    text-decoration: none;
  }
  .dl:hover { color: #eee; background: #3a3a3d; }
  /* Download only: a yellow ring, explained on hover. */
  .dl.ring { border: 1.5px solid #E9A847; color: #E9A847; font-size: 11px; }
  .dl.ring:hover { background: rgba(233, 168, 71, 0.18); color: #ffcf7a; }
  .dl.blank:hover { background: none; }

  .hdr-act {
    flex: none;
    min-width: 46px;
    text-align: center;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 9px;
    color: #bbb;
    padding: 1px 7px;
    border-radius: 9px;
    cursor: pointer;
    background: #3a3a3e;
    border: 1px solid #55555b;
  }
  .hdr-act:hover {
    color: #fff;
    background: color-mix(in oklab, var(--accent) 30%, #3a3a3e);
    border-color: var(--accent);
  }

  .dir-row { border-bottom: 1px solid rgba(62, 62, 66, 0.5); }
  .dir-row:last-child { border-bottom: none; }
  .dir-children {
    background: var(--bg-input);
    border-top: 1px solid rgba(62, 62, 66, 0.5);
    padding: 4px 0;
  }

  /* The inline preview: a light card (a PNG renders on its light surface),
     its left edge on the file name. */
  .prev-card {
    display: block;
    margin: 2px 12px 8px 60px;
    padding: 6px;
    background: #fcfcfb;
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: zoom-in;
    max-width: 260px;
    text-align: left;
  }
  .prev-card.in-dir { margin-left: 86px; max-width: 240px; }
  .prev-card:hover { outline: 2px solid var(--accent); }
  .thumb-img { display: block; max-width: 100%; height: auto; }
  .thumb-img.loading { height: 0; }
  .prev-note {
    display: block;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10px;
    color: #555;
    padding: 4px 2px;
  }

  .file-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    color: var(--text-primary);
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
  }
  .file-row:last-child { border-bottom: none; }
  .unavailable-row { cursor: default; }
  .file-bullet { color: var(--text-muted); font-size: 10px; width: 10px; }
  .file-name {
    font-size: 11px;
    font-family: 'Consolas', 'Courier New', monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .unavailable-row .file-name {
    flex: 0 1 auto;
    color: var(--text-secondary);
    text-decoration: line-through;
  }
  .unavailable-reason {
    flex: 1;
    font-size: 10px;
    color: var(--status-failed-ink);
    text-align: right;
  }
  .unavailable-icon {
    color: var(--status-failed-ink);
    background: color-mix(in oklab, var(--status-failed) 18%, transparent);
    font-weight: 700;
  }

  .artifact-tip {
    position: fixed;
    z-index: 1000;
    pointer-events: none;
    background: var(--bg-panel);
    color: var(--text-primary);
    border: 1px solid #4a4a50;
    border-radius: 4px;
    padding: 3px 7px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    overflow-wrap: anywhere;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
  }
</style>
