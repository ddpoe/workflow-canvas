<script lang="ts">
  /**
   * One pipeline row in PipelinesView. Expandable; expanded reveals
   * ChildRunRow per child run. Hosts the "Open pipeline in Canvas ↗"
   * button — wired through checkRunningBlock + confirmReplaceIfDirty
   * before fetchPipelineDocument + loadPipeline.
   */
  import {
    expandedPipelineIds,
    togglePipelineExpanded,
  } from './historyStore.js';
  import { fetchPipelineDocument } from './historyApi.js';
  import {
    confirmReplaceIfDirty,
    checkRunningBlock,
    loadPipeline,
    canvasPipelineId,
  } from './pipeline.js';
  import { showFlash } from './stores.js';
  import { statusColor, formatRelativeTime } from './historyUtils.js';
  import ChildRunRow from './ChildRunRow.svelte';
  import type { PipelineRowSummary } from './historyStore.js';

  interface Props {
    row: PipelineRowSummary;
    currentCanvasPipelineId: string | null;
  }
  let { row, currentCanvasPipelineId }: Props = $props();

  let expanded = $derived($expandedPipelineIds.has(row.pipelineId));
  let pipelineLabel = $derived(row.name);

  async function handleOpenPipeline(e: MouseEvent): Promise<void> {
    e.stopPropagation();
    if (await checkRunningBlock(currentCanvasPipelineId)) return;
    if (!(await confirmReplaceIfDirty(pipelineLabel))) return;
    try {
      const json = await fetchPipelineDocument(row.pipelineId);
      loadPipeline(json);
      // Action 1: take on the loaded pipeline's identity — subsequent
      // gates (Toolbar import, RunDetailPanel actions) will scope the
      // running-block (D-10) to this id.  pipeline.json itself carries
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
</script>

<div class="pipeline-row" class:expanded>
  <!-- Row head is a clickable div (not a button) so the nested
       "Open pipeline" button is HTML-valid. Keyboard a11y is preserved
       via role="button" + tabindex + Enter/Space onkeydown. -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    class="row-head"
    role="button"
    tabindex="0"
    onclick={() => togglePipelineExpanded(row.pipelineId)}
    onkeydown={(e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        togglePipelineExpanded(row.pipelineId);
      }
    }}
  >
    <span class="caret">{expanded ? '▾' : '▸'}</span>
    <span class="dot" style="background: {statusColor(row.status)}"></span>
    <span class="status-text">{row.status}</span>
    <span class="name-cell">
      <span class="pipeline-name">{pipelineLabel}</span>
      {#if row.cachedCount > 0}
        <span class="cached-pill">{'⟳'} {row.cachedCount}/{row.total} cached</span>
      {/if}
    </span>
    <span class="progress">{row.done}/{row.total}</span>
    <span class="samples">{row.sampleCount} samples</span>
    <span class="time">{formatRelativeTime(row.started)}</span>
    <button class="open-btn" onclick={handleOpenPipeline}>Open pipeline in Canvas ↗</button>
  </div>
  {#if expanded}
    <div class="children">
      {#each row.runs as run (run.id)}
        <ChildRunRow {run} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .pipeline-row {
    border: 1px solid var(--border, #3e3e42);
    margin-bottom: 6px;
    background: var(--bg-panel, #252526);
    border-radius: 3px;
    overflow: hidden;
  }
  .pipeline-row.expanded { border-color: var(--accent, #4A90D9); }
  .row-head {
    display: grid;
    grid-template-columns: 14px 14px 70px 1fr 60px 80px 70px auto;
    align-items: center;
    gap: 10px;
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
  .caret { color: var(--text-muted, #666); }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .status-text { color: var(--text-secondary, #888); text-transform: capitalize; font-size: 11px; }
  .name-cell { display: flex; align-items: center; gap: 8px; overflow: hidden; }
  .pipeline-name {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* Matches the Lineages PathNodeCard cached pill. */
  .cached-pill {
    background: rgba(233, 168, 71, 0.15);
    color: #E9A847;
    border: 1px solid rgba(233, 168, 71, 0.45);
    border-radius: 9px;
    font-size: 9.5px;
    font-weight: 600;
    padding: 1px 7px;
    letter-spacing: 0.3px;
    white-space: nowrap;
  }
  .progress, .samples, .time { color: var(--text-muted, #666); font-family: 'Consolas', monospace; font-size: 11px; }
  .open-btn {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--accent, #4A90D9);
    color: var(--accent, #4A90D9);
    border-radius: 3px;
    padding: 5px 10px;
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
    white-space: nowrap;
  }
  .open-btn:hover { background: rgba(74, 144, 217, 0.10); }
  .children { background: var(--bg-input, #1e1e1e); }
</style>
