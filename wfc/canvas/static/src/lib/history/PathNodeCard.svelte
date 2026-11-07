<script lang="ts">
  import type { WfcRun } from './historyApi.js';
  import { COLLAPSED_SAMPLE } from '../shared/types.js';
  import {
    filters,
    selectedRunIds,
    selectRun,
    selectedRunId,
    setFavoriteOptimistic,
    toggleRunSelection,
  } from './historyStore.js';
  import { statusColor } from './historyUtils.js';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import HelpMark from '../shared/HelpMark.svelte';
  import { openDraftInRegistry } from '../shared/draftLinks.js';
  import { DRAFT_NODE_HELP, REVIEW_IN_REGISTRY, downstreamHelp } from '../builder/drafts.js';
  import { draftExceptionColor, draftOwner } from './historyDrafts.js';

  interface Props {
    run: WfcRun;
  }

  let { run }: Props = $props();

  let isFavorite = $derived(!!run.favorite);

  /**
   * A draft-mode card (the draft's latest draft run, or a step after it):
   * the DRAFT MODE flag, no star and no run id. Only an exception status is
   * dashed; a succeeded draft-mode step keeps the neutral border.
   */
  let step = $derived(run.draftStep ?? null);
  let owner = $derived(step ? draftOwner(step) : null);
  let dashColor = $derived(step ? draftExceptionColor(run.status) : null);
  let draftTip = $derived(step ? (step.isDraft ? DRAFT_NODE_HELP : downstreamHelp(owner!.method)) : '');
  // A failed draft-mode card says it failed, not why; the message is in
  // the step's panel.
  let failedWhy = $derived(step && run.status === 'failed' ? '✗ Failed' : '');

  /**
   * Cache-hit audit rows reuse another run's outputs. They keep the green
   * success body (module left border + tint) and add an amber wrap on the
   * other three edges plus a CACHED pill — deliberately distinct from the
   * running style (amber left border + amber tint + ring).
   */
  let isCached = $derived(!!run.cacheSourceRunId);

  /**
   * Compute border color based on run status.
   * Running or queued = amber, failed = red, cancelled = slate; a succeeded
   * run (cached or not) keeps the neutral border. The left bar is the module's.
   */
  let borderColor = $derived.by(() => {
    switch (run.status) {
      case 'running': return 'var(--status-active)';
      case 'queued': return 'var(--status-active)';
      case 'pending': return 'var(--status-active)';
      case 'failed': return 'var(--status-failed)';
      case 'cancelled': return 'var(--status-cancelled)';
      default: return 'var(--border)';
    }
  });

  /** History colours a border only for an exception (SOP: exceptions only). */
  let isException = $derived(borderColor !== 'var(--border)');

  /** The clicked card, or a card ticked in select mode. */
  let isSelected = $derived(
    $selectedRunId === run.id || ($filters.selectMode && $selectedRunIds.has(run.id)),
  );

  /**
   * Compute background tint based on run status.
   */
  let bgColor = $derived(`color-mix(in srgb, ${moduleColor(run.module)} 10%, var(--bg-panel))`);

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
  class:selected={isSelected}
  class:sel-glow={isSelected}
  class:glow-neutral={isSelected && !isException}
  class:cached={isCached}
  class:ex={isException}
  class:draft-card={!!step}
  class:dash-2={!!dashColor}
  data-testid={step ? 'draft-card' : undefined}
  style="border-color: {borderColor}; border-left-color: {moduleColor(run.module)}; {dashColor ? `--dash-color: ${dashColor}; --dash-body: ${bgColor};` : `background: ${bgColor};`} --glow-c: {borderColor}; --module: {moduleColor(run.module)};"
  role="button"
  tabindex="0"
  onclick={handleClick}
  onkeydown={handleKeydown}
>
  {#if step}
    <!-- The module accent bar: a dashed card's own left border is transparent. -->
    {#if dashColor}<i class="acc"></i>{/if}
    <span class="draft-flag-wrap">
      <span class="draft-flag" class:strong={step.isDraft} data-testid="draft-flag">DRAFT MODE<HelpMark size={10} light={!step.isDraft} /></span>
      <span class="draft-tip" data-testid="draft-tip">
        {draftTip}
        {#if owner}
          <button type="button" class="lnk" title="Opens {owner.method}’s Draft run tab in the Registry"
                  onclick={(e: MouseEvent) => { e.stopPropagation(); openDraftInRegistry(owner!.module, owner!.method); }}>{REVIEW_IN_REGISTRY}</button>
        {/if}
      </span>
    </span>
  {/if}
  <div class="top">
    <span class="status-dot" style="color: {statusColor(run.status)}">
      {'\u25CF'}
    </span>
    <span class="method-name">{run.method}</span>
    {#if !step}
    <button class="star" class:active={isFavorite} onclick={handleStarClick} title="Toggle favorite">
      {isFavorite ? '\u2605' : '\u2606'}
    </button>
    {/if}
  </div>
  <div class="divider"></div>
  {#if step}
    <!-- No run id: the draft shows a neutral draft pill, a later step its NID. -->
    <div class="nid" data-testid="draft-nid">
      {#if step.isDraft}<span class="draft-pill">draft</span>{:else}{run.nid}{/if}
    </div>
    {#if failedWhy}
      <div class="why" data-testid="draft-why"><span aria-hidden="true">✗</span> Failed</div>
    {:else}
      <div class="sample">{run.dataSource || '--'}{step.isDraft && run.nid ? ` · ${run.nid}` : ''}</div>
    {/if}
  {:else}
  <div class="nid" style="color: {nidColor}">
    {run.nid || run.runName || run.id.slice(0, 8)}
    {#if isCached}
      <span class="cached-pill">{'⟳'} CACHED</span>
    {/if}
  </div>
  {#if run.dataSource === COLLAPSED_SAMPLE && run.bundledSamples && run.bundledSamples.length > 0}
    <div class="sample" title={run.bundledSamples.join(', ')}>
      {run.bundledSamples.length} samples: {run.bundledSamples.join(', ')}
    </div>
  {:else}
    <div class="sample">{run.dataSource || '--'}</div>
  {/if}
  {/if}
  {#if $filters.selectMode}
    <span class="checkbox">{$selectedRunIds.has(run.id) ? '\u2611' : '\u2610'}</span>
  {/if}
</div>

<style>
  .path-node {
    padding: 12px 14px;
    border-radius: 6px;
    border: 1px solid var(--border);
    border-left: 4px solid var(--border);
    min-width: 150px;
    position: relative;
    cursor: pointer;
    transition: transform 0.08s;
  }
  .path-node:hover {
    transform: translateY(-1px);
  }
  /* An exception (style guide `.hc.ex`): a 2px status border, recorded or
     draft mode alike; the module bar keeps its 4px. The padding gives back
     the extra pixel so the card keeps its size. */
  .path-node.ex {
    border-top-width: 2px;
    border-right-width: 2px;
    border-bottom-width: 2px;
    padding: 11px 13px;
  }
  .path-node.selected {
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
    color: var(--text-primary);
    cursor: pointer;
    font-size: 14px;
    padding: 0;
    line-height: 1;
    display: flex;
    align-items: center;
  }
  .star.active {
    color: var(--marker-light);
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
    display: flex;
    align-items: center;
    gap: 8px;
  }
  /* Cached: amber on top/right/bottom only — the left edge stays the
     module-color border so the green "completed" signal is untouched. */
  .path-node.cached {
  }
  .cached-pill {
    background: color-mix(in oklab, var(--status-cached) 15%, transparent);
    color: var(--status-cached);
    border: 1px solid color-mix(in oklab, var(--status-cached) 45%, transparent);
    border-radius: 9px;
    font-size: 9.5px;
    font-weight: 600;
    padding: 1px 7px;
    letter-spacing: 0.3px;
    white-space: nowrap;
  }
  .sample {
    color: var(--text-muted);
    font-size: 11px;
  }
  /* A dashed draft-mode card (exception status only): a 2px border all
     round, its dashes painted by the global .dash-2 utility, and the module
     bar drawn on top of them. */
  .path-node.dash-2 {
    border-width: 2px;
    border-left-width: 2px;
    padding: 11px 13px 11px 16px;
  }
  /* The body sits in --dash-body so a selected dashed card's glow
     (app.css .dash-2.sel-glow) can light the dash gaps. */
  .path-node.dash-2:not(.sel-glow) {
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
  /* The DRAFT MODE flag (style guide `.flag`): solid light grey on the
     draft, an outline on the steps after it. */
  .draft-flag-wrap {
    position: absolute;
    top: -10px;
    left: 6px;
    z-index: 4;
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
    font-weight: 400;
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
  /* The draft's own NID-line pill (mockup `.sp.draft`): neutral, no fill. */
  .draft-pill {
    display: inline-flex;
    align-items: center;
    font-size: 9.5px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    padding: 1px 7px;
    line-height: 15px;
    border-radius: 3px;
    border: 1px solid color-mix(in oklab, var(--marker-light) 45%, transparent);
    color: var(--text-primary);
  }
  .why {
    color: var(--status-failed-ink);
    font-size: 10.5px;
    margin-top: 3px;
  }
  .checkbox {
    position: absolute;
    top: 4px;
    right: 4px;
    font-size: 16px;
    color: var(--accent);
  }
</style>
