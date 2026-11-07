<script lang="ts">
  import type { WfcRun } from './historyApi.js';
  import {
    runs,
    filters,
    descendantScope,
    clearDescendantScope,
    scopedDescendantForest,
    descendantsCollapsed,
    toggleDescendantCollapsed,
    descendantRows,
    descendantHighlight,
    selectedRunIds,
    selectRun,
    openRunOn,
    toggleRunSelection,
    setFavoriteOptimistic,
  } from './historyStore.js';

  /** What the scope banner calls the run it is scoped to. */
  let scopeLabel = $derived(
    $descendantScope
      ? ($runs.find(r => r.id === $descendantScope)?.runName || $descendantScope)
      : '',
  );
  import {
    statusColor,
    formatRelativeTime,
    formatDuration,
  } from './historyUtils.js';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import HelpMark from '../shared/HelpMark.svelte';
  import { openDraftInRegistry } from '../shared/draftLinks.js';
  import { DRAFT_NODE_HELP, REVIEW_IN_REGISTRY, downstreamHelp } from '../builder/drafts.js';
  import { draftExceptionColor, draftOwner } from './historyDrafts.js';

  /** The status border, coloured only for an exception (SOP: History shows exceptions). */
  function borderColor(run: WfcRun): string {
    switch (run.status) {
      case 'running': return 'var(--status-active)';
      case 'queued': return 'var(--status-active)';
      case 'pending': return 'var(--status-active)';
      case 'failed': return 'var(--status-failed)';
      case 'cancelled': return 'var(--status-cancelled)';
      default: return 'var(--border)';
    }
  }

  /** The body: a 10% tint of the module colour, whatever the status. */
  function bgColor(run: WfcRun): string {
    return `color-mix(in srgb, ${moduleColor(run.module)} 10%, var(--bg-panel))`;
  }

  function handleCardClick(run: WfcRun) {
    if ($filters.selectMode) {
      toggleRunSelection(run.id);
    } else {
      selectRun(run.id);
    }
  }

  function handleStarClick(e: MouseEvent, run: WfcRun) {
    e.stopPropagation();
    setFavoriteOptimistic(run.id, !run.favorite).catch(() => {
      /* revert happens inside the optimistic helper */
    });
  }

  function handleCardKeydown(e: KeyboardEvent, run: WfcRun) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleCardClick(run);
    }
  }
</script>

<div class="descendants-view">
  {#if $descendantScope}
    <div class="scope-banner">
      <span class="scope-text">Descendants of <strong>{scopeLabel}</strong></span>
      <button class="scope-clear" onclick={() => clearDescendantScope()}>Show all runs</button>
    </div>
  {/if}
  {#if $scopedDescendantForest.length === 0}
    <div class="empty-state">
      {#if $descendantScope}
        <p>Nothing to show below this run. Cache-hit runs are not shown here — see Lineages.</p>
      {:else if $runs.length === 0}
        <p>No runs yet. Click Run on the canvas to start one.</p>
      {:else}
        <p>No executed runs match the current filters. Cache-hit runs are not shown here — see Lineages.</p>
      {/if}
    </div>
  {:else}
    {#snippet runCard(run: WfcRun, parentCount: number)}
      {@const step = run.draftStep ?? null}
      {@const dashColor = step ? draftExceptionColor(run.status) : null}
      {@const owner = step ? draftOwner(step) : null}
      <div
        class="tree-card"
        class:cancelled={run.status === 'cancelled'}
        class:ex={borderColor(run) !== 'var(--border)'}
        class:draft-node={!!step}
        class:dash-2={!!dashColor}
        class:selected={$descendantHighlight.has(run.id)}
        class:sel-glow={$descendantHighlight.has(run.id)}
        class:glow-neutral={$descendantHighlight.has(run.id) && borderColor(run) === 'var(--border)'}
        data-testid={step ? 'draft-tree-card' : undefined}
        style="border-color: {borderColor(run)}; border-left-color: {moduleColor(run.module)}; {dashColor ? `--dash-color: ${dashColor}; --dash-body: ${bgColor(run)};` : `background: ${bgColor(run)};`} --glow-c: {borderColor(run)}; --module: {moduleColor(run.module)};"
        role="button"
        tabindex="0"
        onclick={() => handleCardClick(run)}
        onkeydown={(e) => handleCardKeydown(e, run)}
      >
        {#if dashColor}<i class="acc"></i>{/if}
        {#if step && owner}
          <!-- Draft mode: the neutral right-side strip, light on the draft,
               darker on the steps after it. -->
          <span class="strip" class:on={step.isDraft} class:down={!step.isDraft} data-testid="draft-strip">
            <HelpMark size={13} light={!step.isDraft} />
            <span class="strip-tip">
              {step.isDraft ? DRAFT_NODE_HELP : downstreamHelp(owner.method)}
              <button type="button" class="lnk" title="Opens {owner.method}’s Draft run tab in the Registry"
                      onclick={(e: MouseEvent) => { e.stopPropagation(); openDraftInRegistry(owner.module, owner.method); }}>{REVIEW_IN_REGISTRY}</button>
            </span>
          </span>
        {/if}
        <div class="card-line1">
          <span class="status-dot" style="color: {statusColor(run.status)}">{'●'}</span>
          <span class="card-method">{run.method}</span>
          {#if parentCount > 1}
            <span class="parents-pill" title="This run appears under each of its parents">{parentCount} parents</span>
          {/if}
          {#if !step}
            <button
              class="star"
              class:active={run.favorite}
              onclick={(e) => handleStarClick(e, run)}
              title="Toggle favorite"
            >{run.favorite ? '★' : '☆'}</button>
          {/if}
          {#if $filters.selectMode}
            <span class="checkbox">{$selectedRunIds.has(run.id) ? '☑' : '☐'}</span>
          {/if}
        </div>
        {#if run.nid || run.version != null || step?.isDraft}
          <!-- NAME / METHOD: neutral grey pills (style guide `.kv`). -->
          <div class="pls">
            {#if run.nid}
              <span class="kv"><span class="k">NAME</span><span class="v card-nid">{run.nid}</span></span>
            {/if}
            {#if step?.isDraft}
              <span class="kv" title="Method version"><span class="k">METHOD</span><span class="v card-version">DRAFT</span></span>
            {:else if run.version != null}
              <span class="kv" title="Method version"><span class="k">METHOD</span><span class="v card-version">v{run.version}</span></span>
            {/if}
          </div>
        {/if}
        <div class="card-line2">
          {#if run.status === 'cancelled'}
            cancelled {'—'} upstream failed
          {:else if run.status === 'failed'}
            <!-- The card says it failed, not why; the link opens the log. -->
            {formatRelativeTime(run.timestamp)} {'·'} <button type="button" class="failed-link"
              title="Open this run's output"
              onclick={(e) => { e.stopPropagation(); openRunOn(run.id, 'output'); }}
              onkeydown={(e) => e.stopPropagation()}><span aria-hidden="true">✗</span> Failed</button> after {formatDuration(run.duration)}
          {:else}
            {formatRelativeTime(run.timestamp)} {'·'} {formatDuration(run.duration)}
          {/if}
        </div>
      </div>
    {/snippet}

    {#each $scopedDescendantForest as section, si (section.sample ?? `__nosample__${si}`)}
      <div class="section">
        <div class="section-label">{section.sample || '(no sample)'}</div>
        {#each descendantRows(section.roots, $descendantsCollapsed) as row (row.node.key)}
          <div class="tree-row" style="padding-left: {row.depth * 24}px;">
            {#if row.node.children.length > 0}
              <button
                class="caret"
                onclick={() => toggleDescendantCollapsed(row.node.key)}
                title={row.collapsed ? 'Expand' : 'Collapse'}
              >{row.collapsed ? '▶' : '▼'}</button>
            {:else}
              <span class="caret-spacer"></span>
            {/if}
            {#if row.depth > 0}
              <span class="connector">{row.isLast ? '└─▶' : '├─▶'}</span>
            {/if}
            {@render runCard(row.node.run, row.node.parentCount)}
          </div>
        {/each}
      </div>
    {/each}
  {/if}
</div>

<style>
  .descendants-view {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 40px;
    color: var(--text-muted);
    font-size: 13px;
  }
  .scope-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 6px 10px;
    margin-bottom: 10px;
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 4px;
    font-size: 11px;
    color: var(--text-secondary);
  }
  .scope-text strong {
    color: var(--text-primary);
  }
  .scope-clear {
    background: none;
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 3px 10px;
    color: var(--text-primary);
    font-size: 11px;
    font-family: inherit;
    cursor: pointer;
    flex-shrink: 0;
  }
  .scope-clear:hover {
    border-color: var(--accent);
  }
  .section {
    margin-bottom: 18px;
  }
  .section-label {
    font-size: 11px;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin: 0 0 8px;
  }
  .tree-row {
    display: flex;
    align-items: center;
    margin: 8px 0;
  }
  .caret {
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 9px;
    width: 16px;
    padding: 0;
    flex-shrink: 0;
  }
  .caret-spacer {
    width: 16px;
    flex-shrink: 0;
    display: inline-block;
  }
  .connector {
    color: var(--text-muted);
    font-family: monospace;
    font-size: 13px;
    margin-right: 6px;
    white-space: pre;
    flex-shrink: 0;
  }
  .tree-card {
    border-radius: 6px;
    border: 1px solid var(--border);
    border-left: 4px solid var(--border);
    padding: 8px 12px;
    cursor: pointer;
    min-width: 190px;
    color: var(--text-primary);
    transition: transform 0.08s;
    position: relative;
  }
  .tree-card:hover {
    transform: translateY(-1px);
    /* The lift makes the card its own stacking layer: raise it so its strip's
       help tip draws over the card below instead of under it. */
    z-index: 3;
  }
  /* An exception (style guide `.dn.ex`): a 2px status border, recorded or
     draft mode alike; the module bar keeps its 4px. The padding gives back
     the extra pixel so the node keeps its size. */
  .tree-card.ex {
    border-top-width: 2px;
    border-right-width: 2px;
    border-bottom-width: 2px;
    padding: 7px 11px;
  }
  .tree-card.cancelled {
    opacity: 0.75;
  }
  .tree-card.selected {
  }
  .card-line1 {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .status-dot {
    font-weight: 600;
    font-size: 12px;
    flex-shrink: 0;
  }
  .card-method {
    color: #fff;
    font-weight: 600;
    font-size: 13px;
  }
  /* NAME / METHOD pills (style guide `.kv`): neutral grey on every card. */
  .pls {
    display: flex;
    gap: 6px;
    margin-top: 5px;
    align-items: center;
  }
  .kv {
    display: inline-flex;
    border-radius: 3px;
    font-size: 10px;
    border: 1px solid var(--border-node);
  }
  .kv .k {
    background: var(--border);
    color: #b8b8b8;
    padding: 0 6px;
    letter-spacing: 0.05em;
    font-size: 9px;
    display: flex;
    align-items: center;
    border-radius: 2px 0 0 2px;
  }
  .kv .v {
    padding: 0 7px;
    color: #f2f2f2;
    font-family: Consolas, monospace;
    background: var(--bg-canvas);
    font-weight: 600;
    border-radius: 0 2px 2px 0;
  }
  /* A draft-mode node: room for the right-side strip. */
  .tree-card.draft-node {
    padding-right: 40px;
  }
  /* Dashed only for an exception status: a 2px border, the dashes painted
     by the global .dash-2 utility and the module bar drawn over them. */
  .tree-card.dash-2 {
    border-width: 2px;
    border-left-width: 2px;
    padding: 7px 39px 7px 14px;
  }
  .tree-card.dash-2:not(.sel-glow) {
    background: var(--dash-body);
  }
  .acc {
    position: absolute;
    left: -2px;
    top: -2px;
    bottom: -2px;
    width: 4px;
    border-radius: 6px 0 0 6px;
    background: var(--module);
    z-index: 2;
  }
  .strip {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: 24px;
    border-radius: 0 4px 4px 0;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 2;
    cursor: help;
  }
  .strip.on {
    background: var(--marker-light);
    color: var(--bg-canvas);
  }
  .strip.down {
    background: var(--marker-dark);
    color: var(--text-primary);
  }
  .strip :global(.qm) {
    margin: 0;
  }
  .strip-tip {
    display: none;
    position: absolute;
    top: 50%;
    left: 28px;
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
    z-index: 5;
  }
  .strip:hover .strip-tip { display: block; }
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
  .parents-pill {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 8px;
    border: 1px solid var(--border);
    color: var(--text-secondary);
    white-space: nowrap;
  }
  .star {
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 12px;
    padding: 0;
    line-height: 1;
    margin-left: auto;
  }
  .star.active {
    color: var(--marker-light);
  }
  .star:hover {
    transform: scale(1.2);
  }
  .checkbox {
    font-size: 14px;
    color: var(--accent);
  }
  .card-line2 {
    color: var(--text-secondary);
    font-size: 11px;
    margin-top: 2px;
  }
  .failed-link {
    font: inherit;
    padding: 0;
    background: none;
    border: none;
    color: var(--status-failed-ink);
    font-weight: 600;
    cursor: pointer;
  }
  .failed-link:hover { text-decoration: underline; }
</style>
