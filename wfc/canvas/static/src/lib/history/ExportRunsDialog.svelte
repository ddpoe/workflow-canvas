<script lang="ts">
  /**
   * History's Export run / Export runs: the runs and every run above them as
   * one rerun bundle, downloaded through the bundle route.
   *
   * On open the dialog asks for the plan (the route's dry run, which writes no
   * bundle) with "Include outputs" and "Include images" off, the defaults of
   * `wfc export-run`; changing either asks for a fresh plan, and an answer
   * to an older request is dropped. A refused plan lists every reason under
   * the plan's own lead and offers nothing to download. An ok plan states its
   * steps, shows the plan's own warning lines (a step whose image was never
   * pushed; none when the images are included) and an Export button, which
   * is the user's confirmation and sends the download. The download carries
   * the plan's token and options, so the server writes only the plan shown
   * here. Closing the dialog while it plans or downloads saves nothing.
   */
  import { onMount, onDestroy } from 'svelte';
  import {
    planExportRuns,
    downloadExportRuns,
    ExportRunsRefusedError,
  } from './historyApi.js';
  import type { ExportRunsOptions, ExportRunsPlan } from './historyApi.js';

  interface Props {
    /** The runs asked for; the plan adds every run above them. */
    runIds: string[];
    onClose: () => void;
  }

  let { runIds, onClose }: Props = $props();

  type Phase = 'planning' | 'refused' | 'ready' | 'downloading' | 'failed';

  let phase = $state<Phase>('planning');
  let plan = $state<ExportRunsPlan | null>(null);
  let message = $state<string | null>(null);
  let reasons = $state<string[]>([]);
  let withOutputs = $state(false);
  let withImage = $state(false);
  // The runs and options the shown plan was asked for; the download sends the same.
  let planned: string[] = [];
  let plannedOptions: ExportRunsOptions = { withOutputs: false, withImage: false };
  // Which dry run is the latest: an answer to an earlier one is dropped.
  let planSeq = 0;
  // Set once the dialog closes: nothing answered after that is acted on.
  let closed = false;
  let downloadAbort: AbortController | null = null;

  let title = $derived(runIds.length === 1 ? 'Export run' : 'Export runs');

  function close(): void {
    closed = true;
    downloadAbort?.abort();
    onClose();
  }

  function save(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  async function download(token: string): Promise<void> {
    phase = 'downloading';
    downloadAbort = new AbortController();
    try {
      const { blob, filename } = await downloadExportRuns(
        planned, token, plannedOptions, downloadAbort.signal);
      if (closed) return;
      save(blob, filename);
      close();
    } catch (err) {
      if (closed) return;
      if (err instanceof ExportRunsRefusedError) {
        phase = 'refused';
        message = err.message;
        reasons = err.reasons;
      } else {
        phase = 'failed';
        message = err instanceof Error ? err.message : String(err);
      }
    }
  }

  async function start(): Promise<void> {
    const seq = ++planSeq;
    const runs = [...runIds];
    const options: ExportRunsOptions = { withOutputs, withImage };
    phase = 'planning';
    try {
      const answer = await planExportRuns(runs, options);
      if (closed || seq !== planSeq) return;
      plan = answer;
      planned = runs;
      plannedOptions = options;
      if (!answer.ok) {
        phase = 'refused';
        message = answer.refused_text;
        reasons = answer.refusals.map(r => r.reason);
      } else {
        phase = 'ready';
      }
    } catch (err) {
      if (closed || seq !== planSeq) return;
      phase = 'failed';
      message = err instanceof Error ? err.message : String(err);
    }
  }

  function setOutputs(e: Event): void {
    withOutputs = (e.currentTarget as HTMLInputElement).checked;
    start();
  }

  function setImage(e: Event): void {
    withImage = (e.currentTarget as HTMLInputElement).checked;
    start();
  }

  // Plan once, when the dialog opens: a re-render with an equal selection
  // must not plan again. Only a changed option re-plans.
  onMount(() => {
    start();
  });

  onDestroy(() => {
    closed = true;
    downloadAbort?.abort();
  });
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="modal-overlay" onclick={close} onkeydown={(e) => { if (e.key === 'Escape') close(); }}>
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    class="modal-content"
    role="dialog"
    tabindex="-1"
    aria-modal="true"
    aria-label={title}
    data-testid="export-runs-dialog"
    onclick={(e: MouseEvent) => e.stopPropagation()}
  >
    <div class="modal-header">
      <span class="modal-title">{title}</span>
      <button class="modal-close" onclick={close} title="Close">&times;</button>
    </div>

    {#if phase !== 'downloading'}
      <div class="options">
        <label>
          <input type="checkbox" data-testid="export-runs-with-outputs" checked={withOutputs} onchange={setOutputs} />
          Include outputs
        </label>
        <label>
          <input type="checkbox" data-testid="export-runs-with-image" checked={withImage} onchange={setImage} />
          Include images
        </label>
      </div>
    {/if}

    {#if phase === 'planning'}
      <div class="modal-body center">Planning the bundle...</div>
    {:else if phase === 'downloading'}
      <div class="modal-body center">Writing the bundle...</div>
    {:else if phase === 'failed'}
      <div class="modal-body center error">{message}</div>
    {:else if phase === 'refused'}
      <div class="modal-body">
        <p class="lead error" data-testid="export-runs-refused-lead">{message}</p>
        <ul class="reasons">
          {#each reasons as reason}
            <li data-testid="export-runs-refusal">{reason}</li>
          {/each}
        </ul>
      </div>
      <div class="modal-footer">
        <button class="cancel-btn" onclick={close}>Close</button>
      </div>
    {:else if phase === 'ready' && plan}
      <div class="modal-body">
        <p class="lead">
          The bundle holds {plan.steps.length} step{plan.steps.length === 1 ? '' : 's'}
          (the selected run{runIds.length === 1 ? '' : 's'} and every run above).
        </p>
        {#if plan.warning_lines.length > 0}
          <div class="warning" data-testid="export-runs-warning">
            {#each plan.warning_lines as line}
              <p>{line}</p>
            {/each}
          </div>
        {/if}
      </div>
      <div class="modal-footer">
        <button class="cancel-btn" onclick={close}>Cancel</button>
        <button class="confirm-btn" data-testid="export-runs-confirm" onclick={() => download(plan!.token)}>
          {plan.warning_lines.length > 0 ? 'Export anyway' : 'Export'}
        </button>
      </div>
    {/if}
  </div>
</div>

<style>
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .modal-content {
    background: var(--bg-panel, #252526);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 8px;
    width: 460px;
    max-width: 90vw;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  }
  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border, #3e3e42);
  }
  .modal-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary, #ccc);
  }
  .modal-close {
    background: none;
    border: none;
    color: var(--text-muted, #666);
    font-size: 20px;
    cursor: pointer;
    line-height: 1;
  }
  .modal-close:hover { color: var(--text-primary, #ccc); }
  .modal-body {
    padding: 16px;
    overflow-y: auto;
    flex: 1;
    font-size: 12px;
    color: var(--text-primary, #ccc);
    line-height: 1.45;
  }
  .modal-body.center {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 80px;
    color: var(--text-secondary, #888);
  }
  .options {
    display: flex;
    gap: 16px;
    padding: 10px 16px 0;
    font-size: 12px;
    color: var(--text-primary, #ccc);
  }
  .options label {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }
  .error { color: var(--color-failed, #E74C3C); }
  .modal-body.center.error { color: var(--color-failed, #E74C3C); }
  .lead { margin: 0 0 10px; }
  .reasons {
    margin: 0;
    padding-left: 18px;
  }
  .reasons li { margin-bottom: 4px; }
  .warning {
    border: 1px solid #E9A847;
    background: rgba(233, 168, 71, 0.10);
    border-radius: 4px;
    padding: 8px 10px;
  }
  .warning p { margin: 0 0 6px; }
  .warning p:last-child { margin-bottom: 0; }
  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--border, #3e3e42);
  }
  .cancel-btn {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 4px;
    padding: 6px 14px;
    color: var(--text-primary, #ccc);
    font-size: 12px;
    cursor: pointer;
  }
  .cancel-btn:hover { border-color: var(--accent, #4A90D9); }
  .confirm-btn {
    background: var(--accent, #4A90D9);
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    color: white;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }
  .confirm-btn:hover { filter: brightness(1.1); }
</style>
