<script lang="ts">
  /**
   * The run detail panel's Artifacts tab: artifacts grouped by category, with
   * expandable directory rows, inline image previews and the lightbox.
   *
   * The panel owns fetching; this component owns the grouping, the
   * expanded-directory set and the lightbox.
   */
  import { artifactDownloadUrl, deriveArtifactType } from './historyApi.js';
  import type { Artifact } from './historyApi.js';
  import { formatBytes } from './historyUtils.js';
  import { categoryOf, type CatKey } from './runDetailFormat.js';

  interface Props {
    runId: string;
    artifacts: Artifact[];
    artifactState: 'idle' | 'loading' | 'done';
  }

  let { runId, artifacts, artifactState }: Props = $props();

  let expandedDirs = $state<Set<string>>(new Set());

  function toggleDir(name: string): void {
    const next = new Set(expandedDirs);
    if (next.has(name)) next.delete(name); else next.add(name);
    expandedDirs = next;
  }

  // ----- Inline image preview -----
  // Gate: the BACKEND `is_image` flag (browser-renderable set only:
  // png/jpg/jpeg/gif/svg/webp — computed by WfcProvider.list_artifacts).
  // Deliberately NOT the wider IMAGE_EXTS grouping set in
  // runDetailFormat.ts, which includes pdf/tif/tiff that an <img> cannot
  // paint. Every artifact keeps its download-link row, previewed or not.
  let lightboxSrc = $state<string | null>(null);
  let lightboxAlt = $state('');
  function openLightbox(src: string, alt: string): void {
    lightboxSrc = src;
    lightboxAlt = alt;
  }
  function closeLightbox(): void {
    lightboxSrc = null;
  }
  $effect(() => {
    if (!lightboxSrc) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeLightbox();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // ----- Artifact grouping -----
  const CAT_ORDER: CatKey[] = ['data', 'images', 'directories', 'other'];
  const CAT_ICON: Record<CatKey, string> = {
    data: '▣', images: '◐', directories: '▤', other: '○',
  };
  const CAT_COLOR: Record<CatKey, string> = {
    data:        'var(--color-completed, #50C878)',
    images:      'var(--accent, #4A90D9)',
    directories: '#B084CC',
    other:       'var(--text-secondary, #888)',
  };
  const CAT_LABEL: Record<CatKey, string> = {
    data: 'Data', images: 'Images', directories: 'Directories', other: 'Other',
  };

  let grouped = $derived.by(() => {
    const g: Record<CatKey, Artifact[]> = { data: [], images: [], directories: [], other: [] };
    for (const a of artifacts) g[categoryOf(a)].push(a);
    return g;
  });

  let totalSize = $derived(artifacts.reduce((s, a) => s + (a.size || 0), 0));
  let totalFiles = $derived(
    artifacts.reduce((s, a) => s + (deriveArtifactType(a) === 'dir' ? (a.count || 0) : 1), 0),
  );
</script>

{#if artifactState !== 'done'}
  <div class="empty">Loading artifacts…</div>
{:else if artifacts.length === 0}
  <div class="empty">No artifacts found.</div>
{:else}
  <div class="artifacts">
    <div class="artifacts-summary">
      {artifacts.length} artifacts · {totalFiles} files · {formatBytes(totalSize)}
    </div>
    {#each CAT_ORDER as cat}
      {#if grouped[cat].length > 0}
        {@const items = grouped[cat]}
        {@const catTotal = items.reduce((s, a) => s + (a.size || 0), 0)}
        <div class="card cat-card">
          <div class="cat-header">
            <span
              class="cat-icon"
              style="color: {CAT_COLOR[cat]}; background: color-mix(in oklab, {CAT_COLOR[cat]} 18%, transparent);"
            >{CAT_ICON[cat]}</span>
            <span class="cat-name">{CAT_LABEL[cat]}</span>
            <span class="cat-count">{items.length} · {formatBytes(catTotal)}</span>
          </div>
          {#each items as a}
            {#if deriveArtifactType(a) === 'dir'}
              <div class="dir-row">
                <button class="dir-head" onclick={() => toggleDir(a.name)}>
                  <span class="dir-caret">{expandedDirs.has(a.name) ? '▾' : '▸'}</span>
                  <span class="dir-name">{a.name}</span>
                  <span
                    class="dir-pill"
                    style="color: {CAT_COLOR[cat]}; border-color: color-mix(in oklab, {CAT_COLOR[cat]} 30%, transparent); background: color-mix(in oklab, {CAT_COLOR[cat]} 15%, transparent);"
                  >DIR{a.count != null ? ` · ${a.count}` : ''}</span>
                  <span class="dir-size">{formatBytes(a.size)}</span>
                </button>
                {#if expandedDirs.has(a.name) && a.children}
                  <div class="dir-children">
                    {#each a.children as c}
                      {#if c.name.endsWith('/')}
                        <!-- Subdirectory: no drill-down yet; render as static row. -->
                        <div class="child-row">
                          <span class="child-dash">─</span>
                          <span class="child-name">{c.name}</span>
                        </div>
                      {:else}
                        <a
                          class="child-row child-row-link"
                          href={artifactDownloadUrl(runId, a.name + c.name)}
                          target="_blank"
                          rel="noopener"
                        >
                          <span class="child-dash">─</span>
                          <span class="child-name">{c.name}</span>
                          {#if c.size > 0}<span class="child-size">{formatBytes(c.size)}</span>{/if}
                        </a>
                      {/if}
                    {/each}
                  </div>
                {/if}
              </div>
            {:else}
              <a
                class="file-row"
                href={artifactDownloadUrl(runId, a.name)}
                target="_blank"
                rel="noopener"
              >
                <span class="file-bullet">▪</span>
                <span class="file-name">{a.name}</span>
                <span
                  class="file-ext"
                  style="color: {CAT_COLOR[cat]}; border-color: color-mix(in oklab, {CAT_COLOR[cat]} 40%, transparent);"
                >{a.extension || '—'}</span>
                <span class="file-size">{formatBytes(a.size)}</span>
              </a>
              {#if a.is_image}
                <!-- Inline preview beside the link row above, which stays.
                     Light card — a PNG always renders on its light
                     surface, so it must not float on a dark panel. -->
                <button
                  class="thumb-card"
                  onclick={() => openLightbox(artifactDownloadUrl(runId, a.name), a.name)}
                  aria-label={`Preview ${a.name}`}
                >
                  <img
                    class="thumb-img"
                    src={artifactDownloadUrl(runId, a.name)}
                    alt={a.name}
                    loading="lazy"
                  />
                </button>
              {/if}
            {/if}
          {/each}
        </div>
      {/if}
    {/each}
  </div>
{/if}
{#if lightboxSrc}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="lightbox-overlay" onclick={closeLightbox}>
    <img
      class="lightbox-img"
      src={lightboxSrc}
      alt={lightboxAlt}
      onclick={(e: MouseEvent) => e.stopPropagation()}
    />
  </div>
{/if}

<style>
  .empty {
    color: var(--text-muted, #666);
    font-size: 11px;
    text-align: center;
    padding: 14px;
  }

  /* Cards */
  .card {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    overflow: hidden;
  }

  /* Artifacts */
  .artifacts { display: flex; flex-direction: column; gap: 8px; }
  /* Inline image preview: light card so the always-light PNG does
     not float on a dark panel; click opens the lightbox. */
  .thumb-card {
    display: block;
    margin: 4px 8px 8px 24px;
    padding: 6px;
    background: #fcfcfb;
    border: 1px solid var(--border, #e1e0d9);
    border-radius: 6px;
    cursor: zoom-in;
    max-width: 260px;
  }
  .thumb-img { display: block; max-width: 100%; height: auto; }
  .lightbox-overlay {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: rgba(0, 0, 0, 0.72);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: zoom-out;
  }
  .lightbox-img {
    max-width: 92vw;
    max-height: 92vh;
    background: #fcfcfb;
    padding: 8px;
    border-radius: 6px;
    cursor: default;
  }
  .artifacts-summary {
    padding: 0 4px;
    font-size: 10px;
    color: var(--text-muted, #666);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .cat-card { overflow: hidden; }
  .cat-header {
    padding: 8px 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--bg-header, #2d2d30);
    border-bottom: 1px solid var(--border, #3e3e42);
  }
  .cat-icon {
    width: 18px;
    height: 18px;
    border-radius: 2px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
  }
  .cat-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-primary, #ccc);
  }
  .cat-count {
    font-size: 10px;
    color: var(--text-muted, #666);
    font-family: 'Consolas', 'Courier New', monospace;
    margin-left: auto;
  }

  .file-row, .dir-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    text-decoration: none;
    color: var(--text-primary, #ccc);
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
    background: none;
    border-left: none;
    border-right: none;
    border-top: none;
    width: 100%;
    font-family: inherit;
    cursor: pointer;
  }
  .file-row:last-child, .dir-row:last-child .dir-head { border-bottom: none; }
  .file-row:hover, .dir-head:hover { background: var(--bg-input, #1e1e1e); }
  .file-bullet { color: var(--text-muted, #666); font-size: 10px; width: 10px; }
  .file-name {
    flex: 1;
    font-size: 11px;
    font-family: 'Consolas', 'Courier New', monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: left;
  }
  .file-ext {
    font-size: 9px;
    padding: 1px 5px;
    border: 1px solid;
    border-radius: 2px;
    text-transform: uppercase;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .file-size {
    color: var(--text-muted, #666);
    font-size: 10px;
    min-width: 54px;
    text-align: right;
    font-family: 'Consolas', 'Courier New', monospace;
  }

  .dir-row { border-bottom: 1px solid rgba(62, 62, 66, 0.5); }
  .dir-row:last-child { border-bottom: none; }
  .dir-caret { color: var(--text-muted, #666); font-size: 9px; width: 10px; }
  .dir-name {
    flex: 1;
    font-size: 11px;
    font-family: 'Consolas', 'Courier New', monospace;
    color: var(--accent, #4A90D9);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: left;
  }
  .dir-pill {
    font-size: 9px;
    padding: 1px 5px;
    border: 1px solid;
    border-radius: 2px;
    text-transform: uppercase;
    font-family: 'Consolas', 'Courier New', monospace;
    letter-spacing: 0.3px;
  }
  .dir-size {
    color: var(--text-muted, #666);
    font-size: 10px;
    min-width: 54px;
    text-align: right;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .dir-children {
    background: var(--bg-input, #1e1e1e);
    border-top: 1px solid rgba(62, 62, 66, 0.5);
    padding: 4px 0;
  }
  .child-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 12px 3px 36px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    color: var(--text-secondary, #888);
  }
  .child-dash { color: var(--text-muted, #666); font-size: 9px; }
  .child-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .child-size {
    color: var(--text-muted, #666);
    font-size: 9.5px;
    min-width: 48px;
    text-align: right;
  }
  a.child-row-link {
    text-decoration: none;
    cursor: pointer;
  }
  a.child-row-link:hover {
    background: var(--bg-hover, rgba(255, 255, 255, 0.04));
    color: var(--text-primary, #ddd);
  }
  a.child-row-link:hover .child-name {
    text-decoration: underline;
  }
</style>
