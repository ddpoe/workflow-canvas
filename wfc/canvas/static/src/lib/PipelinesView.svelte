<script lang="ts">
  /**
   * Top-level Pipelines view: row-per-pipeline_id, expandable.
   * Hosts the [Open pipeline in Canvas ↗] button per pipeline (Action 1).
   */
  import { pipelineRuns, loadRuns } from './historyStore.js';
  import { canvasPipelineId } from './pipeline.js';
  import PipelineRow from './PipelineRow.svelte';

  // The "current canvas pipelineId" gates the running-block (D-10).
  // Single source of truth: the `canvasPipelineId` store, written on
  // submit (machine), Action 1 (PipelineRow), and cleared on Action 2,
  // Toolbar upload, Clear, and Reset. All four gate call sites
  // (PipelineRow, RunDetailPanel handlers ×2, Toolbar) now read this
  // same store — review iter 1 root-cause fix.
  let currentCanvasPipelineId = $derived($canvasPipelineId);
</script>

<div class="pipelines-view">
  <div class="toolbar-row">
    <button class="refresh" onclick={() => loadRuns()} title="Refresh pipelines">↻ Refresh</button>
  </div>
  {#if $pipelineRuns.length === 0}
    <div class="empty">No pipelines yet — submit one from the Builder to see it here.</div>
  {:else}
    {#each $pipelineRuns as row (row.pipelineId)}
      <PipelineRow {row} {currentCanvasPipelineId} />
    {/each}
  {/if}
</div>

<style>
  .pipelines-view { display: flex; flex-direction: column; gap: 4px; }
  .toolbar-row { display: flex; justify-content: flex-end; padding: 4px 0; }
  .refresh {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    border-radius: 3px;
    font-size: 11px;
    padding: 4px 10px;
    cursor: pointer;
    font-family: inherit;
  }
  .refresh:hover { border-color: var(--accent, #4A90D9); }
  .empty { padding: 40px; text-align: center; color: var(--text-muted, #666); font-size: 13px; }
</style>
