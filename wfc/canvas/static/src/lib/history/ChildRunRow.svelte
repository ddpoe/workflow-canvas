<script lang="ts">
  /**
   * Slim row inside an expanded pipeline, on the `.child-grid` column
   * tracks: Status | Step | Version | Sample | NID | Duration | Run. Click
   * selects the run and opens RunDetailPanel — there are NO inline action
   * buttons. All per-run reload actions live on RunDetailPanel. A
   * cancelled run's pill says why on hover; an upstream failure names the
   * failed step and sample, then links the failed run.
   */
  import type { WfcRun } from './historyApi.js';
  import { selectRun, highlightedRunId } from './historyStore.js';
  import { modules } from '../builder/stores.js';
  import {
    cancelReasonText,
    formatDuration,
    PILL_LABEL,
    ranNewestVersion,
    runPillKind,
  } from './historyUtils.js';

  interface Props { run: WfcRun }
  let { run }: Props = $props();
  let highlighted = $derived($highlightedRunId === run.id);
  let pillKind = $derived(runPillKind(run));
  let reason = $derived(run.status === 'cancelled' ? cancelReasonText(run) : null);
  let newest = $derived(ranNewestVersion(run, $modules));
  // A cached run did not run and a cancelled one never started: no duration.
  let duration = $derived(
    pillKind === 'cached' || pillKind === 'cancelled' ? '—' : formatDuration(run.duration),
  );

  function openCause(e: MouseEvent, runId: string): void {
    e.stopPropagation();
    selectRun(runId);
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div
  class="child-row child-grid"
  class:highlighted
  role="button"
  tabindex="0"
  data-run-id={run.id}
  onclick={() => selectRun(run.id)}
  onkeydown={(e) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      selectRun(run.id);
    }
  }}
>
  <span class="cell-overflow-visible">
    {#if reason}
      <span class="tip">
        <span class="hist-pill {pillKind}">{PILL_LABEL[pillKind]}</span>
        <span class="tipbox" role="tooltip">
          {reason.text}{#if reason.cause}{#if reason.cause.method}{': '}<code>{reason.cause.method}</code>{#if reason.cause.sample}{' on '}<code>{reason.cause.sample}</code>{/if}{/if}
            <button type="button" class="cause-link"
                    onclick={(e) => openCause(e, reason.cause!.runId)}>r_{reason.cause.runId}</button>{/if}
        </span>
      </span>
    {:else}
      <span class="hist-pill {pillKind}">{PILL_LABEL[pillKind]}</span>
    {/if}
  </span>
  <span class="step" title={run.method}>{run.method}</span>
  <span>
    {#if run.version != null}
      <span class="ver" class:newest
            title={newest ? 'The newest version of this method' : `Method version v${run.version}`}
      >v{run.version}</span>
    {:else}
      <span class="muted">—</span>
    {/if}
  </span>
  <span class="sample mono" title={run.dataSource}>{run.dataSource || '—'}</span>
  <span class="nid mono muted" title={run.nid}>{run.nid || ''}</span>
  <span class="duration mono muted right">{duration}</span>
  <span class="run-id mono muted right" title={`Run ${run.id}`}>r_{run.id}</span>
</div>

<style>
  .child-row {
    padding: 5px 12px;
    border-top: 1px solid rgba(62, 62, 66, 0.5);
    color: var(--text-primary, #ccc);
    font-size: 11px;
    cursor: pointer;
  }
  .child-row:hover { background: rgba(255, 255, 255, 0.03); }
  .child-row.highlighted {
    background: rgba(74, 144, 217, 0.15);
    box-shadow: inset 3px 0 0 var(--accent, #4A90D9);
  }
  .step { font-weight: 500; }
  .mono { font-family: 'Consolas', monospace; font-size: 10.5px; font-variant-numeric: tabular-nums; }
  .muted { color: var(--text-muted, #666); }
  .right { text-align: right; }
  .sample { color: var(--color-completed, #50C878); }
  .ver {
    font-family: 'Consolas', monospace;
    font-size: 10px;
    padding: 0 5px;
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    color: var(--text-secondary, #888);
  }
  .ver.newest::before {
    content: '';
    display: inline-block;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    margin-right: 4px;
    vertical-align: middle;
    background: var(--color-version-newest);
  }
  .tip { position: relative; display: inline-block; }
  /* Hangs below the pill with no gap, so the pointer can reach the link. */
  .tipbox {
    display: none;
    position: absolute;
    left: 0;
    top: 100%;
    z-index: 5;
    margin-top: 0;
    padding: 6px 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.55);
    color: var(--text-primary, #ccc);
    font-size: 11px;
    white-space: nowrap;
    cursor: default;
  }
  .tip:hover .tipbox, .tip:focus-within .tipbox { display: block; }
  .tipbox code { font-family: 'Consolas', monospace; font-size: 10.5px; }
  .cause-link {
    background: none;
    border: none;
    padding: 0;
    margin-left: 4px;
    color: var(--accent, #4A90D9);
    font-family: 'Consolas', monospace;
    font-size: 10.5px;
    cursor: pointer;
  }
  .cause-link:hover { text-decoration: underline; }
</style>
