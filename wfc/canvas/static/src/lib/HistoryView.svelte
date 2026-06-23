<script lang="ts">
  import FilterBar from './FilterBar.svelte';
  import PathsView from './PathsView.svelte';
  import PipelinesView from './PipelinesView.svelte';
  import DescendantTree from './DescendantTree.svelte';
  import RunDetailPanel from './RunDetailPanel.svelte';
  import ExportPreview from './ExportPreview.svelte';
  import {
    loading,
    error,
    selectedRunId,
    descendantTreeRoot,
    filters,
    selectedRunIds,
    statusBuckets,
    loadRuns,
    historyView,
  } from './historyStore.js';

  let { visible = false }: { visible?: boolean } = $props();
  let showExportPreview = $state(false);

  $effect(() => {
    if (visible) {
      loadRuns();
    }
  });
</script>

<div class="history-view">
  <div class="view-switcher">
    <button
      class="seg"
      class:active={$historyView === 'pipelines'}
      onclick={() => historyView.set('pipelines')}
    >Pipelines</button>
    <button
      class="seg"
      class:active={$historyView === 'lineages'}
      onclick={() => historyView.set('lineages')}
    >Lineages</button>
  </div>
  <FilterBar />

  <div class="status-summary" title="Run counts after time/module/method/sample filters, before status chips">
    <span class="summary-bucket summary-success">
      <span class="summary-dot" style="background:#27ae60"></span>
      {$statusBuckets.success} success
    </span>
    <span class="summary-bucket summary-failed">
      <span class="summary-dot" style="background:#e74c3c"></span>
      {$statusBuckets.failed} failed
    </span>
    <span class="summary-bucket summary-running">
      <span class="summary-dot" style="background:#3498db"></span>
      {$statusBuckets.running} running
    </span>
    <span class="summary-bucket summary-cancelled">
      <span class="summary-dot" style="background:#7f8ea3"></span>
      {$statusBuckets.cancelled} cancelled
    </span>
  </div>

  {#if $loading}
    <div class="history-loading">
      <span class="spinner"></span>
      <span>Loading runs...</span>
    </div>
  {:else if $error}
    <div class="history-error">
      <p>Failed to load runs: {$error}</p>
      <button class="retry-btn" onclick={() => loadRuns()}>Retry</button>
    </div>
  {:else}
    <div class="history-content">
      <div class="history-main">
        {#if $descendantTreeRoot}
          <DescendantTree runId={$descendantTreeRoot} />
        {:else if $historyView === 'pipelines'}
          <PipelinesView />
        {:else}
          <PathsView />
        {/if}
      </div>

      {#if $selectedRunId}
        <RunDetailPanel runId={$selectedRunId} />
      {/if}
    </div>
  {/if}

  {#if $filters.selectMode && $selectedRunIds.size > 0}
    <div class="export-bar">
      <span>{$selectedRunIds.size} run{$selectedRunIds.size > 1 ? 's' : ''} selected</span>
      <button class="export-btn" onclick={() => { showExportPreview = true; }}>
        Export Artifacts
      </button>
    </div>
  {/if}

  {#if showExportPreview}
    <ExportPreview onClose={() => { showExportPreview = false; }} />
  {/if}
</div>

<style>
  .history-view {
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
    background: var(--bg-canvas, #1a1a1a);
  }
  .history-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    flex: 1;
    color: var(--text-secondary, #888);
    font-size: 13px;
  }
  .spinner {
    width: 16px;
    height: 16px;
    border: 2px solid var(--border, #3e3e42);
    border-top-color: var(--accent, #4A90D9);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  .history-error {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    flex: 1;
    color: var(--color-failed, #E74C3C);
    font-size: 13px;
  }
  .retry-btn {
    background: var(--bg-panel, #252526);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 4px;
    padding: 6px 14px;
    color: var(--text-primary, #ccc);
    font-size: 12px;
    cursor: pointer;
  }
  .retry-btn:hover {
    border-color: var(--accent, #4A90D9);
  }
  .history-content {
    display: flex;
    flex: 1;
    overflow: hidden;
  }
  .history-main {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
  }
  .export-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    background: var(--bg-header, #2d2d30);
    border-top: 1px solid var(--border, #3e3e42);
    font-size: 12px;
    color: var(--text-primary, #ccc);
    flex-shrink: 0;
  }
  .export-btn {
    background: var(--accent, #4A90D9);
    border: none;
    border-radius: 4px;
    padding: 5px 14px;
    color: white;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
  }
  .export-btn:hover {
    filter: brightness(1.1);
  }
  .status-summary {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    padding: 6px 14px;
    background: var(--bg-panel, #252526);
    border-bottom: 1px solid var(--border, #3e3e42);
    font-size: 11px;
    color: var(--text-secondary, #888);
    flex-shrink: 0;
  }
  .summary-bucket {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .summary-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  }
  .view-switcher {
    display: flex;
    gap: 2px;
    padding: 6px 14px;
    background: var(--bg-panel, #252526);
    border-bottom: 1px solid var(--border, #3e3e42);
    flex-shrink: 0;
  }
  .seg {
    padding: 4px 12px;
    background: var(--bg-input, #1e1e1e);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-secondary, #888);
    font-size: 11px;
    font-weight: 500;
    border-radius: 3px;
    cursor: pointer;
    font-family: inherit;
  }
  .seg.active {
    background: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
    color: #fff;
  }
</style>
