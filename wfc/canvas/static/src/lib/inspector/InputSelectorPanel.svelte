<script lang="ts">
  /**
   * The Inspector's Input Selector body: pick registered inputs, or edit the
   * node's fan settings. The header above it is the Inspector's.
   *
   * Accepting a selection that removes samples can orphan overrides on
   * downstream nodes. This panel detects that and hands the decision up
   * through `onOrphans` rather than rendering the dialog itself, which keeps
   * the dialog's markup a sibling of `.inspector`, not a descendant of it.
   */
  import type { Node } from '@xyflow/svelte';
  import { samples, loadSamples, updateNodeData } from '../builder/stores.js';
  import { pushState } from '../builder/undo.js';
  // The one relative-time function. The panels pass ISO strings, the
  // History views epoch milliseconds, and it takes both.
  import { formatRelativeTime } from '../history/historyUtils.js';
  import type { CanvasNodeData } from '../shared/types.js';
  import { findOrphanOverrides, type OrphanRequest } from './nodeEdits.js';

  let { node, data, onOrphans }: {
    node: Node<CanvasNodeData> | undefined;
    data: CanvasNodeData;
    onOrphans: (request: OrphanRequest) => void;
  } = $props();

  let inputSelectorTab = $state<'select' | 'settings'>('select');
  let inputSearchQuery = $state('');
  let stagedSamples = $state<string[]>([]);
  let lastInitNodeId = $state<string | null>(null);

  let currentFanMode = $derived(data?.fanMode ?? 'out');

  let filteredSamples = $derived(
    $samples.filter(s =>
      s.name.toLowerCase().includes(inputSearchQuery.toLowerCase()) ||
      s.file_type.toLowerCase().includes(inputSearchQuery.toLowerCase())
    )
  );

  function initStagedSamples() {
    if (!node) return;
    stagedSamples = [...(node.data.selectedSamples ?? [])];
  }

  function toggleStagedSample(sampleName: string) {
    if (stagedSamples.includes(sampleName)) {
      stagedSamples = stagedSamples.filter(s => s !== sampleName);
    } else {
      stagedSamples = [...stagedSamples, sampleName];
    }
  }

  function updateLabel(value: string) {
    if (!node) return;
    updateNodeData(node.id, { label: value });
  }

  /**
   * Commit the staged selection, unless removing a sample would orphan an
   * override somewhere — in which case the Inspector confirms first.
   */
  function acceptInputSelection() {
    if (!node) return;
    const oldSamples = new Set(node.data.selectedSamples ?? []);
    const newSamples = new Set(stagedSamples);
    const removed: string[] = [];
    for (const s of oldSamples) if (!newSamples.has(s)) removed.push(s);

    const orphans = removed.length > 0 ? findOrphanOverrides(removed) : [];
    if (orphans.length === 0) {
      // Fast path — no overrides to worry about.
      updateNodeData(node.id, { selectedSamples: [...stagedSamples] });
      return;
    }

    // Stash the pending commit with the Inspector and let it confirm.
    onOrphans({
      info: orphans,
      pending: [...stagedSamples],
      revert: () => {
        if (node) stagedSamples = [...(node.data.selectedSamples ?? [])];
      },
    });
  }

  $effect(() => {
    if (node) {
      const isNewNode = node.id !== lastInitNodeId;
      if (isNewNode) lastInitNodeId = node.id;
      // One refresh per selection. The panel reads the `samples`
      // store rather than a second copy it fills itself, so this effect does
      // not re-fire on its own write — and the refresh hangs off the
      // selection changing, not off every re-run.
      if (isNewNode) {
        loadSamples();
        initStagedSamples();
      }
    } else {
      lastInitNodeId = null;
    }
  });
</script>

<div class="sys-tabs">
  <button class="sys-tab" class:active={inputSelectorTab === 'select'} onclick={() => { inputSelectorTab = 'select'; }}>Select Inputs</button>
  <button class="sys-tab" class:active={inputSelectorTab === 'settings'} onclick={() => { inputSelectorTab = 'settings'; }}>Settings</button>
</div>
<div class="tab-content">
  {#if inputSelectorTab === 'select'}
    <div class="card-search-bar">
      <input class="card-search-input" type="text" placeholder="Search registered inputs..." bind:value={inputSearchQuery} />
      <button class="card-filter-btn" title="Filter options">&#x2699;</button>
    </div>
    {#if filteredSamples.length > 0}
      <div class="card-count">{filteredSamples.length} registered input{filteredSamples.length !== 1 ? 's' : ''}</div>
    {/if}
    {#each filteredSamples as sample}
      {@const checked = stagedSamples.includes(sample.name)}
      <div class="card-wrap" class:card-checked-input={checked} onclick={() => toggleStagedSample(sample.name)}>
        <input type="checkbox" class="corner-check corner-check-input" checked={checked}
          onclick={(e: MouseEvent) => e.stopPropagation()}
          onchange={() => toggleStagedSample(sample.name)} />
        <div class="card-row">
          <span class="card-name">{sample.name}</span>
          <span class="type-badge type-badge-blue">{sample.file_type}</span>
        </div>
        <div class="card-file">{sample.registered_path?.split(/[/\\]/).pop() ?? ''}</div>
        {#if sample.description}
          <div class="card-desc">{sample.description}</div>
        {/if}
        <div class="card-meta">
          {#if sample.registered_at}{formatRelativeTime(sample.registered_at)}{/if}
          {#if sample.file_size} &middot; {(sample.file_size / 1024).toFixed(1)} KB{/if}
          {#if sample.file_count != null} &middot; {sample.file_count} file{sample.file_count !== 1 ? 's' : ''}{/if}
        </div>
      </div>
    {/each}
    {#if filteredSamples.length === 0}
      <span class="empty-hint">No registered inputs found.</span>
    {/if}
    {#if stagedSamples.length > 0}
      <div class="selected-summary">{stagedSamples.length} input{stagedSamples.length !== 1 ? 's' : ''} selected</div>
    {/if}
    <button class="accept-btn" onclick={acceptInputSelection}>Accept Selection</button>
  {:else}
    <div class="param-form">
      <div class="field">
        <label>Label</label>
        <input type="text" value={data.label} oninput={(e: Event) => updateLabel((e.target as HTMLInputElement).value)} />
      </div>
      <div class="fanout-toggle">
        <div class="fanout-text">
          <span class="fanout-label-text">{currentFanMode === 'out' ? 'Fan-out' : 'Fan-in'}</span>
          <span class="fanout-help">
            {currentFanMode === 'out'
              ? 'Each sample spawns a parallel pipeline run.'
              : 'All samples bundled as one multi-input.'}
          </span>
        </div>
        <label class="toggle">
          <input
            type="checkbox"
            checked={currentFanMode === 'in'}
            onchange={(e) => {
              if (!node) return;
              pushState();
              updateNodeData(node.id, { fanMode: (e.currentTarget as HTMLInputElement).checked ? 'in' : 'out' });
            }}
          />
          <span class="toggle-slider"></span>
        </label>
      </div>
      {#if currentFanMode === 'out'}
        {@const keepGoing = data.keepGoing ?? true}
        <div class="fanout-toggle">
          <div class="fanout-text">
            <span class="fanout-label-text">Keep going on failure</span>
            <span class="fanout-help">
              {keepGoing
                ? 'A failed sample does not cancel the others. Node lands in the mixed state when partial failures occur.'
                : 'First failed sample aborts the pipeline immediately (fail-fast).'}
            </span>
          </div>
          <label class="toggle">
            <input
              type="checkbox"
              checked={keepGoing}
              onchange={(e) => {
                if (!node) return;
                pushState();
                updateNodeData(node.id, { keepGoing: (e.currentTarget as HTMLInputElement).checked });
              }}
            />
            <span class="toggle-slider"></span>
          </label>
        </div>
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
  .toggle { position: relative; display: inline-block; width: 28px; height: 14px; }
  .toggle input { display: none; }
  .toggle-slider {
    position: absolute;
    inset: 0;
    background: var(--border);
    border-radius: 7px;
    cursor: pointer;
    transition: 0.2s;
  }
  .toggle-slider::before {
    content: '';
    position: absolute;
    width: 10px; height: 10px;
    border-radius: 50%;
    background: white;
    left: 2px; top: 2px;
    transition: 0.2s;
  }
  .toggle input:checked + .toggle-slider { background: #50C878; }
  .toggle input:checked + .toggle-slider::before { left: 16px; }
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
  .corner-check-input { accent-color: #E9A847; }
  .card-checked-input {
    background: rgba(233, 168, 71, 0.08);
    border-color: rgba(233, 168, 71, 0.3);
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
  .card-file {
    font-size: 11px;
    color: #666;
    margin-top: 2px;
    font-family: Consolas, monospace;
  }
  .card-desc {
    font-size: 11px;
    color: #999;
    margin-top: 2px;
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
  .type-badge-blue {
    color: var(--accent);
    border-color: rgba(74, 144, 217, 0.3);
    background: rgba(74, 144, 217, 0.1);
  }
  /* ── Selected summary ── */
  .selected-summary {
    margin-top: 8px;
    padding: 6px 8px;
    background: rgba(233, 168, 71, 0.08);
    border: 1px solid rgba(233, 168, 71, 0.2);
    border-radius: 4px;
    font-size: 12px;
    color: #E9A847;
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
  /* ── Fan-out toggle ── */
  .fanout-toggle {
    margin-top: 8px;
    padding: 6px 8px;
    background: #1e1e1e;
    border: 1px solid var(--border);
    border-radius: 4px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .fanout-label-text { font-size: 12px; color: #ccc; font-weight: 600; }
  .fanout-text { display: flex; flex-direction: column; gap: 2px; }
  .fanout-help { font-size: 10px; color: #777; line-height: 1.3; }
  .empty-hint {
    color: #555;
    font-size: 12px;
    font-style: italic;
    padding: 4px;
  }
</style>
