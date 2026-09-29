<script lang="ts">
  /**
   * The run detail panel's canvas actions: scope the Descendants view to this
   * run, load its lineage into the canvas, or graft a reference to it.
   */
  import { scopeDescendantsTo } from './historyStore.js';
  import { fetchLineagePipeline } from './historyApi.js';
  import type { WfcRun } from './historyApi.js';
  import {
    confirmReplaceIfDirty,
    checkRunningBlock,
    graftRunReference,
    loadPipeline,
    canvasPipelineId,
  } from '../builder/pipeline.js';
  import { showFlash, nodes } from '../builder/stores.js';
  import { graftToastState, centerOnNodeRequest } from '../shared/uiState.js';
  import { get } from 'svelte/store';

  interface Props {
    run: WfcRun;
  }

  let { run }: Props = $props();

  // ---------- Load-in-Canvas action handlers (Actions 2, 3) ----------

  // Current-canvas pipelineId.  Read from the `canvasPipelineId` store:
  // the running-block is scoped to the *user's current canvas*, not
  // whatever historical run is currently being inspected, so a row from
  // a still-running pipeline whose canvas the user has moved on from
  // does not block Actions 2 & 3.
  function currentCanvasPipelineId(): string | null {
    return get(canvasPipelineId);
  }

  async function handleOpenLineage(): Promise<void> {
    if (!run) return;
    if (await checkRunningBlock(currentCanvasPipelineId())) return;
    if (!(await confirmReplaceIfDirty(`lineage of run #${run.id}`))) return;
    try {
      const json = await fetchLineagePipeline(run.id);
      loadPipeline(json);
      // Action 2: synthesized lineage is NOT a submitted pipeline, so
      // clear the canvas pipelineId.  Subsequent gates correctly treat
      // the canvas as having no in-flight identity.
      canvasPipelineId.set(null);
      showFlash(`Loaded lineage for run ${run.id} into canvas`, 'success');
    } catch (err) {
      const code = err instanceof Error ? err.message : String(err);
      if (code === 'LINEAGE_SYNTHESIS_FAILED') {
        showFlash(
          "Couldn't reconstruct lineage — the run's ancestor chain is malformed or too long.",
          'error',
        );
      } else if (code === 'LINEAGE_RUN_NOT_FOUND') {
        showFlash('Run not found.', 'error');
      } else {
        showFlash(`Failed to load lineage: ${code}`, 'error');
      }
    }
  }

  async function handleReferenceInCanvas(): Promise<void> {
    if (!run) return;
    if (await checkRunningBlock(currentCanvasPipelineId())) return;
    // No dirty-confirm: graft is additive.
    const newNodeId = graftRunReference(run.id);
    // Drive the styled GraftToast. [Jump to node] selects the new
    // node and asks App.svelte to center the canvas on it. The
    // centering bridge writes a node id into `centerOnNodeRequest`;
    // App.svelte subscribes there and calls SvelteFlow's `fitView` with
    // that node — this avoids prop-drilling `fitView` through HistoryView
    // → RunDetailPanel.
    graftToastState.set({
      message: 'Reference added to Canvas',
      detail: `${run.method} · ${run.id}`,
      onJump: () => {
        nodes.update(ns => ns.map(n => ({ ...n, selected: n.id === newNodeId })));
        centerOnNodeRequest.set(newNodeId);
        graftToastState.set(null);
      },
    });
  }
</script>

<!-- The footer holds → Descendants beside the Open lineage and
     Reference actions. -->
<div class="footer">
  <button class="footer-btn primary" onclick={() => scopeDescendantsTo(run!.id)}>→ Descendants</button>
  <button class="footer-btn accent" onclick={handleOpenLineage}>Open lineage in Canvas</button>
  <button class="footer-btn" onclick={handleReferenceInCanvas}>Reference in Canvas</button>
</div>

<style>
  /* Footer */
  .footer {
    padding: 10px;
    display: flex;
    gap: 6px;
    border-top: 1px solid var(--border, #3e3e42);
    background: var(--bg-header, #2d2d30);
    flex-shrink: 0;
  }
  .footer-btn {
    flex: 1;
    padding: 7px 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    border-radius: 3px;
    font-size: 11px;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
  }
  .footer-btn:hover:not(:disabled) { border-color: var(--accent, #4A90D9); }
  .footer-btn:disabled { color: var(--text-muted, #666); cursor: not-allowed; }
  .footer-btn.primary {
    background: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
    color: #fff;
    font-weight: 600;
  }
</style>
