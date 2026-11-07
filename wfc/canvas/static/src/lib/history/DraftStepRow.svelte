<script lang="ts">
  /**
   * One DRAFT row inside an expanded pipeline, on the `.child-grid` tracks
   * beside the recorded runs: Status | Step | Version | Sample | NID |
   * Duration | Run. The pill reads DRAFT · OK / DRAFT · FAILED with a solid
   * outline; the draft's own step shows the ● DRAFT version tag; the Run
   * cell reads "draft" and copies the step's draft string. Click opens the
   * draft step's run panel.
   */
  import type { DraftStep } from './historyApi.js';
  import { selectRun, highlightedRunId } from './historyStore.js';
  import { formatDuration } from './historyUtils.js';
  import { draftPillKind, draftPillText } from './historyDrafts.js';
  import { showFlash } from '../builder/stores.js';

  interface Props { step: DraftStep }
  let { step }: Props = $props();

  let highlighted = $derived($highlightedRunId === step.draftString);
  let duration = $derived(step.duration != null ? formatDuration(step.duration) : '—');

  function copyDraftString(e: MouseEvent): void {
    e.stopPropagation();
    const copied = navigator.clipboard?.writeText(step.draftString);
    if (!copied) { showFlash('Could not copy the draft string', 'error'); return; }
    copied.then(
      () => showFlash(`Copied ${step.draftString}`, 'success'),
      () => showFlash('Could not copy the draft string', 'error'),
    );
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div
  class="child-row child-grid draft-row"
  class:highlighted
  role="button"
  tabindex="0"
  data-testid="draft-row"
  data-draft-string={step.draftString}
  onclick={() => selectRun(step.draftString)}
  onkeydown={(e) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectRun(step.draftString); }
  }}
>
  <span><span class="hist-pill draft-pill {draftPillKind(step.status)}" data-testid="draft-pill">{draftPillText(step.status)}</span></span>
  <span class="step" title={step.method}>{step.method}</span>
  <span>
    {#if step.isDraft}
      <span class="ver draft-ver" data-testid="draft-version">● DRAFT</span>
    {:else if step.version != null}
      <span class="ver">v{step.version}</span>
    {:else}
      <span class="muted">—</span>
    {/if}
  </span>
  <span class="sample mono" title={step.sample}>{step.sample}</span>
  <span class="nid mono muted" title={step.nid ?? ''}>{step.nid ?? ''}</span>
  <span class="duration mono muted right">{duration}</span>
  <span class="right">
    <button type="button" class="draft-ref mono" data-testid="draft-copy"
            title="Copy {step.draftString}" onclick={copyDraftString}>draft</button>
  </span>
</div>

<style>
  .child-row {
    padding: 5px 12px;
    border-top: 1px solid rgba(62, 62, 66, 0.5);
    color: var(--text-primary);
    font-size: 11px;
    cursor: pointer;
  }
  .child-row:hover { background: rgba(255, 255, 255, 0.03); }
  .child-row.highlighted {
    background: color-mix(in srgb, var(--accent) 15%, transparent);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  /* DRAFT · status: the status pill with a solid outline. */
  .draft-pill { border-color: var(--pill); }
  .step { font-weight: 500; }
  .mono { font-family: 'Consolas', monospace; font-size: 10.5px; font-variant-numeric: tabular-nums; }
  .muted { color: var(--text-muted); }
  .right { text-align: right; }
  .sample { color: var(--system-ink); }
  .ver {
    font-family: 'Consolas', monospace;
    font-size: 10px;
    padding: 0 5px;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-secondary);
  }
  .draft-ver {
    color: #fff;
    font-weight: 700;
    letter-spacing: 0.04em;
    border-color: color-mix(in oklab, var(--marker-light) 45%, transparent);
  }
  .draft-ref {
    background: none;
    border: none;
    padding: 0;
    color: var(--text-secondary);
    cursor: copy;
  }
  .draft-ref:hover { color: var(--text-primary); text-decoration: underline; }
</style>
