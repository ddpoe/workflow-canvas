<script lang="ts">
  import { nodes, edges, runState, pipelineName, clearRunState,
           setPipelineError, showFlash, newCanvasGeneration } from './stores.js';
  import { pushState } from './undo.js';
  import { undo, redo } from './undo.js';
  import {
    exportPipeline,
    loadPipeline,
    confirmReplaceIfDirty,
    checkRunningBlock,
    canvasPipelineId,
  } from './pipeline.js';
  import { dispatchUserStop } from '../machines/root.js';
  import { requestRun, blockedReasons } from './lockFlow.js';
  import { validatePipeline } from '../machines/services.js';
  import ArchiveBadge from './ArchiveBadge.svelte';
  import { refreshDrafts } from './drafts.js';
  import { clearRefusals, refusalsByNode, showRefusals } from './nodeRefusals.js';
  import { isDraftNode } from './nodeData.js';
  import type { PipelineJSON, CanvasNodeData } from '../shared/types.js';
  import { get } from 'svelte/store';

  // Run-button enabled/disabled flag derived from the bridged
  // `runState.running`, which root.ts updates on each pipelineRunActor
  // snapshot. Double-click protection is structural (the actor's
  // RUN_CLICKED guard); this just keeps the button visually disabled.
  $: runDisabled = $runState.running;
  // Blocked rows in the current lock disable Run; the reasons are its tooltip.
  $: runBlocked = $blockedReasons.length > 0;
  // A canvas holding a draft node runs in draft mode; Run says so.
  $: hasDraft = $nodes.some(n => isDraftNode(n.data as CanvasNodeData));

  export let activeTab: 'builder' | 'registry' | 'history' = 'builder';
  export let onTabChange: ((tab: 'builder' | 'registry' | 'history') => void) | undefined = undefined;

  function doExport() {
    const pipeline = exportPipeline();
    const blob = new Blob([JSON.stringify(pipeline, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${pipeline.name || 'pipeline'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function doImport() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const text = await file.text();
      const pipeline: PipelineJSON = JSON.parse(text);
      // The Toolbar JSON upload goes through the same running-block +
      // dirty-confirm gates as the History tab's reload actions: block
      // first, then confirm dirty. The block reads the canvas-level
      // pipelineId, so an upload cannot replace a canvas whose pipeline
      // is still running.
      if (await checkRunningBlock(get(canvasPipelineId))) return;
      const targetName = pipeline.name || file.name;
      if (!(await confirmReplaceIfDirty(targetName))) return;
      pushState();
      loadPipeline(pipeline);
      // Uploaded JSON is treated as a fresh canvas — the file format
      // (PipelineJSON in shared/types.ts) carries no pipeline_id field,
      // and even when the upload happens to be the same shape as a
      // submitted pipeline.json, the user's "intent" is to load it as
      // unsubmitted (anything else would conflate identity across
      // sessions). Clear the canvas pid.
      canvasPipelineId.set(null);
    };
    input.click();
  }

  async function doValidate() {
    // Every draft node first takes the contract its method.yaml declares
    // now; the server reads each draft again and gives the verdict.
    clearRefusals();
    if (get(nodes).some(n => isDraftNode(n.data as CanvasNodeData))) await refreshDrafts();
    const pipeline = exportPipeline();
    try {
      const result = await validatePipeline(pipeline);
      // A refusal naming a node shows on it and in the Inspector.
      showRefusals(refusalsByNode(result.errors ?? [], result.draftRefusals ?? []));
      if (result.valid) {
        // Clear any stale error banner; confirm via transient toast so the
        // user gets feedback without a blocking modal.
        setPipelineError(null);
        showFlash('Workflow is valid!', 'success');
      } else {
        // `errors` already holds each draft refusal's message.
        setPipelineError({
          kind: 'not_found',
          message: 'Validation errors:',
          items: result.errors ?? [],
          hint: "Fix each issue in the pipeline, or in the method's files if it says so, then click Validate again.",
        });
      }
    } catch (err) {
      setPipelineError({
        kind: 'unknown',
        message: `Validation request failed: ${String(err)}`,
      });
    }
  }

  function doClear() {
    pushState();
    newCanvasGeneration();
    nodes.set([]);
    edges.set([]);
    clearRunState();
    // Clear the canvas-level pipelineId — a cleared canvas has no
    // submitted-pipeline identity, so the running-block gate
    // should not fire on subsequent imports until a new submit happens.
    canvasPipelineId.set(null);
  }

  /**
   * Run goes through the lock flow: with any parameter row still
   * unlocked it opens the lock summary, and the run starts only when the
   * user confirms. It is disabled while the current lock has blocked rows.
   */
  function doRun() {
    requestRun();
  }
</script>

<div class="toolbar">
  <span class="title">Workflow Canvas</span>
  <div class="tabs">
    <button class="tab" class:active={activeTab === 'builder'} onclick={() => onTabChange?.('builder')}>Builder</button>
    <button class="tab" class:active={activeTab === 'registry'} onclick={() => onTabChange?.('registry')}>Registry</button>
    <button class="tab" class:active={activeTab === 'history'} onclick={() => onTabChange?.('history')}>History</button>
  </div>
  <div class="spacer"></div>
  <ArchiveBadge />
  <input class="pipeline-name" type="text" value={$pipelineName}
    oninput={(e: Event) => pipelineName.set((e.target as HTMLInputElement).value)} />
  <div class="actions">
    <button class="btn" onclick={doImport}>Import</button>
    <button class="btn" onclick={doValidate}>Validate</button>
    <button class="btn" onclick={doExport}>Export</button>
    {#if runDisabled}
      <button class="btn btn-stop" onclick={dispatchUserStop}>Stop</button>
    {:else}
      <button class="btn btn-run" onclick={doRun} disabled={runDisabled || runBlocked}
        title={runBlocked ? ['Blocked:', ...$blockedReasons].join('\n') : ''}>&#9654; Run{#if hasDraft} <small class="draft-mode-note">· draft mode</small>{/if}</button>
    {/if}
    <button class="btn btn-muted" onclick={doClear}>Clear</button>
    <button class="btn btn-muted" onclick={undo} title="Undo (Ctrl+Z)">&#8630;</button>
    <button class="btn btn-muted" onclick={redo} title="Redo (Ctrl+Y)">&#8631;</button>
  </div>
</div>

<!-- Status bar -->
{#if $runState.running}
  <div class="status-bar">
    <span>&#9654; Running...</span>
    <span>Nodes: {$nodes.length}</span>
  </div>
{/if}

<style>
  .toolbar {
    display: flex;
    align-items: center;
    padding: 10px 14px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    gap: 14px;
    flex-shrink: 0;
  }
  .title { color: var(--text-primary); font-size: 22px; font-weight: 600; white-space: nowrap; }
  .tabs {
    display: flex;
    gap: 2px;
    background: var(--bg-input);
    padding: 4px;
    border-radius: 6px;
  }
  .tab {
    padding: 6px 16px;
    color: var(--text-secondary);
    font-size: 13px;
    background: none;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .tab.active { background: var(--accent); color: white; }
  .spacer { flex: 1; }
  .pipeline-name {
    background: var(--bg-header);
    padding: 6px 12px;
    border-radius: 4px;
    border: 1px solid var(--border);
    color: var(--text-primary);
    font-size: 13px;
    /* 320px fits most pipeline names; the min-width keeps the action
       buttons on-screen on narrow viewports. */
    width: 320px;
    min-width: 180px;
    outline: none;
  }
  .actions { display: flex; gap: 8px; align-items: center; }
  .btn {
    padding: 6px 10px;
    background: var(--bg-header);
    border-radius: 4px;
    font-size: 13px;
    color: var(--text-primary);
    border: none;
    cursor: pointer;
  }
  .btn:hover { background: var(--border); }
  .btn-run { background: var(--status-ok); color: white; font-weight: 600; padding: 6px 12px; }
  .btn-run:disabled { background: #2e2e32; color: var(--text-muted); cursor: not-allowed; }
  .btn-stop { background: var(--status-failed); color: white; font-weight: 600; }
  .btn-muted { color: var(--text-secondary); }
  .status-bar {
    padding: 5px 14px;
    background: #1e7a3e;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-shrink: 0;
    color: white;
    font-size: 12px;
  }
</style>
