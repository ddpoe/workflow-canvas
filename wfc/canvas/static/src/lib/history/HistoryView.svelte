<script lang="ts">
  import FilterBar from './FilterBar.svelte';
  import PathsView from './PathsView.svelte';
  import PipelinesView from './PipelinesView.svelte';
  import DescendantsView from './DescendantsView.svelte';
  import RunDetailPanel from './RunDetailPanel.svelte';
  import DraftStepPanel from './DraftStepPanel.svelte';
  import { isDraftRef, draftBundleRefusal } from './historyDrafts.js';
  import ExportPreview from './ExportPreview.svelte';
  import ExportRunsDialog from './ExportRunsDialog.svelte';
  import {
    loading,
    error,
    selectedRunId,
    filters,
    selectedRunIds,
    statusBuckets,
    loadRuns,
    historyView,
    draftSteps,
  } from './historyStore.js';

  let { visible = false }: { visible?: boolean } = $props();
  let showExportPreview = $state(false);
  // The runs Export runs was pressed for; the dialog is open while set.
  let exportRunIds = $state<string[] | null>(null);
  // A lineage with a draft step cannot become a rerun bundle: Export runs
  // is disabled while the selection holds one, naming it (d-10).
  let bundleRefusal = $derived.by(() => {
    const ref = [...$selectedRunIds].find(isDraftRef);
    const step = ref ? $draftSteps.find(d => d.draftString === ref) : undefined;
    return step ? draftBundleRefusal(step) : null;
  });

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
      class:active={$historyView === 'descendants'}
      onclick={() => historyView.set('descendants')}
    >Descendants</button>
    <button
      class="seg"
      class:active={$historyView === 'lineages'}
      onclick={() => historyView.set('lineages')}
    >Lineages</button>
    <button
      class="seg"
      class:active={$historyView === 'pipelines'}
      onclick={() => historyView.set('pipelines')}
    >Pipelines</button>
  </div>
  <FilterBar />

  <div class="status-summary" title="Run counts after time/module/method/sample filters, before status chips">
    <span class="summary-bucket summary-success">
      <span class="summary-dot" style="background: var(--status-ok)"></span>
      {$statusBuckets.success} success
    </span>
    <span class="summary-bucket summary-cached" title="Cache-hit runs (also counted in success)">
      <span class="summary-dot" style="background: var(--status-cached)"></span>
      {$statusBuckets.cached} cached
    </span>
    <span class="summary-bucket summary-failed">
      <span class="summary-dot" style="background:var(--status-failed)"></span>
      {$statusBuckets.failed} failed
    </span>
    <span class="summary-bucket summary-running">
      <span class="summary-dot" style="background: var(--status-active)"></span>
      {$statusBuckets.running} running
    </span>
    <span class="summary-bucket summary-cancelled">
      <span class="summary-dot" style="background:var(--status-cancelled)"></span>
      {$statusBuckets.cancelled} cancelled
    </span>
    {#if $statusBuckets.draft > 0}
      <span class="summary-bucket summary-draft" data-testid="summary-draft"
            title="Draft steps: each draft's latest draft run (also counted by status)">
        <span class="summary-sq"></span>
        {$statusBuckets.draft} draft (latest kept, not recorded)
      </span>
    {/if}
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
        {#if $historyView === 'pipelines'}
          <PipelinesView />
        {:else if $historyView === 'descendants'}
          <DescendantsView />
        {:else}
          <PathsView />
        {/if}
      </div>

      {#if $selectedRunId && isDraftRef($selectedRunId)}
        <DraftStepPanel draftRef={$selectedRunId} />
      {:else if $selectedRunId}
        <RunDetailPanel runId={$selectedRunId} />
      {/if}
    </div>
  {/if}

  {#if $filters.selectMode && $selectedRunIds.size > 0}
    <div class="export-bar">
      <span>{$selectedRunIds.size} run{$selectedRunIds.size > 1 ? 's' : ''} selected</span>
      <div class="export-actions">
        <button
          class="export-btn"
          onclick={() => { exportRunIds = [...$selectedRunIds]; }}
          disabled={!!bundleRefusal}
          data-testid="export-runs"
          title={bundleRefusal ?? 'Export the selected runs and every run above them as a bundle anyone can rerun'}
        >
          Export runs
        </button>
        <button class="export-btn" onclick={() => { showExportPreview = true; }}>
          Export Artifacts
        </button>
      </div>
    </div>
  {/if}

  {#if showExportPreview}
    <ExportPreview onClose={() => { showExportPreview = false; }} />
  {/if}

  {#if exportRunIds}
    <ExportRunsDialog runIds={exportRunIds} onClose={() => { exportRunIds = null; }} />
  {/if}
</div>

<style>
  .history-view {
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
    background: var(--bg-canvas);
  }
  .history-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    flex: 1;
    color: var(--text-secondary);
    font-size: 13px;
  }
  .spinner {
    width: 16px;
    height: 16px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
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
    color: var(--status-failed-ink);
    font-size: 13px;
  }
  .retry-btn {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 6px 14px;
    color: var(--text-primary);
    font-size: 12px;
    cursor: pointer;
  }
  .retry-btn:hover {
    border-color: var(--accent);
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
    background: var(--bg-header);
    border-top: 1px solid var(--border);
    font-size: 12px;
    color: var(--text-primary);
    flex-shrink: 0;
  }
  .export-actions {
    display: flex;
    gap: 6px;
  }
  .export-btn {
    background: var(--accent);
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
  .export-btn:disabled { opacity: 0.45; cursor: not-allowed; filter: none; }
  .summary-sq {
    width: 10px;
    height: 9px;
    border: 1px solid var(--dash-neutral);
    border-radius: 2px;
    display: inline-block;
  }
  .status-summary {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    padding: 6px 14px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    font-size: 11px;
    color: var(--text-secondary);
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
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .seg {
    padding: 4px 12px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 500;
    border-radius: 3px;
    cursor: pointer;
    font-family: inherit;
  }
  .seg.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
</style>
