<script lang="ts">
  import { Handle, Position } from '@xyflow/svelte';
  import type { CanvasNodeData, RunStatus } from '../shared/types.js';
  import { deleteNodes, edges as edgesStore, nodes as nodesStore, samples as samplesStore, updateNodeData } from './stores.js';
  import { displaySlotType, slotColor, truncateSlotType } from '../shared/slotColor.js';
  import { draftInvalidReason, isDraftNode, nodeVersion, versionTitle } from './nodeData.js';
  import { DRAFT_NODE_HELP, LATEST_KEPT, REVIEW_IN_REGISTRY, downstreamHelp, draftModeOf } from './drafts.js';
  import { openDraftInRegistry } from '../shared/draftLinks.js';
  import HelpMark from '../shared/HelpMark.svelte';
  import { moduleColor } from '../shared/moduleColor.svelte.js';

  let { data, id, selected }: { data: CanvasNodeData; id: string; selected?: boolean } = $props();

  function handleDelete(event: MouseEvent) {
    event.stopPropagation();
    deleteNodes([id]);
  }

  // The border shows the live run state (SOP canvas-colours, Protected
  // Colours). Pending (dispatched, waiting for a run id) is queued: the
  // active colour, like running. A cache hit keeps the completed border;
  // the footer's CACHED pill tells it apart. Partial (some samples failed)
  // takes the failed colour; its glyph and tally tell it apart.
  const borderColors: Record<RunStatus, string> = {
    idle: 'var(--border-node)',
    pending: 'var(--status-active)',
    running: 'var(--status-active)',
    completed: 'var(--status-ok)',
    cached: 'var(--status-ok)',
    failed: 'var(--status-failed)',
    cancelled: 'var(--status-cancelled)',
    mixed: 'var(--status-failed)',
  };
  // Small text in failed and cancelled uses the ink shade.
  const labelColors: Partial<Record<RunStatus, string>> = {
    failed: 'var(--status-failed-ink)',
    cancelled: 'var(--status-cancelled-ink)',
    mixed: 'var(--status-failed-ink)',
  };

  // The version a method node runs: a green dot beside its number when that
  // is the newest, a yellow one when the node is pinned to an older one.
  let running = $derived(nodeVersion(data));

  // The env's name without its kind (`container:scanpy` → `scanpy`); the
  // chip's hover gives the full value.
  function envName(env: string): string {
    return env.replace(/^[a-z]+:/, '');
  }

  let isSystemNode = $derived(data.nodeType === 'input_selector' || data.nodeType === 'run_reference');

  // System nodes never show a status: their border is always the idle one.
  // Selection never changes the border; it adds a glow in the border's own
  // colour (near-white on the neutral idle border).
  // A node carrying a refusal from the last Validate or Run (SOP
  // canvas-colours: blocked or invalid) takes the failed border over any
  // earlier run's colour, until the next Validate or Run clears it. Its
  // footer says it is refused in place of how that run ended; once the
  // refusals clear, the footer shows the run outcome again.
  let liveStatus = $derived<RunStatus>(isSystemNode ? 'idle' : (data.runStatus ?? 'idle'));
  let refused = $derived(!isSystemNode && !!data.refusals?.length);
  let refusalCount = $derived.by(() => {
    const n = data.refusals?.length ?? 0;
    return `${n} issue${n === 1 ? '' : 's'}`;
  });
  let statusColor = $derived(borderColors[liveStatus] ?? 'var(--border-node)');
  let borderColor = $derived(refused ? 'var(--status-failed)' : statusColor);
  let labelColor = $derived(labelColors[liveStatus] ?? statusColor);
  let plainBorder = $derived(liveStatus === 'idle' && !refused);
  let borderWidth = $derived(plainBorder ? '1px' : '2px');
  let glowStyle = $derived(plainBorder ? '' : `--glow-c: ${borderColor};`);

  // Draft mode (SOP canvas-colours, Drafts): the draft node and every node
  // downstream of it draw a dashed border in the status colour (neutral
  // when not run, failed while refused), with a DRAFT MODE flag: solid on
  // the draft, an outline on the steps after it.
  let isDraft = $derived(isDraftNode(data));
  let draftMode = $derived(isSystemNode ? undefined : draftModeOf($nodesStore, $edgesStore).get(id));
  let draftOwner = $derived(draftMode ? ($nodesStore.find(n => n.id === draftMode!.ownerId)?.data ?? null) : null);
  let invalidReason = $derived(draftInvalidReason(data));
  let dashColor = $derived(plainBorder ? 'var(--dash-neutral)' : borderColor);
  let methodBorderStyle = $derived(draftMode
    ? `border: 2px solid transparent; --dash-color: ${dashColor}; --dash-bw: 2px; --dash-body: var(--node-bg); --glow-c: ${dashColor};`
    : `border: ${borderWidth} solid ${borderColor}; ${glowStyle}`);
  let draftEnv = $derived(isDraft ? data.draft?.env : undefined);

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
    liveStatus === 'pending' ? 'queued...' :
    liveStatus === 'running' ? 'running...' :
    liveStatus === 'completed' ? 'completed' :
    liveStatus === 'cached' ? 'completed' :
    liveStatus === 'failed' ? 'failed' :
    liveStatus === 'cancelled' ? 'cancelled' :
    liveStatus === 'mixed' ? (mixedBadge ? `partial ${mixedBadge}` : 'partial') :
    ''
  );

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
  <div class="custom-node system-node" class:sel-glow={selected} class:glow-neutral={selected} style="border: {borderWidth} solid {borderColor};">
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
                style="background: var(--port); width: 10px; height: 10px; border: 2px solid var(--bg-canvas); z-index: 3;" />
            </div>
          </div>
        {:else if inputRows.length === 1}
          <!-- single-sample compact view: name above, type chip below, both right-aligned at handle -->
          <div class="slot-row">
            <div class="slot-left"></div>
            <div class="slot-right">
              <div class="sample-cell">
                <span class="slot-name sample-name">{inputRows[0].name}</span>
                <span class="type-chip" style="--chip: {slotColor(inputRows[0].type)};">{inputRows[0].type}</span>
              </div>
              <Handle type="source" position={Position.Right} id="output"
                style="background: var(--port); width: 10px; height: 10px; border: 2px solid var(--bg-canvas); z-index: 3;" />
            </div>
          </div>
        {:else}
          <!-- multi-sample: collapsed summary or expanded mini-table; name(s) always at handle -->
          <div class="slot-row">
            <div class="slot-left"></div>
            <div class="slot-right">
              <div class="sample-cell">
                <span class="slot-name sample-name">{inputRows.length} samples</span>
                <span class="type-chip" style="--chip: {slotColor(aggregateType)};">{aggregateType}</span>
              </div>
              <Handle type="source" position={Position.Right} id="output"
                style="background: var(--port); width: 10px; height: 10px; border: 2px solid var(--bg-canvas); z-index: 3;" />
            </div>
          </div>
          {#if !inputCollapsed}
            <div class="sample-table">
              {#each inputRows as row}
                <div class="sample-table-row">
                  <span class="sample-table-name">{row.name}</span>
                  <span class="type-chip type-chip-sm" style="--chip: {slotColor(row.type)};">{row.type}</span>
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
                  <span class="type-chip" style="--chip: {slotColor(output.type)};">{displaySlotType(output.type)}</span>
                </div>
                <Handle type="source" position={Position.Right} id={output.name}
                  style="background: var(--port); width: 10px; height: 10px; border: 2px solid var(--bg-canvas); z-index: 3;" />
              </div>
            </div>
          {/each}
        {/if}
      {/if}
    </div>
    <div class="node-footer">
      {#if statusLabel}
        <span class="status-label" style="color: {labelColor};">{statusLabel}</span>
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
  <div class="custom-node" class:dash-2={!!draftMode} class:draft-node={isDraft} class:draft-mode-node={!!draftMode}
       class:sel-glow={selected} class:glow-neutral={selected && plainBorder} style={methodBorderStyle}>
    <button class="delete-btn" onclick={handleDelete} title="Delete node">&#10005;</button>
    {#if draftMode}
      <span class="draft-flag-wrap">
        <span class="draft-flag" class:strong={draftMode.isDraft} data-testid="draft-flag">DRAFT MODE<HelpMark size={10} light={!draftMode.isDraft} /></span>
        <span class="draft-tip nodrag">
          {draftMode.isDraft ? DRAFT_NODE_HELP : downstreamHelp(draftMode.owner)}
          {#if draftOwner}
            <button type="button" class="lnk nodrag" title="Opens {draftOwner.method}’s Draft run tab in the Registry"
                    onclick={(e: MouseEvent) => { e.stopPropagation(); openDraftInRegistry(draftOwner!.module, draftOwner!.method); }}>{REVIEW_IN_REGISTRY}</button>
          {/if}
        </span>
      </span>
    {/if}
    <!-- Header -->
    <div class="node-header" style="background: {moduleColor(data.module)}; --module: {moduleColor(data.module)};">
      <span class="node-title">{data.label}</span>
      <span class="node-meta">
        <span class="meta-chip">
          <span class="meta-key"><span class="cap-trim">Module</span></span>
          <span class="meta-val"><span class="cap-trim">{data.module}</span></span>
        </span>
        {#if draftEnv}
          <span class="meta-chip node-env" title={draftEnv}>
            <span class="meta-key"><span class="cap-trim">Env</span></span>
            <span class="meta-val"><span class="cap-trim">{envName(draftEnv)}</span></span>
          </span>
        {/if}
        {#if isDraft}
          <span class="meta-chip node-version node-version-draft" title={versionTitle(data)}>
            <span class="meta-key"><span class="cap-trim">Version</span></span>
            <span class="meta-val"><span class="cap-trim">&#9679; DRAFT</span></span>
          </span>
        {/if}
        {#if running?.env}
          <span class="meta-chip node-env" title={running.env}>
            <span class="meta-key"><span class="cap-trim">Env</span></span>
            <span class="meta-val"><span class="cap-trim">{envName(running.env)}</span></span>
          </span>
        {/if}
        {#if running}
          <span
            class="meta-chip node-version"
            class:node-version-newest={running.newest}
            title={versionTitle(data)}
          >
            <span class="meta-key"><span class="cap-trim">Version</span></span>
            <span class="meta-val"><i class="version-dot"></i><span class="cap-trim">v{running.label}</span></span>
          </span>
        {/if}
      </span>
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
                style="background: var(--port); width: 10px; height: 10px; border: 2px solid var(--bg-canvas); z-index: 3;" />
              <div class="slot-stack slot-stack-left">
                <span class="slot-name">{input.name}</span>
                <span class="chip-row">
                  <span class="type-chip type-chip-sm" style="--chip: {slotColor(input.type)};" title={displaySlotType(input.type)}>{truncateSlotType(input.type)}</span>
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
                  <span class="type-chip type-chip-sm" style="--chip: {slotColor(output.type)};" title={displaySlotType(output.type)}>{truncateSlotType(output.type)}</span>
                </span>
              </div>
              <Handle type="source" position={Position.Right} id={output.name}
                style="background: var(--port); width: 10px; height: 10px; border: 2px solid var(--bg-canvas); z-index: 3;" />
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
      <!-- One status, in this slot: refused, then invalid, then the run outcome. -->
      {#if refused}
        <span class="status-label node-problem" style="color: var(--status-failed-ink);"
              title={data.refusals!.join('\n')} data-testid="node-refused">&#9888; refused · {refusalCount}</span>
      {:else if invalidReason}
        <span class="status-label node-problem" style="color: var(--status-failed-ink);"
              title={invalidReason} data-testid="draft-invalid">&#9888; invalid</span>
      {:else if statusLabel}
        <span class="status-label" class:is-pending={liveStatus === 'pending'} style="color: {labelColor};">
          {#if liveStatus === 'completed' || liveStatus === 'cached'}&#10003;{:else if liveStatus === 'failed'}&#10007;{:else if liveStatus === 'running'}&#8635;{:else if liveStatus === 'mixed'}&#9680;{:else if liveStatus === 'pending'}&#9679;{:else if liveStatus === 'cancelled'}&#9632;{/if}
          {statusLabel}
          {#if liveStatus === 'cached' && !draftMode}<span class="cached-pill">&#10227; cached</span>{/if}
          {#if draftMode}<span class="latest-kept">{LATEST_KEPT}</span>{/if}
        </span>
      {:else if draftMode && !draftMode.isDraft}
        <span class="param-count">draft input · not run</span>
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
    background: var(--node-bg);
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
    background: var(--status-failed);
    color: white;
    border: 2px solid var(--node-bg);
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
    filter: brightness(1.1);
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
  /* Module, Env and Version, each a labelled chip like the Inspector
     header's, so they read on any module colour. Zero width with a 100%
     floor: the row takes the width the rest of the node sets and wraps to
     it, rather than widening the node. */
  .node-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 3px;
    width: 0;
    min-width: 100%;
    font-size: 9px;
  }
  /* Trim the text box to the digits' cap height, so flex centring lines the
     text up with the dot rather than with the font's ascender and descender. */
  .cap-trim {
    text-box: trim-both cap alphabetic;
  }
  .meta-chip {
    display: inline-flex;
    align-items: stretch;
    height: 12px;
    max-width: 100%;
    border-radius: 3px;
    overflow: hidden;
    background: rgba(0, 0, 0, 0.3);
    color: #fff;
    line-height: 1;
  }
  .meta-key {
    display: flex;
    align-items: center;
    padding: 0 4px;
    background: rgba(0, 0, 0, 0.25);
    color: rgba(255, 255, 255, 0.65);
    font-size: 6.5px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .meta-val {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 0 4px;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
  }
  .node-version {
    background: rgba(0, 0, 0, 0.5);
  }
  .node-version .meta-val {
    color: #eee;
    font-weight: 600;
  }
  .version-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    flex-shrink: 0;
    /* An older pinned version: a hollow ring; the newest: a filled dot. */
    border: 1px solid var(--version-older);
  }
  .node-version-newest .version-dot {
    background: var(--status-ok);
    border: none;
  }
  .node-slots {
    padding: 6px 0;
  }
  /* A draft node's version chip: ● DRAFT with a light outline. */
  .node-version-draft {
    border: 1px solid var(--marker-light);
    background: rgba(0, 0, 0, 0.55);
  }
  .node-version-draft .meta-val {
    font-weight: 700;
    letter-spacing: 0.06em;
  }
  /* Refused or invalid, in the footer's status slot: glyph and words in the
     failed ink, the reasons on hover. */
  .node-problem {
    font-weight: 600;
    cursor: help;
    white-space: nowrap;
  }
  .latest-kept {
    margin-left: 3px;
    color: var(--text-secondary);
    font-weight: 400;
  }
  /* The DRAFT MODE flag (style guide `.flag`): solid light grey on the
     draft, an outline on the steps after it. */
  .draft-flag-wrap {
    position: absolute;
    top: -10px;
    left: 6px;
    z-index: 4;
  }
  /* The flag straddles the top border and reaches into the node; a
     draft-mode node's header starts below it, with a little air, so the
     flag never covers the title row. */
  .draft-mode-node > .node-header {
    padding-top: 13px;
  }
  .draft-flag {
    display: inline-flex;
    align-items: center;
    font-size: 8px;
    font-weight: 700;
    letter-spacing: 0.05em;
    padding: 0 4px 0 5px;
    line-height: 13px;
    border-radius: 3px;
    white-space: nowrap;
    background: var(--bg-header);
    color: var(--text-primary);
    border: 1px solid var(--dash-neutral);
    box-shadow: 0 0 0 2px var(--bg-canvas);
    cursor: help;
  }
  .draft-flag.strong {
    background: var(--marker-light);
    color: var(--bg-canvas);
    border-color: var(--marker-light);
  }
  .draft-tip {
    display: none;
    position: absolute;
    top: 16px;
    left: 0;
    width: 250px;
    background: var(--bg-panel);
    border: 1px solid var(--border);
    color: var(--text-primary);
    border-radius: 6px;
    padding: 9px 10px;
    font-size: 11px;
    line-height: 1.45;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.6);
    cursor: default;
    white-space: normal;
  }
  .draft-flag-wrap:hover .draft-tip { display: block; }
  .lnk {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent);
    font: inherit;
    cursor: pointer;
    white-space: nowrap;
  }
  .lnk:hover { text-decoration: underline; }
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
    border-top: 1px solid var(--border-node);
  }
  .param-row {
    margin-bottom: 4px;
  }
  .param-label {
    font-size: 8px;
    color: var(--text-secondary);
  }
  .required {
    color: var(--status-failed-ink);
  }
  .param-value {
    background: var(--bg-header);
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid var(--border);
    font-size: 9px;
    color: var(--text-primary);
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
    border-top: 1px solid var(--border-node);
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
  /* The CACHED pill beside "completed": status text on a 15% tint with a
     45% outline, like every status pill. */
  .cached-pill {
    display: inline-flex;
    align-items: center;
    margin-left: 4px;
    background: color-mix(in oklab, var(--status-cached) 15%, transparent);
    color: var(--status-cached);
    border: 1px solid color-mix(in oklab, var(--status-cached) 45%, transparent);
    border-radius: 3px;
    font-size: 8px;
    font-weight: 600;
    padding: 0 5px;
    letter-spacing: 0.3px;
    text-transform: uppercase;
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
    color: var(--accent);
    font-size: 9px;
    cursor: pointer;
    padding: 0;
  }
  /* System node styles */
  .system-header {
    background: var(--fill-system) !important;
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
    border-bottom: 1px solid var(--border-node);
    background: var(--bg-header);
  }
  .chip-strip-override { background: rgba(255, 255, 255, 0.04); }
  .node-chip {
    font-size: 8px;
    padding: 1px 4px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: #aaa;
    font-family: Consolas, monospace;
  }
  .node-chip-override {
    color: #f2f2f2;
    border-color: var(--dash-neutral);
  }
  .node-chip-more {
    font-size: 8px;
    color: var(--text-muted);
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
    /* A slot type's palette colour, on its chip only (never the port). */
    color: color-mix(in srgb, var(--chip, #808080), #fff 45%);
    background: color-mix(in srgb, var(--chip, #808080) 18%, transparent);
    border: 1px solid color-mix(in srgb, var(--chip, #808080) 55%, transparent);
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
    color: var(--text-secondary);
    font-size: 9px;
    margin-left: 2px;
  }
  .sample-table {
    border-top: 1px dashed var(--border-node);
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
  .info-icon:hover { color: var(--accent); }
  .info-icon svg { display: block; }
</style>
