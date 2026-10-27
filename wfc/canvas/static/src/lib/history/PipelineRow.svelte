<script lang="ts">
  /**
   * One pipeline row in PipelinesView, on the `.pipeline-grid` column tracks:
   * Status | Pipeline | Runs | Samples | Started | Actions. Expandable;
   * expanded reveals a header row and one ChildRunRow per child run, on the
   * `.child-grid` tracks. The status is the server summary's. The Runs cell
   * draws the runs bar and its counts in words. While the pipeline runs, the
   * row shows Stop and "Watch in Canvas ↗" (watchPipeline.ts) in place of
   * "Open in Canvas ↗". "Open in Canvas ↗" is wired
   * through checkRunningBlock + confirmReplaceIfDirty before
   * fetchPipelineDocument + loadPipeline; the ⋯ menu holds it again with
   * the download and the pipeline id's copy.
   */
  import {
    expandedPipelineIds,
    togglePipelineExpanded,
    RUN_BUCKET_ORDER,
    loadRuns,
  } from './historyStore.js';
  import { cancelJob } from '../machines/services.js';
  import { fetchPipelineDocument, pipelineDownloadUrl } from './historyApi.js';
  import { watchPipelineInCanvas } from './watchPipeline.js';
  import {
    confirmReplaceIfDirty,
    checkRunningBlock,
    loadPipeline,
    canvasPipelineId,
  } from '../builder/pipeline.js';
  import { showFlash } from '../builder/stores.js';
  import {
    formatRelativeTime,
    formatTimestamp,
    pipelinePillKind,
    PILL_LABEL,
  } from './historyUtils.js';
  import ChildRunRow from './ChildRunRow.svelte';
  import PopMenu from '../registry/PopMenu.svelte';
  import type { MenuItem } from '../registry/PopMenu.svelte';
  import type { PipelineRowSummary, RunBuckets } from './historyStore.js';

  interface Props {
    row: PipelineRowSummary;
    currentCanvasPipelineId: string | null;
  }
  let { row, currentCanvasPipelineId }: Props = $props();

  let expanded = $derived($expandedPipelineIds.has(row.pipelineId));
  let pipelineLabel = $derived(row.name);
  let pillKind = $derived(pipelinePillKind(row.status));

  /** Each bucket's word and colour, in the runs bar's fixed order. */
  const BUCKET_STYLE: Record<keyof RunBuckets, { word: string; color: string }> = {
    ran: { word: 'ran', color: 'var(--color-completed)' },
    cached: { word: 'cached', color: 'var(--color-cached)' },
    failed: { word: 'failed', color: 'var(--color-failed)' },
    cancelled: { word: 'cancelled', color: 'var(--color-cancelled)' },
    running: { word: 'running', color: 'var(--color-history-running)' },
    pending: { word: 'pending', color: 'var(--color-history-pending)' },
  };

  // Only the buckets above zero are drawn and counted.
  let parts = $derived(
    RUN_BUCKET_ORDER
      .filter(key => row.buckets[key] > 0)
      .map(key => ({ key, n: row.buckets[key], ...BUCKET_STYLE[key] })),
  );
  let runsText = $derived(parts.map(p => `${p.n} ${p.word}`).join(' · '));

  let kebab = $state<HTMLButtonElement | undefined>();
  let menuOpen = $state(false);

  async function openPipeline(): Promise<void> {
    if (await checkRunningBlock(currentCanvasPipelineId)) return;
    if (!(await confirmReplaceIfDirty(pipelineLabel))) return;
    try {
      const json = await fetchPipelineDocument(row.pipelineId);
      loadPipeline(json);
      // Action 1: take on the loaded pipeline's identity — subsequent
      // gates (Toolbar import, RunDetailPanel actions) will scope the
      // running-block to this id.  pipeline.json itself carries
      // no pipeline_id field; the directory name (= row.pipelineId
      // prop) is the authoritative source.
      canvasPipelineId.set(row.pipelineId);
      showFlash(`Loaded ${pipelineLabel} into canvas`, 'success');
    } catch (err) {
      const code = err instanceof Error ? err.message : String(err);
      if (code === 'PIPELINE_DOCUMENT_NOT_FOUND') {
        showFlash(
          'Pipeline document not available — this pipeline never reached the run-generation stage.',
          'error',
        );
      } else {
        showFlash(`Failed to load pipeline: ${code}`, 'error');
      }
    }
  }

  function handleOpenPipeline(e: MouseEvent): void {
    e.stopPropagation();
    void openPipeline();
  }

  /** Download the run's frozen pipeline.json through the download route. */
  function downloadPipeline(): void {
    const a = document.createElement('a');
    a.href = pipelineDownloadUrl(row.pipelineId);
    a.download = `${row.pipelineId}.pipeline.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function copyPipelineId(): void {
    const copied = navigator.clipboard?.writeText(row.pipelineId);
    if (!copied) {
      showFlash('Could not copy the pipeline ID', 'error');
      return;
    }
    copied.then(
      () => showFlash('Copied pipeline ID', 'success'),
      () => showFlash('Could not copy the pipeline ID', 'error'),
    );
  }

  // While the pipeline runs, "Open in Canvas" becomes "Watch in Canvas":
  // the canvas attaches to the running pipeline, statuses, log stream and
  // Stop included, instead of loading its document only.
  let running = $derived(pillKind === 'running');

  function watchPipeline(): Promise<boolean> {
    return watchPipelineInCanvas(row.pipelineId, pipelineLabel);
  }

  function handleWatchPipeline(e: MouseEvent): void {
    e.stopPropagation();
    void watchPipeline();
  }

  let menuItems = $derived<MenuItem[]>([
    running
      ? { label: 'Watch in Canvas ↗', testid: 'pipeline-menu-watch', run: () => void watchPipeline() }
      : { label: 'Open in Canvas ↗', testid: 'pipeline-menu-open', run: () => void openPipeline() },
    { label: 'Download pipeline JSON', testid: 'pipeline-menu-download', run: downloadPipeline },
    { label: 'Copy pipeline ID', testid: 'pipeline-menu-copy', run: copyPipelineId },
  ]);

  function toggleMenu(e: MouseEvent): void {
    e.stopPropagation();
    menuOpen = !menuOpen;
  }

  // Stop: shown while the pipeline reads running. The cancel route takes
  // the pipeline id (also its job id) and reaches any pipeline the server
  // can: this session's, one reached after a restart, or a CLI one.
  let stopping = $state(false);

  async function stopPipeline(e: MouseEvent): Promise<void> {
    e.stopPropagation();
    if (stopping) return;
    stopping = true;
    try {
      const resp = await cancelJob(row.pipelineId);
      if (resp.ok) {
        showFlash(`Stopped ${pipelineLabel}`, 'success');
      } else {
        showFlash(`Could not stop ${pipelineLabel} (HTTP ${resp.status})`, 'error');
      }
    } catch (err) {
      const why = err instanceof Error ? err.message : String(err);
      showFlash(`Could not stop ${pipelineLabel}: ${why}`, 'error');
    } finally {
      stopping = false;
    }
    await loadRuns();
  }
</script>

<div class="pipeline-row" class:expanded>
  <!-- Row head is a clickable div (not a button) so the nested buttons are
       HTML-valid. Keyboard a11y is preserved via role="button" + tabindex
       + Enter/Space onkeydown. -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    class="row-head pipeline-grid"
    role="button"
    tabindex="0"
    onclick={() => togglePipelineExpanded(row.pipelineId)}
    onkeydown={(e) => {
      if (e.target !== e.currentTarget) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        togglePipelineExpanded(row.pipelineId);
      }
    }}
  >
    <span class="status-cell">
      <span class="hist-pill {pillKind}">{PILL_LABEL[pillKind]}</span>
    </span>
    <span class="name-cell" title={pipelineLabel}>
      <span class="caret">{expanded ? '▾' : '▸'}</span>
      <span class="pipeline-name">{pipelineLabel}</span>
    </span>
    <span class="runs-cell" title={runsText}>
      <span class="runs-bar" data-testid="runs-bar">
        {#each parts as part (part.key)}
          <span class="seg" data-bucket={part.key}
                style="flex: {part.n}; background: {part.color};"></span>
        {/each}
      </span>
      <span class="runs-text" data-testid="runs-text">
        {#each parts as part, i (part.key)}{#if i > 0}<span class="sep">{' · '}</span>{/if}<span
            class="count count-{part.key}"
            style="color: {part.key === 'pending' ? 'var(--text-muted)' : part.color};"
          >{part.n} {part.word}</span>{/each}
      </span>
    </span>
    <span class="mono muted">{row.sampleCount}</span>
    <span class="mono muted" title={formatTimestamp(row.started)}>{formatRelativeTime(row.started)}</span>
    <span class="actions">
      {#if running}
        <button class="stop-btn" data-testid="pipeline-stop" disabled={stopping}
                onclick={stopPipeline}>Stop</button>
        <button class="open-btn" data-testid="pipeline-watch"
                onclick={handleWatchPipeline}>Watch in Canvas ↗</button>
      {:else}
        <button class="open-btn" onclick={handleOpenPipeline}>Open in Canvas ↗</button>
      {/if}
      <button
        class="kebab"
        bind:this={kebab}
        onclick={toggleMenu}
        aria-label="Pipeline actions"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >⋯</button>
    </span>
  </div>
  {#if expanded}
    <div class="children">
      <div class="child-grid child-header" data-testid="child-header">
        <span>Status</span>
        <span>Step</span>
        <span>Version</span>
        <span>Sample</span>
        <span>NID</span>
        <span class="right">Duration</span>
        <span class="right">Run</span>
      </div>
      {#each row.runs as run (run.id)}
        <ChildRunRow {run} />
      {/each}
    </div>
  {/if}
</div>

{#if menuOpen && kebab}
  <PopMenu anchor={kebab} items={menuItems} title={pipelineLabel} testid="pipeline-menu"
           onClose={() => (menuOpen = false)} />
{/if}

<style>
  .pipeline-row {
    border: 1px solid var(--border, #3e3e42);
    margin-bottom: 6px;
    background: var(--bg-panel, #252526);
    border-radius: 3px;
  }
  .pipeline-row.expanded { border-color: var(--accent, #4A90D9); }
  .row-head {
    padding: 8px 12px;
    background: none;
    border: none;
    width: 100%;
    text-align: left;
    cursor: pointer;
    color: var(--text-primary, #ccc);
    font-family: inherit;
    font-size: 12px;
  }
  .row-head:hover { background: rgba(255, 255, 255, 0.02); }
  .name-cell { display: flex; align-items: center; gap: 6px; }
  .caret { color: var(--text-muted, #666); flex-shrink: 0; }
  .pipeline-name {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .runs-cell { display: flex; align-items: center; gap: 10px; }
  /* A 1px frame around segments separated by 1px gaps, every segment the
     same height on the same top and bottom edge. */
  .runs-bar {
    display: flex;
    align-items: stretch;
    gap: 1px;
    height: 8px;
    width: 92px;
    flex-shrink: 0;
    padding: 1px;
    border: 1px solid var(--border-node, #4a4a4a);
    border-radius: 3px;
    background: var(--bg-canvas, #1a1a1a);
    box-sizing: content-box;
    overflow: hidden;
  }
  .seg { display: block; min-width: 2px; border-radius: 1px; }
  .runs-text { font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sep { color: var(--text-muted, #666); }
  .mono { font-family: 'Consolas', monospace; font-size: 11px; font-variant-numeric: tabular-nums; }
  .muted { color: var(--text-muted, #666); }
  .actions { display: flex; gap: 6px; align-items: center; justify-content: flex-end; }
  .open-btn {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--accent, #4A90D9);
    color: var(--accent, #4A90D9);
    border-radius: 3px;
    padding: 3px 9px;
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
    white-space: nowrap;
  }
  .open-btn:hover { background: rgba(74, 144, 217, 0.10); }
  .stop-btn {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--color-failed, #d9534f);
    color: var(--color-failed, #d9534f);
    border-radius: 3px;
    padding: 3px 9px;
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
  }
  .stop-btn:disabled { opacity: 0.5; cursor: default; }
  .kebab {
    background: none;
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-secondary, #888);
    border-radius: 3px;
    padding: 0 7px;
    font-size: 13px;
    line-height: 1.4;
    cursor: pointer;
    font-family: inherit;
  }
  .kebab:hover { color: var(--text-primary, #ccc); border-color: var(--accent, #4A90D9); }
  .children {
    background: var(--bg-input, #1e1e1e);
    border-top: 1px solid var(--border, #3e3e42);
    border-radius: 0 0 3px 3px;
  }
  .child-header {
    padding: 4px 12px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--text-muted, #666);
    background: var(--bg-header, #2d2d30);
    border-bottom: 1px solid var(--border, #3e3e42);
  }
  .right { text-align: right; }
</style>
