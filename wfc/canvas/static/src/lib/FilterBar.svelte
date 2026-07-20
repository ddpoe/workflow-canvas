<script lang="ts">
  import {
    filters,
    availableModules,
    availableMethods,
    availableSamples,
    selectedRunIds,
    clearSelection,
    resetFilters,
    loadRuns,
    historyView,
    collapseAllDescendants,
    expandAllDescendants,
    setModuleFilter,
    toggleMethodFilter,
  } from './historyStore.js';
  import type { TimeRange, RunStatusFilter } from './historyStore.js';

  const STATUS_CHIPS: { key: RunStatusFilter; label: string; color: string }[] = [
    { key: 'success',   label: 'Success',   color: '#27ae60' },
    { key: 'failed',    label: 'Failed',    color: '#e74c3c' },
    { key: 'running',   label: 'Running',   color: '#3498db' },
    { key: 'cancelled', label: 'Cancelled', color: '#7f8ea3' },
  ];

  function toggleStatus(status: RunStatusFilter) {
    filters.update(f => {
      const statuses = f.statuses.includes(status)
        ? f.statuses.filter(s => s !== status)
        : [...f.statuses, status];
      return { ...f, statuses };
    });
  }

  function setTimeRange(value: string) {
    filters.update(f => ({ ...f, timeRange: value as TimeRange }));
  }

  // Module and method changes go through the store's cascading setters,
  // which prune dependent method/sample selections the narrowed
  // dropdowns no longer offer.
  function setModule(value: string) {
    setModuleFilter(value);
  }

  function toggleMethod(method: string) {
    toggleMethodFilter(method);
  }

  function toggleSample(sample: string) {
    filters.update(f => {
      const samples = f.sample.includes(sample)
        ? f.sample.filter(s => s !== sample)
        : [...f.sample, sample];
      return { ...f, sample: samples };
    });
  }

  function setSearchText(value: string) {
    filters.update(f => ({ ...f, searchText: value }));
  }

  function toggleFavoritesOnly() {
    filters.update(f => ({ ...f, favoritesOnly: !f.favoritesOnly }));
  }

  function cycleArchiveView() {
    // hide → only → all → hide
    filters.update(f => {
      const next = f.archiveView === 'hide' ? 'only'
                 : f.archiveView === 'only' ? 'all'
                 : 'hide';
      return { ...f, archiveView: next };
    });
  }

  function toggleSelectMode() {
    filters.update(f => {
      const next = !f.selectMode;
      if (!next) clearSelection();
      return { ...f, selectMode: next };
    });
  }

  let methodDropdownOpen = $state(false);
  let sampleDropdownOpen = $state(false);
  let methodFilterEl: HTMLDivElement;
  let sampleFilterEl: HTMLDivElement;

  function handleWindowClick(event: MouseEvent) {
    if (methodDropdownOpen && methodFilterEl && !methodFilterEl.contains(event.target as Node)) {
      methodDropdownOpen = false;
    }
    if (sampleDropdownOpen && sampleFilterEl && !sampleFilterEl.contains(event.target as Node)) {
      sampleDropdownOpen = false;
    }
  }
</script>

<svelte:window onclick={handleWindowClick} />

<div class="filter-bar">
  <div class="filter-group">
    <label class="filter-label">Time</label>
    <div class="select-wrap">
      <select class="filter-select" value={$filters.timeRange} onchange={(e) => setTimeRange((e.target as HTMLSelectElement).value)}>
        <option value="all">All time</option>
        <option value="24h">Last 24h</option>
        <option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
      </select>
      <span class="arrow">&#x25BE;</span>
    </div>
  </div>

  <div class="filter-group">
    <label class="filter-label">Module</label>
    <div class="select-wrap">
      <select class="filter-select" value={$filters.module} onchange={(e) => setModule((e.target as HTMLSelectElement).value)}>
        <option value="">All modules</option>
        {#each $availableModules as mod}
          <option value={mod}>{mod}</option>
        {/each}
      </select>
      <span class="arrow">&#x25BE;</span>
    </div>
  </div>

  <div class="filter-group method-filter" bind:this={methodFilterEl}>
    <label class="filter-label">Methods</label>
    <button class="filter-select method-toggle" onclick={() => { methodDropdownOpen = !methodDropdownOpen; }}>
      {$filters.methods.length === 0 ? 'All methods' : `${$filters.methods.length} selected`} <span class="arrow">&#x25BE;</span>
    </button>
    {#if methodDropdownOpen}
      <div class="dropdown">
        {#each $availableMethods as method}
          <label class="dropdown-option">
            <input type="checkbox" checked={$filters.methods.includes(method)} onchange={() => toggleMethod(method)} />
            <span>{method}</span>
          </label>
        {/each}
        {#if $availableMethods.length === 0}
          <span class="dropdown-empty">{$filters.module ? 'No methods in this module' : 'No methods loaded'}</span>
        {/if}
      </div>
    {/if}
  </div>

  <div class="filter-group sample-filter" bind:this={sampleFilterEl}>
    <label class="filter-label">Sample</label>
    <button
      class="filter-select sample-toggle"
      class:active={$filters.sample.length > 0}
      onclick={() => { sampleDropdownOpen = !sampleDropdownOpen; }}
    >
      {$filters.sample.length === 0 ? 'All samples' : `${$filters.sample.length} selected`} <span class="arrow">&#x25BE;</span>
    </button>
    {#if sampleDropdownOpen}
      <div class="dropdown">
        {#each $availableSamples as sample}
          <label class="dropdown-option">
            <input type="checkbox" checked={$filters.sample.includes(sample)} onchange={() => toggleSample(sample)} />
            <span>{sample}</span>
          </label>
        {/each}
        {#if $availableSamples.length === 0}
          <span class="dropdown-empty">No samples loaded</span>
        {/if}
      </div>
    {/if}
  </div>

  <div class="filter-group status-chips-group">
    <label class="filter-label">Status</label>
    <div class="status-chips">
      {#each STATUS_CHIPS as chip}
        <button
          type="button"
          class="status-chip"
          class:active={$filters.statuses.includes(chip.key)}
          style="--chip-color: {chip.color}"
          onclick={() => toggleStatus(chip.key)}
          title={`Toggle ${chip.label} filter`}
        >
          <span class="status-dot"></span>
          {chip.label}
        </button>
      {/each}
    </div>
  </div>

  <div class="filter-group search-group">
    <label class="filter-label">Search</label>
    <input class="filter-input" type="text" placeholder="Filter runs..."
      value={$filters.searchText}
      oninput={(e) => setSearchText((e.target as HTMLInputElement).value)} />
  </div>

  <div class="filter-actions">
    <button class="filter-btn" class:active={$filters.favoritesOnly} onclick={toggleFavoritesOnly} title="Favorites only">
      {$filters.favoritesOnly ? '\u2605' : '\u2606'}
    </button>
    <button
      class="filter-btn"
      class:active={$filters.archiveView !== 'hide'}
      onclick={cycleArchiveView}
      title={$filters.archiveView === 'hide' ? 'Archived hidden — click to show only archived'
           : $filters.archiveView === 'only' ? 'Showing archived only — click to show all'
           : 'Showing all (incl. archived) — click to hide archived'}
    >
      {$filters.archiveView === 'hide' ? '\u{1F5C4}' : $filters.archiveView === 'only' ? '\u{1F5C4} only' : '\u{1F5C4} all'}
    </button>
    <button class="filter-btn" class:active={$filters.selectMode} onclick={toggleSelectMode} title="Select mode for export">
      {$filters.selectMode ? `\u2611 ${$selectedRunIds.size}` : '\u2610'}
    </button>
    <button class="filter-btn refresh-btn" onclick={() => loadRuns()} title="Refresh runs">
      {'\u21BB'} Refresh
    </button>
    <button class="filter-btn reset-btn" onclick={resetFilters} title="Reset all filters">
      Reset
    </button>
  </div>

  {#if $historyView === 'descendants'}
    <div class="filter-actions collapse-actions">
      <button class="filter-btn collapse-all-btn" onclick={collapseAllDescendants} title="Collapse every tree section">
        {'⊟'} Collapse all
      </button>
      <button class="filter-btn expand-all-btn" onclick={expandAllDescendants} title="Expand every tree section">
        {'⊞'} Expand all
      </button>
    </div>
  {/if}
</div>

<style>
  .filter-bar {
    display: flex;
    align-items: flex-end;
    gap: 12px;
    padding: 10px 14px;
    background: var(--bg-panel, #252526);
    border-bottom: 1px solid var(--border, #3e3e42);
    flex-shrink: 0;
    flex-wrap: wrap;
  }
  .filter-group {
    display: flex;
    flex-direction: column;
    gap: 3px;
    position: relative;
  }
  .filter-label {
    font-size: 11px;
    color: var(--text-muted, #666);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .filter-select, .filter-input {
    background: var(--bg-input, #1e1e1e);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    padding: 6px 10px;
    color: var(--text-primary, #ccc);
    font-size: 13px;
    outline: none;
    min-height: 30px;
  }
  .filter-select:focus, .filter-input:focus {
    border-color: var(--accent, #4A90D9);
  }
  .filter-select.active {
    border-color: var(--accent, #4A90D9);
    color: #fff;
  }
  .search-group {
    flex: 1;
    min-width: 120px;
  }
  .filter-input {
    width: 100%;
  }
  .filter-actions {
    display: flex;
    gap: 4px;
    align-items: flex-end;
    padding-bottom: 1px;
  }
  .collapse-actions {
    margin-left: auto;
  }
  .collapse-all-btn, .expand-all-btn {
    color: var(--accent, #4A90D9);
  }
  .filter-btn {
    background: var(--bg-input, #1e1e1e);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    padding: 6px 10px;
    color: var(--text-secondary, #888);
    font-size: 13px;
    cursor: pointer;
    min-height: 30px;
  }
  .filter-btn:hover {
    border-color: var(--accent, #4A90D9);
    color: var(--text-primary, #ccc);
  }
  .filter-btn.active {
    background: var(--accent, #4A90D9);
    color: white;
    border-color: var(--accent, #4A90D9);
  }
  .refresh-btn {
    color: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
  }
  .reset-btn {
    color: var(--text-muted, #666);
    font-size: 12px;
  }
  .method-toggle, .sample-toggle {
    cursor: pointer;
    text-align: left;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
  }
  .select-wrap {
    position: relative;
    display: flex;
    align-items: center;
  }
  .select-wrap select {
    appearance: none;
    -webkit-appearance: none;
    padding-right: 24px;
  }
  .select-wrap .arrow {
    position: absolute;
    right: 8px;
    pointer-events: none;
  }
  .arrow {
    color: var(--text-muted, #666);
    font-size: 14px;
  }
  .dropdown {
    position: absolute;
    top: 100%;
    left: 0;
    z-index: 100;
    background: var(--bg-panel, #252526);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 4px;
    padding: 4px 0;
    max-height: 200px;
    overflow-y: auto;
    min-width: 180px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  }
  .dropdown-option {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    font-size: 13px;
    color: var(--text-primary, #ccc);
    cursor: pointer;
  }
  .dropdown-option:hover {
    background: var(--bg-header, #2d2d30);
  }
  .dropdown-option input[type="checkbox"] {
    accent-color: var(--accent, #4A90D9);
  }
  .dropdown-empty {
    padding: 10px;
    font-size: 13px;
    color: var(--text-muted, #666);
  }
  .status-chips-group {
    min-width: 0;
  }
  .status-chips {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    align-items: center;
    min-height: 30px;
  }
  .status-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: var(--bg-input, #1e1e1e);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 12px;
    padding: 3px 10px;
    color: var(--text-secondary, #888);
    font-size: 12px;
    cursor: pointer;
    line-height: 1.4;
  }
  .status-chip:hover {
    border-color: var(--chip-color, var(--accent, #4A90D9));
    color: var(--text-primary, #ccc);
  }
  .status-chip.active {
    background: var(--chip-color, var(--accent, #4A90D9));
    border-color: var(--chip-color, var(--accent, #4A90D9));
    color: #fff;
  }
  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--chip-color, #888);
    display: inline-block;
  }
  .status-chip.active .status-dot {
    background: #fff;
  }
</style>
