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
    getModuleColor,
    hslToRgba,
    statusColor,
    formatRelativeTime,
    formatDuration,
  } from './historyUtils.js';

  function borderColor(run: WfcRun): string {
    switch (run.status) {
      case 'running': return '#E9A847';
      case 'failed': return '#E74C3C';
      case 'cancelled': return '#7f8ea3';
      case 'success': return getModuleColor(run.module);
      default: return '#666';
    }
  }

  function bgColor(run: WfcRun): string {
    switch (run.status) {
      case 'running': return 'rgba(233, 168, 71, 0.12)';
      case 'failed': return 'rgba(231, 76, 60, 0.10)';
      case 'cancelled': return 'rgba(127, 142, 163, 0.08)';
      case 'success': return hslToRgba(getModuleColor(run.module), 0.08);
      default: return 'transparent';
    }
  }

  function errorFirstLine(run: WfcRun): string {
    return run.error_message ? run.error_message.split('\n')[0] : '';
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
      <div
        class="tree-card"
        class:cancelled={run.status === 'cancelled'}
        class:selected={$descendantHighlight.has(run.id)}
        style="border-left-color: {borderColor(run)}; background: {bgColor(run)};"
        role="button"
        tabindex="0"
        onclick={() => handleCardClick(run)}
        onkeydown={(e) => handleCardKeydown(e, run)}
      >
        <div class="card-line1">
          <span class="status-dot" style="color: {statusColor(run.status)}">{'●'}</span>
          <span class="card-method">{run.method}</span>
          <span class="card-version">{run.nid || `v${run.version}`}</span>
          {#if parentCount > 1}
            <span class="parents-pill" title="This run appears under each of its parents">{parentCount} parents</span>
          {/if}
          <button
            class="star"
            class:active={run.favorite}
            onclick={(e) => handleStarClick(e, run)}
            title="Toggle favorite"
          >{run.favorite ? '★' : '☆'}</button>
          {#if $filters.selectMode}
            <span class="checkbox">{$selectedRunIds.has(run.id) ? '☑' : '☐'}</span>
          {/if}
        </div>
        <div class="card-line2">
          {#if run.status === 'cancelled'}
            cancelled {'—'} upstream failed
          {:else if run.status === 'failed'}
            {formatRelativeTime(run.timestamp)} {'·'} failed after {formatDuration(run.duration)}
          {:else}
            {formatRelativeTime(run.timestamp)} {'·'} {formatDuration(run.duration)}
          {/if}
        </div>
        {#if run.status === 'failed' && errorFirstLine(run)}
          <div class="err-inline">{errorFirstLine(run)}</div>
        {/if}
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
    color: var(--text-muted, #666);
    font-size: 13px;
  }
  .scope-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 6px 10px;
    margin-bottom: 10px;
    background: var(--bg-panel, #252526);
    border: 1px solid var(--border, #3e3e42);
    border-left: 3px solid var(--accent, #4A90D9);
    border-radius: 4px;
    font-size: 11px;
    color: var(--text-secondary, #888);
  }
  .scope-text strong {
    color: var(--text-primary, #ccc);
  }
  .scope-clear {
    background: none;
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    padding: 3px 10px;
    color: var(--text-primary, #ccc);
    font-size: 11px;
    font-family: inherit;
    cursor: pointer;
    flex-shrink: 0;
  }
  .scope-clear:hover {
    border-color: var(--accent, #4A90D9);
  }
  .section {
    margin-bottom: 18px;
  }
  .section-label {
    font-size: 11px;
    color: var(--text-secondary, #888);
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
    color: var(--text-muted, #666);
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
    color: var(--text-muted, #666);
    font-family: monospace;
    font-size: 13px;
    margin-right: 6px;
    white-space: pre;
    flex-shrink: 0;
  }
  .tree-card {
    border-radius: 6px;
    border-left: 4px solid var(--accent, #4A90D9);
    padding: 8px 12px;
    cursor: pointer;
    min-width: 190px;
    color: var(--text-primary, #ccc);
    transition: transform 0.08s;
    position: relative;
  }
  .tree-card:hover {
    transform: translateY(-1px);
  }
  .tree-card.cancelled {
    opacity: 0.75;
  }
  .tree-card.selected {
    outline: 2px solid var(--accent, #4A90D9);
    outline-offset: 1px;
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
  .card-version {
    color: var(--text-secondary, #888);
    font-size: 11px;
  }
  .parents-pill {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 8px;
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-secondary, #888);
    white-space: nowrap;
  }
  .star {
    background: none;
    border: none;
    color: var(--text-muted, #666);
    cursor: pointer;
    font-size: 12px;
    padding: 0;
    line-height: 1;
    margin-left: auto;
  }
  .star.active {
    color: var(--color-running, #E9A847);
  }
  .star:hover {
    transform: scale(1.2);
  }
  .checkbox {
    font-size: 14px;
    color: var(--accent, #4A90D9);
  }
  .card-line2 {
    color: var(--text-secondary, #888);
    font-size: 11px;
    margin-top: 2px;
  }
  .err-inline {
    margin-top: 6px;
    padding: 4px 8px;
    background: rgba(231, 76, 60, 0.10);
    border-radius: 3px;
    color: var(--color-failed, #E74C3C);
    font-size: 11px;
  }
</style>
