<script lang="ts">
  /**
   * The Inspector's Run Reference body: pick a finished run to graft onto the
   * canvas, or review the one already referenced. The header above it is the
   * Inspector's.
   */
  import type { Node } from '@xyflow/svelte';
  import { get } from 'svelte/store';
  import { modules, runState, updateNodeData } from '../builder/stores.js';
  // The one relative-time function. The panels pass ISO strings, the
  // History views epoch milliseconds, and it takes both.
  import { formatRelativeTime } from '../history/historyUtils.js';
  import type { CanvasNodeData, CompletedRun } from '../shared/types.js';
  import { fetchCompletedRuns as fetchCompletedRunsRequest } from './api.js';

  let { node, data }: {
    node: Node<CanvasNodeData> | undefined;
    data: CanvasNodeData;
  } = $props();

  let runRefTab = $state<'select' | 'output'>('select');
  let runSearchQuery = $state('');
  let stagedRunId = $state<string>('');
  let expandedRunIds = $state<Record<string, boolean>>({});
  let completedRuns = $state<CompletedRun[]>([]);
  let lastInitNodeId = $state<string | null>(null);

  let filteredRuns = $derived(
    completedRuns.filter(r =>
      r.method.toLowerCase().includes(runSearchQuery.toLowerCase()) ||
      r.module.toLowerCase().includes(runSearchQuery.toLowerCase()) ||
      (r.sample ?? '').toLowerCase().includes(runSearchQuery.toLowerCase()) ||
      r.id.includes(runSearchQuery)
    )
  );

  function moduleColor(moduleName: string): string {
    const mod = $modules.find(m => m.name === moduleName);
    return mod?.color ?? '#888';
  }

  function updateLabel(value: string) {
    if (!node) return;
    updateNodeData(node.id, { label: value });
  }

  async function fetchCompletedRuns() {
    try {
      const runs = await fetchCompletedRunsRequest();
      if (runs) completedRuns = runs;
    } catch { /* noop */ }
  }

  function initStagedRun() {
    if (!node) return;
    stagedRunId = node.data.selectedRunId ?? '';
  }

  function toggleExpandRun(runId: string) {
    expandedRunIds = { ...expandedRunIds, [runId]: !expandedRunIds[runId] };
  }

  function acceptRunSelection() {
    if (!node || !stagedRunId) return;
    const run = completedRuns.find(r => r.id === stagedRunId);
    if (!run) return;
    // Every output slot of the referenced run becomes a handle on the node.
    // Names come from run.output_slots (authoritative: what the run actually
    // produced); types come from the current method contract with a graceful
    // fallback when the contract has drifted since the run executed.
    const mods = get(modules);
    const methodDef = mods
      .find(m => m.name === run.module)
      ?.methods.find(me => me.name === run.method);
    const outputs = run.output_slots.length > 0
      ? run.output_slots.map(slotName => {
          const def = methodDef?.outputs.find(o => o.name === slotName);
          return {
            name: slotName,
            type: def?.type ?? 'csv',
            description: def?.description,
          };
        })
      : [{ name: 'output', type: 'csv' }];
    updateNodeData(node.id, {
      selectedRunId: stagedRunId,
      outputs,
      label: `${run.method} #${run.id}`,
    });
  }

  $effect(() => {
    if (node) {
      const isNewNode = node.id !== lastInitNodeId;
      if (isNewNode) lastInitNodeId = node.id;
      fetchCompletedRuns();
      if (isNewNode) initStagedRun();
    } else {
      lastInitNodeId = null;
    }
  });

  // Refresh completed runs when a pipeline finishes (running -> not running)
  let wasRunning = $state(false);
  $effect(() => {
    const running = $runState.running;
    if (wasRunning && !running) {
      fetchCompletedRuns();
    }
    wasRunning = running;
  });
</script>

<div class="sys-tabs">
  <button class="sys-tab" class:active={runRefTab === 'select'} onclick={() => { runRefTab = 'select'; }}>Select Run</button>
  <button class="sys-tab" class:active={runRefTab === 'output'} onclick={() => { runRefTab = 'output'; }}>Output</button>
</div>
<div class="tab-content">
  {#if runRefTab === 'select'}
    <div class="card-search-bar">
      <input class="card-search-input" type="text" placeholder="Search runs..." bind:value={runSearchQuery} />
      <button class="card-filter-btn" title="Filter options">&#x2699;</button>
    </div>
    {#if filteredRuns.length > 0}
      <div class="card-count">{filteredRuns.length} completed run{filteredRuns.length !== 1 ? 's' : ''}</div>
    {/if}
    {#each filteredRuns as run}
      {@const checked = stagedRunId === run.id}
      {@const expanded = expandedRunIds[run.id] ?? checked}
      {@const paramEntries = Object.entries(run.params ?? {})}
      <div class="card-wrap" class:card-checked-run={checked}
        onclick={() => { stagedRunId = run.id; }}>
        <input type="checkbox" class="corner-check corner-check-run" checked={checked}
          onclick={(e: MouseEvent) => e.stopPropagation()}
          onchange={() => { stagedRunId = run.id; }} />
        <div class="card-row">
          <div>
            <span class="card-name" style="color: {moduleColor(run.module)}">{run.method}</span>
            <span class="card-dot">&middot;</span>
            <span class="card-sample">{run.sample || 'no sample'}</span>
          </div>
          <span class="run-status-dot">&bull;</span>
        </div>
        <div class="card-meta">Run #{run.id}{#if run.finished_at} &middot; {formatRelativeTime(run.finished_at)}{/if}</div>
        {#if expanded && paramEntries.length > 0}
          <table class="params-table"><tbody>
            {#each paramEntries as [key, val]}
              <tr><td class="params-key">{key}</td><td class="params-val">{val}</td></tr>
            {/each}
          </tbody></table>
          {#if run.output_slots.length > 0}
            <div class="output-slots-section">
              <div class="output-slots-label">Outputs</div>
              {#each run.output_slots as slot}
                <span class="type-badge type-badge-orange">{slot}</span>
              {/each}
            </div>
          {/if}
        {:else if !expanded && paramEntries.length > 0}
          <div class="params-summary">{paramEntries.slice(0, 3).map(([k, v]) => `${k}=${v}`).join(', ')}</div>
          <div class="expand-params" onclick={(e: MouseEvent) => { e.stopPropagation(); toggleExpandRun(run.id); }}>
            &#x25B8; {paramEntries.length} param{paramEntries.length !== 1 ? 's' : ''} &mdash; click to expand
          </div>
        {/if}
      </div>
    {/each}
    {#if filteredRuns.length === 0}
      <span class="empty-hint">No completed runs found.</span>
    {/if}
    <button class="accept-btn" onclick={acceptRunSelection} disabled={!stagedRunId}>Accept Selected Run</button>
  {:else}
    <div class="param-form">
      <div class="field">
        <label>Label</label>
        <input type="text" value={data.label} oninput={(e: Event) => updateLabel((e.target as HTMLInputElement).value)} />
      </div>
      {#if data.selectedRunId}
        {@const selectedRun = completedRuns.find(r => r.id === data.selectedRunId)}
        {#if selectedRun}
          <div class="run-detail">
            <span class="detail-label">Method:</span> <span class="detail-value">{selectedRun.method}</span>
            <span class="detail-label">Module:</span> <span class="detail-value">{selectedRun.module}</span>
            <span class="detail-label">Run:</span> <span class="detail-value">#{selectedRun.id}</span>
            {#if selectedRun.output_slots.length > 0}
              <span class="detail-label">Outputs:</span> <span class="detail-value">{selectedRun.output_slots.join(', ')}</span>
            {/if}
          </div>
        {/if}
      {:else}
        <span class="empty-hint">No run selected yet.</span>
      {/if}
    </div>
  {/if}
</div>

<style>
  .tab-content {
    flex: 1;
    padding: 12px;
    overflow-y: auto;
  }
  .param-form { display: flex; flex-direction: column; gap: 10px; }
  .field { display: flex; flex-direction: column; gap: 3px; }
  .field label { font-size: 12px; color: #888; }
  .field input {
    background: #1e1e1e;
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 6px 8px !important;
    color: #ccc;
    font-size: 13px;
    outline: none;
    width: 100%;
    box-sizing: border-box;
    min-height: 28px;
  }
  .field input::placeholder {
    color: #555;
    font-style: italic;
  }
  /* ── System node tabs ── */
  .sys-tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .sys-tab {
    flex: 1;
    padding: 7px;
    text-align: center;
    font-size: 12px;
    color: #666;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
  }
  .sys-tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }
  /* ── Card search bar ── */
  .card-search-bar {
    display: flex;
    gap: 4px;
    margin-bottom: 8px;
  }
  .card-search-input {
    flex: 1;
    padding: 7px 10px;
    background: #1e1e1e;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: #ccc;
    font-size: 13px;
    outline: none;
  }
  .card-filter-btn {
    padding: 7px 8px;
    background: #1e1e1e;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 12px;
    color: #888;
    cursor: pointer;
  }
  .card-filter-btn:hover {
    color: #ccc;
    border-color: var(--accent);
  }
  .card-count {
    font-size: 11px;
    color: #666;
    margin-bottom: 6px;
  }
  /* ── Shared card wrap ── */
  .card-wrap {
    position: relative;
    padding: 8px 10px 8px 28px;
    background: #1e1e1e;
    border: 1px solid var(--border);
    border-radius: 4px;
    margin-bottom: 4px;
    cursor: pointer;
    font-size: 13px;
  }
  .card-wrap:hover {
    border-color: rgba(74, 144, 217, 0.3);
  }
  .corner-check {
    position: absolute;
    left: 7px;
    top: 8px;
    width: 12px;
    height: 12px;
    cursor: pointer;
  }
  .corner-check-run { accent-color: var(--accent); }
  .card-checked-run {
    background: rgba(74, 144, 217, 0.08);
    border-color: rgba(74, 144, 217, 0.3);
  }
  .card-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .card-name {
    font-weight: 600;
    font-size: 13px;
    color: #ccc;
  }
  .card-meta {
    font-size: 11px;
    color: #666;
    margin-top: 2px;
  }
  /* ── Type badges ── */
  .type-badge {
    display: inline-block;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid;
    text-transform: uppercase;
  }
  .type-badge-orange {
    color: #F39C12;
    border-color: rgba(243, 156, 18, 0.3);
    background: rgba(243, 156, 18, 0.1);
  }
  /* ── Accept button ── */
  .accept-btn {
    margin-top: 8px;
    width: 100%;
    padding: 8px;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .accept-btn:hover { background: #3a80c9; }
  .accept-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  /* ── Run card specific ── */
  .card-dot { opacity: 0.3; margin: 0 2px; }
  .card-sample { color: #E9A847; font-size: 13px; }
  .run-status-dot { font-size: 16px; color: #50C878; }
  .params-summary {
    font-size: 11px;
    color: #666;
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-family: Consolas, monospace;
  }
  .expand-params {
    font-size: 10px;
    color: #666;
    margin-top: 2px;
    cursor: pointer;
  }
  .expand-params:hover { color: var(--accent); }
  .params-table {
    margin-top: 4px;
    font-size: 11px;
    font-family: Consolas, monospace;
    width: 100%;
    border-collapse: collapse;
  }
  .params-key { color: #888; padding-right: 6px; white-space: nowrap; }
  .params-val { color: #ccc; }
  .output-slots-section {
    margin-top: 5px;
    padding-top: 4px;
    border-top: 1px solid var(--border);
  }
  .output-slots-label {
    font-size: 10px;
    color: #888;
    text-transform: uppercase;
    margin-bottom: 3px;
  }
  .empty-hint {
    color: #555;
    font-size: 12px;
    font-style: italic;
    padding: 4px;
  }
  .run-detail {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 2px 8px;
    padding: 6px;
    background: #1e1e1e;
    border-radius: 3px;
    border: 1px solid var(--border);
  }
  .detail-label {
    font-size: 11px;
    color: #888;
  }
  .detail-value {
    font-size: 11px;
    color: #ccc;
  }
</style>
