<script lang="ts">
  /**
   * Top-level Pipelines view: a header row naming the columns, then one
   * expandable row per pipeline_id. The header and the rows share the
   * column tracks of `.pipeline-grid` (app.css), so every column lines up.
   * Hosts the [Open in Canvas ↗] button per pipeline (Action 1).
   */
  import { pipelineRuns, loadRuns } from './historyStore.js';
  import { canvasPipelineId } from '../builder/pipeline.js';
  import PipelineRow from './PipelineRow.svelte';

  // The "current canvas pipelineId" gates the running-block.
  // Single source of truth: the `canvasPipelineId` store, written on
  // submit (machine), Action 1 (PipelineRow), and cleared on Action 2,
  // Toolbar upload, Clear, and Reset. All four gate call sites
  // (PipelineRow, RunDetailPanel handlers ×2, Toolbar) read this
  // same store.
  let currentCanvasPipelineId = $derived($canvasPipelineId);
</script>

<div class="pipelines-view">
  <div class="toolbar-row">
    <button class="refresh" onclick={() => loadRuns()} title="Refresh pipelines">↻ Refresh</button>
  </div>
  {#if $pipelineRuns.length === 0}
    <div class="empty">No pipelines yet — submit one from the Builder to see it here.</div>
  {:else}
    <div class="pipeline-grid col-header" data-testid="pipelines-header">
      <span>Status</span>
      <span>Pipeline</span>
      <span>Runs</span>
      <span>Samples</span>
      <span>Started</span>
      <span class="right">Actions</span>
    </div>
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
  /* The rows' 1px border plus their 12px padding, so the header's tracks
     start where the rows' do. */
  .col-header {
    padding: 4px 13px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--text-muted, #666);
    border-bottom: 1px solid var(--border, #3e3e42);
  }
  .right { text-align: right; }
  .empty { padding: 40px; text-align: center; color: var(--text-muted, #666); font-size: 13px; }
</style>
