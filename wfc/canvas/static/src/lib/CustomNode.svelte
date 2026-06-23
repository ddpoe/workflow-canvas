<script lang="ts">
  import { Handle, Position } from '@xyflow/svelte';
  import type { CanvasNodeData, RunStatus } from './types.js';
  import { deleteNodes, samples as samplesStore, updateNodeData } from './stores.js';
  import { pushState } from './history.js';

  let { data, id, selected }: { data: CanvasNodeData; id: string; selected?: boolean } = $props();

  function handleDelete(event: MouseEvent) {
    event.stopPropagation();
    pushState();
    deleteNodes([id]);
  }

  const borderColors: Record<RunStatus, string> = {
    idle: '#4a4a4a',
    // Pending = run dispatched, waiting on Snakemake to allocate runId.
    // Lit blue (vs idle's flat gray) so the user gets immediate visual
    // confirmation the click registered, even before the orchestrator
    // reports anything back.
    pending: '#5DA9D9',
    running: '#E9A847',
    completed: '#50C878',
    failed: '#E74C3C',
    cancelled: '#7f8ea3',
    // Mixed = some fan-out samples completed, others failed. Gold tone
    // is close to "running" amber but distinct enough for accessibility.
    mixed: '#D89E2E',
  };

  let borderColor = $derived(selected ? '#4A90D9' : borderColors[data.runStatus] || '#4a4a4a');
  let borderWidth = $derived(
    selected
      || data.runStatus === 'pending'
      || data.runStatus === 'running'
      || data.runStatus === 'completed'
      || data.runStatus === 'failed'
      || data.runStatus === 'mixed'
      ? '2px' : '1px'
  );

  // Short "3/4 ✓" badge for mixed fan-out — the tally fields count
  // per-sample runs, so (completed)/(completed+failed) reads naturally.
  let mixedBadge = $derived((() => {
    if (data.runStatus !== 'mixed' || !data.runTally) return '';
    const c = data.runTally.completed ?? 0;
    const f = data.runTally.failed ?? 0;
    const total = c + f;
    return total > 0 ? `${c}/${total} ✓` : '';
  })());

  let statusLabel = $derived(
    data.runStatus === 'pending' ? 'pending...' :
    data.runStatus === 'running' ? 'running...' :
    data.runStatus === 'completed' ? 'completed' :
    data.runStatus === 'failed' ? 'failed' :
    data.runStatus === 'cancelled' ? 'cancelled' :
    data.runStatus === 'mixed' ? (mixedBadge ? `mixed ${mixedBadge}` : 'mixed') :
    selected ? 'selected' : ''
  );

  let isSystemNode = $derived(data.nodeType === 'input_selector' || data.nodeType === 'run_reference');

  // Match ValueList.displayValue: render dict/list params as JSON, not "[object Object]".
  function formatParamForCard(v: unknown): string {
    if (v === undefined || v === null) return '';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'object') {
      try { return JSON.stringify(v); } catch { return String(v); }
    }
    return String(v);
  }

  // Per-sample type lookup (file_type from registered samples).
  let sampleTypeMap = $derived((() => {
    const m: Record<string, string> = {};
    for (const s of $samplesStore) m[s.name] = s.file_type;
    return m;
  })());

  let inputRows = $derived((data.selectedSamples ?? []).map(name => ({
    name,
    type: sampleTypeMap[name] ?? '?',
  })));

  let aggregateType = $derived((() => {
    const types = new Set(inputRows.map(r => r.type));
    if (types.size === 0) return '';
    if (types.size === 1) return [...types][0];
    return 'mixed';
  })());

  let fanMode = $derived(data.fanMode ?? 'out');
  let inputCollapsed = $derived(data.inputCollapsed ?? false);

  function toggleInputCollapsed(e: MouseEvent) {
    e.stopPropagation();
    updateNodeData(id, { inputCollapsed: !(data.inputCollapsed ?? false) });
  }

  // Build read-only chip strip summary from variants + sampleOverrides.
  let chipSummary = $derived((() => {
    const chips: { label: string; override?: boolean }[] = [];
    const variants = data.variants ?? {};
    for (const [paramName, pv] of Object.entries(variants)) {
      for (const vname of Object.keys(pv ?? {})) {
        chips.push({ label: `${paramName}:${vname}` });
      }
    }
    const overrides = data.sampleOverrides ?? {};
    for (const sample of Object.keys(overrides)) {
      if (Object.keys(overrides[sample] ?? {}).length > 0) {
        chips.push({ label: `${sample}*`, override: true });
      }
    }
    return chips;
  })());
  let hasOverride = $derived(chipSummary.some(c => c.override));
</script>

{#if isSystemNode}
  <!-- ═══ System Node (Input Selector / Run Reference) ═══ -->
  <div class="custom-node system-node" style="border: {borderWidth} solid {borderColor};">
    <button class="delete-btn" onclick={handleDelete} title="Delete node">&#10005;</button>
    <div class="node-header system-header">
      <div class="system-icon">
        {#if data.nodeType === 'input_selector'}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        {:else}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2">
            <polyline points="16 3 21 3 21 8"/>
            <line x1="4" y1="20" x2="21" y2="3"/>
            <polyline points="21 16 21 21 16 21"/>
            <line x1="15" y1="15" x2="21" y2="21"/>
            <line x1="4" y1="4" x2="9" y2="9"/>
          </svg>
        {/if}
      </div>
      <div class="system-title-group">
        <span class="node-title">{data.label}</span>
        <span class="node-module system-type">
          {data.nodeType === 'input_selector' ? 'Input Selector' : 'Run Reference'}
        </span>
      </div>
      {#if data.nodeType === 'input_selector' && inputRows.length > 1}
        <span class="fan-tag" title={fanMode === 'out'
            ? 'Fan-out: each sample spawns a parallel pipeline run'
            : 'Fan-in: all samples bundled as one multi-input'}>
          {fanMode === 'out' ? 'FAN-OUT' : 'FAN-IN'}
        </span>
      {/if}
    </div>
    <div class="node-slots">
      {#if data.nodeType === 'input_selector'}
        {#if inputRows.length === 0}
          <!-- empty state -->
          <div class="slot-row">
            <div class="slot-left"></div>
            <div class="slot-right">
              <div class="sample-cell">
                <span class="slot-name placeholder">no sample selected</span>
              </div>
              <Handle type="source" position={Position.Right} id="output"
                style="background: #1ABC9C; width: 8px; height: 8px; border: none;" />
            </div>
          </div>
        {:else if inputRows.length === 1}
          <!-- single-sample compact view: name above, type chip below, both right-aligned at handle -->
          <div class="slot-row">
            <div class="slot-left"></div>
            <div class="slot-right">
              <div class="sample-cell">
                <span class="slot-name sample-name">{inputRows[0].name}</span>
                <span class="type-chip">{inputRows[0].type}</span>
              </div>
              <Handle type="source" position={Position.Right} id="output"
                style="background: #1ABC9C; width: 8px; height: 8px; border: none;" />
            </div>
          </div>
        {:else}
          <!-- multi-sample: collapsed summary or expanded mini-table; name(s) always at handle -->
          <div class="slot-row">
            <div class="slot-left"></div>
            <div class="slot-right">
              <div class="sample-cell">
                <span class="slot-name sample-name">{inputRows.length} samples</span>
                <span class="type-chip">{aggregateType}</span>
              </div>
              <Handle type="source" position={Position.Right} id="output"
                style="background: #1ABC9C; width: 8px; height: 8px; border: none;" />
            </div>
          </div>
          {#if !inputCollapsed}
            <div class="sample-table">
              {#each inputRows as row}
                <div class="sample-table-row">
                  <span class="sample-table-name">{row.name}</span>
                  <span class="type-chip type-chip-sm">{row.type}</span>
                </div>
              {/each}
            </div>
          {/if}
        {/if}
      {:else}
        {#if (data.outputs ?? []).length === 0}
          <div class="slot-row">
            <div class="slot-left"></div>
            <div class="slot-right">
              <div class="sample-cell">
                <span class="slot-name placeholder">no run selected</span>
              </div>
            </div>
          </div>
        {:else}
          {#each data.outputs as output}
            <div class="slot-row">
              <div class="slot-left"></div>
              <div class="slot-right">
                <div class="sample-cell">
                  <span class="slot-name sample-name">{output.name}</span>
                  <span class="type-chip">{output.type}</span>
                </div>
                <Handle type="source" position={Position.Right} id={output.name}
                  style="background: #1ABC9C; width: 8px; height: 8px; border: none;" />
              </div>
            </div>
          {/each}
        {/if}
      {/if}
    </div>
    <div class="node-footer">
      {#if statusLabel}
        <span class="status-label" style="color: {borderColor};">{statusLabel}</span>
      {:else if data.nodeType === 'input_selector'}
        <span class="param-count">{inputRows.length} sample{inputRows.length === 1 ? '' : 's'}</span>
      {:else}
        <span class="param-count">{data.selectedRunId ? 'run selected' : 'no run'}</span>
      {/if}
      {#if data.nodeType === 'input_selector' && inputRows.length > 1}
        <button class="expand-toggle" onclick={toggleInputCollapsed}>
          {inputCollapsed ? '\u25BC expand' : '\u25B2 collapse'}
        </button>
      {/if}
    </div>
  </div>
{:else}
  <!-- ═══ Method Node (existing) ═══ -->
  <div class="custom-node" style="border: {borderWidth} solid {borderColor};">
    <button class="delete-btn" onclick={handleDelete} title="Delete node">&#10005;</button>
    <!-- Header -->
    <div class="node-header" style="background: {data.color};">
      <span class="node-title">{data.label}</span>
      <span class="node-module">{data.module}{data.version ? ' \u00b7 v' + data.version : ''}</span>
    </div>

    <!-- Chip strip (read-only) -->
    {#if chipSummary.length > 0}
      <div class="chip-strip" class:chip-strip-override={hasOverride}>
        {#each chipSummary.slice(0, 4) as chip}
          <span class="node-chip" class:node-chip-override={chip.override}>{chip.label}</span>
        {/each}
        {#if chipSummary.length > 4}
          <span class="node-chip-more">+{chipSummary.length - 4}</span>
        {/if}
      </div>
    {/if}

    <!-- Slots: independent left + right columns. Each side stacks its
         own slots top-to-bottom; no index-pairing across sides. -->
    <div class="node-slots">
      <div class="slots-grid">
        <div class="slots-col slots-col-left">
          {#each data.inputs as input}
            <div class="slot-row-side">
              <Handle type="target" position={Position.Left} id={input.name}
                style="background: {input.type === 'csv' ? '#F39C12' : input.type === 'label' ? '#E9A847' : '#F39C12'}; width: 8px; height: 8px; border: none;" />
              <div class="slot-stack slot-stack-left">
                <span class="slot-name">{input.name}</span>
                <span class="chip-row">
                  <span class="type-chip type-chip-sm">{input.type}</span>
                  {#if input.description}
                    <span class="info-icon" title={input.description}>
                      <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true">
                        <circle cx="6" cy="6" r="5" fill="none" stroke="currentColor" stroke-width="1"/>
                        <text x="6" y="9" text-anchor="middle" font-size="8" font-family="Georgia, serif" font-style="italic" fill="currentColor">i</text>
                      </svg>
                    </span>
                  {/if}
                </span>
              </div>
            </div>
          {/each}
        </div>
        <div class="slots-col slots-col-right">
          {#each data.outputs as output}
            <div class="slot-row-side slot-row-side-right">
              <div class="slot-stack slot-stack-right">
                <span class="slot-name">{output.name}</span>
                <span class="chip-row">
                  {#if output.description}
                    <span class="info-icon" title={output.description}>
                      <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true">
                        <circle cx="6" cy="6" r="5" fill="none" stroke="currentColor" stroke-width="1"/>
                        <text x="6" y="9" text-anchor="middle" font-size="8" font-family="Georgia, serif" font-style="italic" fill="currentColor">i</text>
                      </svg>
                    </span>
                  {/if}
                  <span class="type-chip type-chip-sm">{output.type}</span>
                </span>
              </div>
              <Handle type="source" position={Position.Right} id={output.name}
                style="background: {output.type === 'csv' ? '#F39C12' : output.type === 'label' ? '#E9A847' : '#F39C12'}; width: 8px; height: 8px; border: none;" />
            </div>
          {/each}
        </div>
      </div>
    </div>

    <!-- Expanded params -->
    {#if data.expanded}
      <div class="node-params">
        {#each data.params as param}
          <div class="param-row">
            <span class="param-label">{param.name}{#if param.required} <span class="required">*</span>{/if}</span>
            <div class="param-value">{formatParamForCard(data.paramValues[param.name] ?? param.default)}{#if data.paramValues[param.name] === undefined && param.default === undefined}<span class="placeholder">{param.required ? 'required' : '—'}</span>{/if}</div>
          </div>
        {/each}
      </div>
    {/if}

    <!-- Footer -->
    <div class="node-footer">
      {#if statusLabel}
        <span class="status-label" class:is-pending={data.runStatus === 'pending'} style="color: {borderColor};">
          {#if data.runStatus === 'completed'}&#10003;{:else if data.runStatus === 'failed'}&#10007;{:else if data.runStatus === 'running'}&#8635;{:else if data.runStatus === 'mixed'}&#9888;{:else if data.runStatus === 'pending'}&#9679;{:else if selected}&#9679;{/if}
          {statusLabel}
        </span>
      {:else}
        <span class="param-count">{data.params.length} params</span>
      {/if}
      <button class="expand-toggle" onclick={() => { data.expanded = !data.expanded; }}>
        {data.expanded ? '\u25B2 collapse' : '\u25BC expand'}
      </button>
    </div>
  </div>
{/if}

<style>
  .custom-node {
    background: #353535;
    border-radius: 6px;
    overflow: visible;
    min-width: 170px;
    font-family: 'Segoe UI', sans-serif;
    cursor: pointer;
    position: relative;
  }
  .delete-btn {
    position: absolute;
    top: -8px;
    right: -8px;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: #E74C3C;
    color: white;
    border: 2px solid #353535;
    font-size: 10px;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.15s;
    z-index: 10;
    padding: 0;
  }
  .custom-node:hover .delete-btn {
    opacity: 1;
  }
  .delete-btn:hover {
    background: #c0392b;
    transform: scale(1.1);
  }
  .node-header {
    padding: 5px 10px;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .node-title {
    display: block;
    color: white;
    font-weight: 600;
    font-size: 12px;
  }
  .node-module {
    display: block;
    color: rgba(255,255,255,0.6);
    font-size: 9px;
  }
  .node-slots {
    padding: 6px 0;
  }
  .slot-row {
    display: flex;
    justify-content: space-between;
    padding: 3px 10px;
    position: relative;
  }
  .slot-left, .slot-right {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .slot-name {
    color: #aaa;
    font-size: 10px;
  }
  .node-params {
    padding: 6px 10px 8px;
    border-top: 1px solid #4a4a4a;
  }
  .param-row {
    margin-bottom: 4px;
  }
  .param-label {
    font-size: 8px;
    color: #888;
  }
  .required {
    color: #E74C3C;
  }
  .param-value {
    background: #2d2d30;
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid #3e3e42;
    font-size: 9px;
    color: #ccc;
    margin-top: 1px;
    min-height: 16px;
    line-height: 12px;
  }
  .placeholder {
    color: #555;
    font-style: italic;
  }
  .node-footer {
    padding: 3px 10px 5px;
    border-top: 1px solid #4a4a4a;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .param-count {
    color: #555;
    font-size: 9px;
  }
  .status-label {
    font-size: 9px;
  }
  .status-label.is-pending {
    animation: pending-pulse 1.4s ease-in-out infinite;
  }
  @keyframes pending-pulse {
    0%, 100% { opacity: 0.55; }
    50%      { opacity: 1.0; }
  }
  .expand-toggle {
    background: none;
    border: none;
    color: #4A90D9;
    font-size: 9px;
    cursor: pointer;
    padding: 0;
  }
  /* System node styles */
  .system-node {
    border-color: #1ABC9C;
  }
  .system-header {
    background: linear-gradient(135deg, #1ABC9C, #16A085) !important;
    flex-direction: row !important;
    align-items: center;
    gap: 6px;
  }
  .system-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .system-title-group {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .chip-strip {
    padding: 3px 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
    border-bottom: 1px solid #4a4a4a;
    background: #2d2d30;
  }
  .chip-strip-override { background: rgba(233, 168, 71, 0.08); }
  .node-chip {
    font-size: 8px;
    padding: 1px 4px;
    background: #1e1e1e;
    border: 1px solid #3e3e42;
    border-radius: 6px;
    color: #aaa;
    font-family: Consolas, monospace;
  }
  .node-chip-override {
    color: #E9A847;
    border-color: rgba(233, 168, 71, 0.4);
  }
  .node-chip-more {
    font-size: 8px;
    color: #666;
    padding: 1px 2px;
  }
  .system-type {
    color: rgba(255,255,255,0.7) !important;
    font-size: 8px !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .fan-tag {
    margin-left: auto;
    font-size: 8px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: white;
    background: rgba(0,0,0,0.25);
    padding: 2px 5px;
    border-radius: 3px;
    flex-shrink: 0;
  }
  /* Sample cell — name + type chip stacked, right-aligned at output handle. */
  .sample-cell {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 2px;
    line-height: 1.1;
  }
  .sample-name {
    font-weight: 500;
    color: #d0d0d0;
  }
  .type-chip {
    font-size: 7px;
    text-transform: lowercase;
    letter-spacing: 0.2px;
    color: rgba(26, 188, 156, 0.85);
    background: rgba(26, 188, 156, 0.1);
    border: 1px solid rgba(26, 188, 156, 0.3);
    border-radius: 3px;
    padding: 0 3px;
    line-height: 11px;
    font-family: Consolas, monospace;
  }
  .type-chip-sm {
    font-size: 6.5px;
    line-height: 10px;
    padding: 0 2px;
  }
  .collapse-btn {
    background: none;
    border: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 2px;
    cursor: pointer;
    color: inherit;
    font: inherit;
  }
  .collapse-btn:hover .sample-name { color: #fff; }
  .caret {
    color: #888;
    font-size: 9px;
    margin-left: 2px;
  }
  .sample-table {
    border-top: 1px dashed #4a4a4a;
    padding: 4px 10px 2px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    background: rgba(0,0,0,0.15);
  }
  .sample-table-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
  }
  .sample-table-name {
    font-size: 9px;
    color: #bbb;
    font-family: Consolas, monospace;
  }
  /* Slot stack: name on top, type chip beneath, aligned to its handle side. */
  .slot-stack {
    display: flex;
    flex-direction: column;
    gap: 2px;
    line-height: 1.1;
  }
  .slot-stack-left { align-items: flex-start; }
  .slot-stack-right { align-items: flex-end; }
  /* Independent left/right slot columns — each side stacks its own
     handles top-to-bottom; no index-pairing across columns. */
  .slots-grid {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }
  .slots-col {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .slots-col-left { align-items: flex-start; }
  .slots-col-right { align-items: flex-end; }
  .slot-row-side {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 10px;
    position: relative;
  }
  /* Chip row — sits beside the type chip; same row on both sides. */
  .chip-row {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  /* SVG-drawn info badge — tooltip via parent's title attr. */
  .info-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: rgba(255, 255, 255, 0.5);
    cursor: help;
    flex-shrink: 0;
  }
  .info-icon:hover { color: #4A90D9; }
  .info-icon svg { display: block; }
</style>
