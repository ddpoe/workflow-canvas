<script lang="ts">
  import type { WfcRun } from './historyApi.js';
  import {
    filters,
    selectedRunIds,
    selectRun,
    setFavoriteOptimistic,
    toggleRunSelection,
  } from './historyStore.js';
  import { getModuleColor, hslToRgba, statusColor } from './historyUtils.js';

  interface Props {
    run: WfcRun;
  }

  let { run }: Props = $props();

  let isFavorite = $derived(!!run.favorite);

  /**
   * Compute border color based on run status.
   * Running = amber, failed = red, completed/success = module color, other = grey.
   */
  let borderColor = $derived.by(() => {
    switch (run.status) {
      case 'running': return '#E9A847';
      case 'failed': return '#E74C3C';
      case 'cancelled': return '#7f8ea3';
      case 'success': return getModuleColor(run.module);
      default: return '#666';
    }
  });

  /**
   * Compute background tint based on run status.
   */
  let bgColor = $derived.by(() => {
    switch (run.status) {
      case 'running': return 'rgba(233, 168, 71, 0.14)';
      case 'failed': return 'rgba(231, 76, 60, 0.12)';
      case 'cancelled': return 'rgba(127, 142, 163, 0.10)';
      case 'success': return hslToRgba(getModuleColor(run.module), 0.10);
      default: return 'transparent';
    }
  });

  /**
   * Compute box-shadow for running status (amber glow).
   */
  let boxShadow = $derived(run.status === 'running' ? '0 0 0 1px #E9A847' : 'none');

  /**
   * Detect whether the NID is a custom name (not an auto-version like v1, v2).
   * Custom NIDs render in a distinct color to help users distinguish them.
   */
  let isCustomNid = $derived(run.nid ? !/^v\d+$/.test(run.nid) : false);
  let nidColor = $derived(isCustomNid ? '#4fc3f7' : '#fff');

  function handleClick() {
    if ($filters.selectMode) {
      toggleRunSelection(run.id);
    } else {
      selectRun(run.id);
    }
  }

  function handleStarClick(e: MouseEvent) {
    e.stopPropagation();
    setFavoriteOptimistic(run.id, !run.favorite).catch(() => {
      /* revert happens inside the optimistic helper; swallow here to
         keep the UI responsive. */
    });
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleClick();
    }
  }
</script>

<div
  class="path-node"
  class:running={run.status === 'running'}
  class:failed={run.status === 'failed'}
  class:selected={$filters.selectMode && $selectedRunIds.has(run.id)}
  style="border-left-color: {borderColor}; background: {bgColor}; box-shadow: {boxShadow};"
  role="button"
  tabindex="0"
  onclick={handleClick}
  onkeydown={handleKeydown}
>
  <div class="top">
    <span class="status-dot" style="color: {statusColor(run.status)}">
      {'\u25CF'}
    </span>
    <span class="method-name">{run.method}</span>
    <button class="star" class:active={isFavorite} onclick={handleStarClick} title="Toggle favorite">
      {isFavorite ? '\u2605' : '\u2606'}
    </button>
  </div>
  <div class="divider"></div>
  <div class="nid" style="color: {nidColor}">{run.nid || run.runName || run.id.slice(0, 8)}</div>
  {#if run.dataSource === '__all__' && run.bundledSamples && run.bundledSamples.length > 0}
    <div class="sample" title={run.bundledSamples.join(', ')}>
      {run.bundledSamples.length} samples: {run.bundledSamples.join(', ')}
    </div>
  {:else}
    <div class="sample">{run.dataSource || '--'}</div>
  {/if}
  {#if $filters.selectMode}
    <span class="checkbox">{$selectedRunIds.has(run.id) ? '\u2611' : '\u2610'}</span>
  {/if}
</div>

<style>
  .path-node {
    padding: 12px 14px;
    border-radius: 6px;
    border-left: 4px solid var(--accent, #4A90D9);
    min-width: 150px;
    position: relative;
    cursor: pointer;
    transition: transform 0.08s;
  }
  .path-node:hover {
    transform: translateY(-1px);
  }
  .path-node.selected {
    outline: 2px solid var(--accent, #4A90D9);
    outline-offset: 1px;
  }
  .top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 3px;
    font-size: 12px;
  }
  .star {
    background: none;
    border: none;
    color: #ccc;
    cursor: pointer;
    font-size: 14px;
    padding: 0;
    line-height: 1;
    display: flex;
    align-items: center;
  }
  .star.active {
    color: var(--color-running, #E9A847);
  }
  .star:hover {
    transform: scale(1.2);
  }
  .status-dot {
    font-weight: 600;
    font-size: 12px;
    flex-shrink: 0;
  }
  .method-name {
    flex: 1;
    color: #fff;
    font-size: 14px;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .divider {
    height: 1px;
    background: #555;
    margin: 6px 0;
  }
  .nid {
    font-weight: 600;
    font-size: 15px;
  }
  .sample {
    color: var(--text-muted, #666);
    font-size: 11px;
  }
  .checkbox {
    position: absolute;
    top: 4px;
    right: 4px;
    font-size: 16px;
    color: var(--accent, #4A90D9);
  }
</style>
