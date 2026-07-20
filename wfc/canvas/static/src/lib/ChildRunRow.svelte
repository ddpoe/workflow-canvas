<script lang="ts">
  /**
   * Slim row inside an expanded pipeline. Click selects the run and opens
   * RunDetailPanel — there are NO inline action buttons (D-9). All
   * per-run reload actions live on RunDetailPanel.
   */
  import type { WfcRun } from './historyApi.js';
  import { selectRun, highlightedRunId } from './historyStore.js';
  import { statusColor, formatDuration } from './historyUtils.js';

  interface Props { run: WfcRun }
  let { run }: Props = $props();
  let highlighted = $derived($highlightedRunId === run.id);
  // Cache-hit audit rows keep their success status (cached is a flavor of
  // success, matching the Lineages PathNodeCard treatment) and add the
  // amber CACHED pill.
  let isCached = $derived(!!run.cacheSourceRunId);
</script>

<button
  type="button"
  class="child-row"
  class:highlighted
  onclick={() => selectRun(run.id)}
  title={`Run ${run.id} — ${run.method}`}
>
  <span class="dot" style="background: {statusColor(run.status)}"></span>
  <span class="status">{run.status}</span>
  <span class="method">
    <span class="method-name">{run.method}</span>
    {#if isCached}
      <span class="cached-pill">{'⟳'} CACHED</span>
    {/if}
  </span>
  <span class="sample">{run.dataSource || '—'}</span>
  <span class="nid">{run.nid || ''}</span>
  <span class="duration">{formatDuration(run.duration)}</span>
  <span class="run-id">r_{run.id}</span>
</button>

<style>
  .child-row {
    display: grid;
    grid-template-columns: 14px 70px 1fr 1fr 60px 60px 60px;
    align-items: center;
    gap: 10px;
    padding: 6px 12px 6px 30px;
    background: none;
    border: none;
    border-bottom: 1px solid rgba(62, 62, 66, 0.4);
    width: 100%;
    text-align: left;
    color: var(--text-primary, #ccc);
    font-family: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  .child-row:hover { background: rgba(255, 255, 255, 0.03); }
  .child-row.highlighted {
    background: rgba(74, 144, 217, 0.15);
    box-shadow: inset 3px 0 0 var(--accent, #4A90D9);
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .status { color: var(--text-secondary, #888); text-transform: capitalize; font-size: 10.5px; }
  .method { display: flex; align-items: center; gap: 8px; overflow: hidden; }
  .method-name { color: var(--text-primary, #ccc); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
  .sample { color: var(--color-completed, #50C878); font-family: 'Consolas', monospace; font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .nid, .duration, .run-id { color: var(--text-muted, #666); font-family: 'Consolas', monospace; font-size: 10.5px; text-align: right; }
</style>
