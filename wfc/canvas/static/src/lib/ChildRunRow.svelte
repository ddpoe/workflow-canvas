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
  <span class="method">{run.method}</span>
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
  .method { color: var(--text-primary, #ccc); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sample { color: var(--color-completed, #50C878); font-family: 'Consolas', monospace; font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .nid, .duration, .run-id { color: var(--text-muted, #666); font-family: 'Consolas', monospace; font-size: 10.5px; text-align: right; }
</style>
