<script lang="ts">
  import { nodes, edges, runState, pipelineName, clearRunState,
           setPipelineError, showFlash } from './stores.js';
  import { pushState } from './history.js';
  import { undo, redo } from './history.js';
  import {
    exportPipeline,
    loadPipeline,
    confirmReplaceIfDirty,
    checkRunningBlock,
    canvasPipelineId,
  } from './pipeline.js';
  import { dispatchRun, dispatchUserStop, hasDirtyEditors,
           dirtyEditorIds, awaitAllCommitted } from './machines/root.js';
  import type { PipelineJSON, CanvasNodeData } from './types.js';
  import { get } from 'svelte/store';

  // Run-button enabled/disabled flag derived from the bridged
  // `runState.running`, which root.ts updates on each pipelineRunActor
  // snapshot. Double-click protection is structural (the actor's
  // RUN_CLICKED guard); this just keeps the button visually disabled.
  $: runDisabled = $runState.running;

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
      // SPEC OQ-2 / Task 9: route the dev Toolbar JSON upload through
      // the same running-block + dirty-confirm gates as the History
      // tab's reload actions. Block first (D-10), then confirm dirty.
      // Read the canvas-level pipelineId (review iter 1 root-cause fix):
      // passing `null` here was the bug that let JSON upload bypass the
      // running-pipeline block when the canvas matched an in-flight run.
      if (await checkRunningBlock(get(canvasPipelineId))) return;
      const targetName = pipeline.name || file.name;
      if (!(await confirmReplaceIfDirty(targetName))) return;
      pushState();
      loadPipeline(pipeline);
      // Uploaded JSON is treated as a fresh canvas — the file format
      // (PipelineJSON) carries no pipeline_id field (see types.ts:114),
      // and even when the upload happens to be the same shape as a
      // submitted pipeline.json, the user's "intent" is to load it as
      // unsubmitted (anything else would conflate identity across
      // sessions). Clear the canvas pid.
      canvasPipelineId.set(null);
    };
    input.click();
  }

  async function doValidate() {
    const pipeline = exportPipeline();
    try {
      const resp = await fetch('/api/workflow/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pipeline),
      });
      const result = await resp.json();
      if (result.valid) {
        // Clear any stale error banner; confirm via transient toast so the
        // user gets feedback without a blocking modal.
        setPipelineError(null);
        showFlash('Workflow is valid!', 'success');
      } else {
        setPipelineError({
          kind: 'not_found',
          message: 'Validation errors:\n' + (result.errors ?? []).join('\n'),
          hint: 'Fix the listed issues on the canvas, then click Validate again.',
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
    nodes.set([]);
    edges.set([]);
    clearRunState();
    // Clear the canvas-level pipelineId — a cleared canvas has no
    // submitted-pipeline identity, so the running-block gate (D-10)
    // should not fire on subsequent imports until a new submit happens.
    canvasPipelineId.set(null);
  }

  /**
   * Pre-Run validation. Returns an error string to block the run, or
   * null if OK. Dirty rows trigger a confirm-and-lock dialog rather
   * than an outright block.
   */
  function collectRequiredErrors(): string[] {
    const errors: string[] = [];
    for (const n of get(nodes)) {
      const d = n.data as CanvasNodeData;
      if (!d?.params || d.nodeType !== 'method') continue;
      const label = d.label || n.id;
      for (const p of d.params) {
        if (!p.required) continue;
        const base = d.paramValues?.[p.name];
        const hasBase = base !== undefined && base !== null && base !== '';
        const hasDefault = p.default !== undefined && p.default !== null && p.default !== '';
        const variants = d.variants?.[p.name] ?? {};
        const hasVariant = Object.keys(variants).length > 0;
        if (!hasBase && !hasDefault && !hasVariant) {
          errors.push(`${label}.${p.name} is required`);
        }
      }
    }
    return errors;
  }

  /**
   * Decompose aggregator child IDs (``nodeId::paramName[::suffix]``)
   * into a readable ``node · param`` list. Replaces the legacy
   * `dirtyParams` decomposition; same key shape so the UX is unchanged.
   */
  function describeDirtyRows(keys: Iterable<string>): string[] {
    const $nodes = get(nodes);
    const labelByNode: Record<string, string> = {};
    for (const n of $nodes) {
      labelByNode[n.id] = (n.data as CanvasNodeData)?.label || n.id;
    }
    const out: string[] = [];
    const seen = new Set<string>();
    for (const key of keys) {
      const [nodeId = '?', paramName = '?'] = key.split('::');
      const dedup = `${nodeId}::${paramName}`;
      if (seen.has(dedup)) continue;
      seen.add(dedup);
      const label = labelByNode[nodeId] ?? nodeId;
      out.push(`${label} · ${paramName}`);
    }
    return out;
  }

  async function doRun() {
    if (hasDirtyEditors()) {
      const ids = dirtyEditorIds();
      const count = ids.length;
      const msg = `${count} param row${count === 1 ? '' : 's'} still in edit mode. ` +
                  `OK to lock them and run; Cancel to return to the canvas.`;
      if (!window.confirm(msg)) return;
      // ADR-016 Phase 2 expand: typed transition replaces the legacy
      // `requestCommitAll() + setTimeout(0)` microtask race. The
      // aggregator fans COMMIT to every editing child and we await
      // `allCommitted` before checking for stragglers (rows whose
      // coerce rejected and stayed in `invalid`).
      await awaitAllCommitted();
      if (hasDirtyEditors()) {
        const stillDirty = dirtyEditorIds();
        const rows = describeDirtyRows(stillDirty);
        setPipelineError({
          kind: 'not_found',
          message: `${rows.length} param row${rows.length === 1 ? '' : 's'} could not be locked:\n` +
                   rows.map(r => `  • ${r}`).join('\n'),
          hint: 'Select the node in the Inspector panel — each row shows its specific validation reason inline.',
        });
        return;
      }
    }
    const required = collectRequiredErrors();
    if (required.length > 0) {
      setPipelineError({
        kind: 'not_found',
        message: 'Cannot run — required params missing:\n' +
                 required.map(r => `  • ${r}`).join('\n'),
        hint: 'Fill in the listed params (or set a default) before running.',
      });
      return;
    }
    // Send the pipeline payload to the actor — it owns submission +
    // polling now. Double-click protection is structural: the second
    // click leaves `idle`, so RUN_CLICKED is silently dropped (visible
    // in the Stately Inspector as a rejected event).
    setPipelineError(null);
    const pipeline = exportPipeline();
    dispatchRun(pipeline);
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
  <input class="pipeline-name" type="text" value={$pipelineName}
    oninput={(e: Event) => pipelineName.set((e.target as HTMLInputElement).value)} />
  <div class="actions">
    <button class="btn" onclick={doImport}>Import</button>
    <button class="btn" onclick={doValidate}>Validate</button>
    <button class="btn" onclick={doExport}>Export</button>
    {#if runDisabled}
      <button class="btn btn-stop" onclick={dispatchUserStop}>Stop</button>
    {:else}
      <button class="btn btn-run" onclick={doRun} disabled={runDisabled}>&#9654; Run</button>
    {/if}
    <button class="btn btn-muted" onclick={doClear}>Clear</button>
    <button class="btn btn-muted" onclick={undo} title="Undo (Ctrl+Z)">&#8630;</button>
    <button class="btn btn-muted" onclick={redo} title="Redo (Ctrl+Y)">&#8631;</button>
  </div>
</div>

<!-- Status bar -->
{#if $runState.running}
  <div class="status-bar">
    <span>&#9654; Running... {#if $runState.currentStep}step {$runState.completedSteps ?? 0}/{$runState.totalSteps ?? '?'} — {$runState.currentStep}{/if}</span>
    <span>Nodes: {$nodes.length}</span>
  </div>
{/if}

<style>
  .toolbar {
    display: flex;
    align-items: center;
    padding: 10px 14px;
    background: #252526;
    border-bottom: 1px solid #3e3e42;
    gap: 14px;
    flex-shrink: 0;
  }
  .title { color: #ccc; font-size: 22px; font-weight: 600; white-space: nowrap; }
  .tabs {
    display: flex;
    gap: 2px;
    background: #1e1e1e;
    padding: 4px;
    border-radius: 6px;
  }
  .tab {
    padding: 6px 16px;
    color: #888;
    font-size: 13px;
    background: none;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .tab.active { background: #4A90D9; color: white; }
  .spacer { flex: 1; }
  .pipeline-name {
    background: #2d2d30;
    padding: 6px 12px;
    border-radius: 4px;
    border: 1px solid #3e3e42;
    color: #ccc;
    font-size: 13px;
    /* 140px was too short for most pipeline names; 320 + min gives
       enough room without pushing the action buttons off-screen on
       narrow viewports. */
    width: 320px;
    min-width: 180px;
    outline: none;
  }
  .actions { display: flex; gap: 8px; align-items: center; }
  .btn {
    padding: 6px 10px;
    background: #2d2d30;
    border-radius: 4px;
    font-size: 13px;
    color: #ccc;
    border: none;
    cursor: pointer;
  }
  .btn:hover { background: #3e3e42; }
  .btn-run { background: #50C878; color: white; font-weight: 600; padding: 6px 12px; }
  .btn-stop { background: #E74C3C; color: white; font-weight: 600; }
  .btn-muted { color: #888; }
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
