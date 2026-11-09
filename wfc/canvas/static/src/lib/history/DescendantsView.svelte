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
  import { tick } from 'svelte';
  import {
    FIT_MIN,
    fitZoom,
    zoomIn,
    zoomOut,
    loadZoomChoice,
    saveZoomChoice,
    type ZoomChoice,
  } from './descendantsZoom.js';

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

  /*
   * The card zoom. Each tree row (its cards and connectors) is drawn with CSS
   * `zoom`; the sample labels and the scope banner keep their size. Fit is
   * measured from the drawn rows; − and + choose a fixed percentage.
   */
  let zoomChoice = $state<ZoomChoice>(loadZoomChoice());
  let fitPercent = $state(FIT_MIN);
  let zoomPercent = $derived(zoomChoice === 'fit' ? fitPercent : zoomChoice);
  let scroller = $state<HTMLElement | null>(null);

  function chooseZoom(choice: ZoomChoice): void {
    zoomChoice = choice;
    saveZoomChoice(choice);
  }

  /**
   * Measure the widest card at plain size and fit it to the view's width.
   * A card is a flex item that shrinks (and wraps) when its row is too
   * narrow, so it is measured with shrinking turned off for the read, giving
   * its own width rather than the row's; nothing is painted in between.
   */
  function refit(): void {
    if (!scroller || zoomChoice !== 'fit') return;
    const z = zoomPercent / 100;
    const cards = [...scroller.querySelectorAll<HTMLElement>('.tree-row > .tree-card')];
    for (const card of cards) card.style.flexShrink = '0';
    let natural = 0;
    let rowsLeft = Infinity;
    for (const card of cards) {
      const left = card.parentElement!.getBoundingClientRect().left;
      rowsLeft = Math.min(rowsLeft, left);
      natural = Math.max(natural, (card.getBoundingClientRect().right - left) / z);
    }
    for (const card of cards) card.style.flexShrink = '';
    if (!Number.isFinite(rowsLeft)) return;
    const viewRight = scroller.getBoundingClientRect().left + scroller.clientLeft + scroller.clientWidth;
    fitPercent = fitZoom(viewRight - rowsLeft, natural);
  }

  // Re-fit when the content changes: other runs, collapse state, select mode, back to Fit.
  $effect(() => {
    void $scopedDescendantForest;
    void $descendantsCollapsed;
    void $filters.selectMode;
    if (!scroller || zoomChoice !== 'fit') return;
    tick().then(refit);
  });

  // Re-fit when the view's width changes: the window, the run panel opening or resizing.
  $effect(() => {
    if (!scroller || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => refit());
    ro.observe(scroller);
    return () => ro.disconnect();
  });

  // Ctrl+wheel anywhere over the view, the zoom box included, zooms the cards
  // (a non-passive listener, so the page itself does not zoom). A trackpad
  // pinch sends many small deltas: one step per 50 accumulated, the count
  // starting afresh after a pause.
  let viewEl = $state<HTMLElement | null>(null);
  $effect(() => {
    const el = viewEl;
    if (!el) return;
    let acc = 0;
    let last = -Infinity;
    const onWheel = (e: WheelEvent) => {
      // No cards (the box is hidden): leave the wheel to the page.
      if (!e.ctrlKey || $scopedDescendantForest.length === 0) return;
      e.preventDefault();
      if (e.timeStamp - last > 250) acc = 0;
      last = e.timeStamp;
      acc += e.deltaY;
      if (Math.abs(acc) < 50) return;
      chooseZoom(acc < 0 ? zoomIn(zoomPercent) : zoomOut(zoomPercent));
      acc = 0;
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  });

  /** + = − 0 while the view has focus; never from a field, never with a modifier. */
  function onViewKeydown(e: KeyboardEvent): void {
    const t = e.target as HTMLElement | null;
    const tag = t?.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || t?.isContentEditable) return;
    if (e.ctrlKey || e.metaKey || e.altKey || $scopedDescendantForest.length === 0) return;
    if (e.key === '+' || e.key === '=') { e.preventDefault(); chooseZoom(zoomIn(zoomPercent)); }
    else if (e.key === '-') { e.preventDefault(); chooseZoom(zoomOut(zoomPercent)); }
    else if (e.key === '0') { e.preventDefault(); chooseZoom('fit'); }
  }
</script>

<!-- The view takes focus on a click so its zoom keys reach it; the keys are a
     shortcut for the control box's buttons. -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div class="descendants-view" role="region" aria-label="Descendants" tabindex="-1" onkeydown={onViewKeydown} bind:this={viewEl}>
<div class="dv-scroll" bind:this={scroller}>
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
              onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}><span aria-hidden="true">✗</span> Failed</button>{#if run.duration != null}{' '}after {formatDuration(run.duration)}{/if}
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
          <div class="tree-row" style="padding-left: {row.depth * 24}px; --dz: {zoomPercent / 100};">
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
  {#if $scopedDescendantForest.length > 0}
    <!-- The card zoom, in the view's lower right (styled as the Builder's controls). -->
    <div class="zoom-box" data-testid="descendants-zoom" role="group" aria-label="Card zoom">
      <button type="button" class="z-btn" aria-label="Zoom out" title="Zoom out (−)" onclick={() => chooseZoom(zoomOut(zoomPercent))}>−</button>
      <span class="z-level" data-testid="descendants-zoom-level">{zoomPercent}%</span>
      <button type="button" class="z-btn" aria-label="Zoom in" title="Zoom in (+)" onclick={() => chooseZoom(zoomIn(zoomPercent))}>+</button>
      <button type="button" class="z-btn z-fit" class:sel={zoomChoice === 'fit'} aria-label="Fit to view" aria-pressed={zoomChoice === 'fit'}
              title="Fit the cards to the view (0)" onclick={() => chooseZoom('fit')}>Fit</button>
    </div>
  {/if}
</div>

<style>
  /* The view fills its pane: the cards scroll inside it, the zoom box stays
     in its lower right. */
  .descendants-view {
    position: relative;
    height: 100%;
    display: flex;
    flex-direction: column;
    outline: none;
  }
  .dv-scroll {
    flex: 1;
    min-height: 0;
    overflow: auto;
    /* A scrollbar that comes and goes would change the width Fit measures. */
    scrollbar-gutter: stable;
    display: flex;
    flex-direction: column;
    gap: 4px;
    /* Room to scroll the last card clear of the zoom box. */
    padding-bottom: 44px;
  }
  .zoom-box {
    position: absolute;
    right: 16px;
    bottom: 4px;
    z-index: 6;
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 3px;
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 4px;
  }
  .z-btn {
    width: 26px;
    height: 26px;
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--xy-controls-button-background);
    border: 1px solid var(--xy-controls-button-border-color);
    border-radius: 3px;
    color: var(--xy-controls-button-color);
    font-family: inherit;
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
  }
  .z-btn:hover {
    background: var(--xy-controls-button-background-hover);
    color: var(--xy-controls-button-color-hover);
  }
  .z-fit {
    width: auto;
    padding: 0 8px;
    font-size: 11px;
  }
  .z-fit.sel {
    border-color: var(--accent);
    color: var(--xy-controls-button-color-hover);
    background: color-mix(in oklab, var(--accent) 22%, var(--xy-controls-button-background));
  }
  .z-level {
    min-width: 40px;
    text-align: center;
    font-size: 11px;
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
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
  /* The card zoom (--dz, set per row) scales the row's cards and connectors
     together, its indent included; the sample labels sit outside it. */
  .tree-row {
    display: flex;
    align-items: center;
    margin: 8px 0;
    zoom: var(--dz, 1);
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
