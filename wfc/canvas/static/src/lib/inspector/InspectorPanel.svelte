<script lang="ts">
  /**
   * The Inspector: the header for whatever node is selected, one body per
   * node type, and the orphan-override confirmation dialog.
   *
   * The bodies are `InputSelectorPanel`, `RunReferencePanel` and
   * `MethodPanel`; none of them renders a wrapper element, so their tab
   * strips and `.tab-content` stay direct flex children of `.inspector`.
   * The dialog lives here, a sibling of `.inspector`, rather than in the
   * panel that raises it.
   */
  import { selectedNode, nodes, updateNodeData, deleteNodes } from '../builder/stores.js';
  import type { CanvasNodeData } from '../shared/types.js';
  import { onDestroy } from 'svelte';
  import InputSelectorPanel from './InputSelectorPanel.svelte';
  import RunReferencePanel from './RunReferencePanel.svelte';
  import MethodPanel from './MethodPanel.svelte';
  import { stripOrphanOverrides, type OrphanRequest } from './nodeEdits.js';
  import { RenderTick } from './renderTick.svelte.js';
  import { nodeVersion, undeclaredParams, versionSwitch, versionTitle, withoutParam } from '../builder/nodeData.js';

  // The canvas `nodes` store, whose deep field changes `updateNodeData`
  // makes in place (see `data` below). `renderTick.svelte.ts` owns the
  // counter and the teardown.
  const nodesTick = new RenderTick(nodes);
  onDestroy(() => { nodesTick.dispose(); });

  let node = $derived($selectedNode);
  // `updateNodeData` mutates `node.data` in place (needed so SvelteFlow
  // doesn't lose its drag bookkeeping) and then re-emits the nodes array.
  // Runes-mode `$derived` uses strict equality, so returning `node?.data`
  // directly would be short-circuited on same-ref and deep field changes
  // (paramValues, variants, sampleOverrides) would never reach the
  // template — or the panels this passes it to.
  let data = $derived.by(() => {
    void nodesTick.count;
    return node?.data ? { ...node.data } as CanvasNodeData : undefined;
  });

  function handleDeleteNode() {
    if (!node) return;
    deleteNodes([node.id]);
  }

  /**
   * Run the version the dropdown names: the newest clears the pin, an older
   * one sets it. The node draws that version's contract; a value it does
   * not declare stays, flagged, until the user removes it.
   */
  function switchVersion(label: string) {
    if (!node || !data) return;
    const picked = data.versions?.find(v => String(v.label) === label);
    if (!picked) return;
    updateNodeData(node.id, versionSwitch(data, picked.identity));
  }

  /** Remove a parameter the node's version does not declare. */
  function removeParam(name: string) {
    if (!node || !data) return;
    updateNodeData(node.id, withoutParam(data, name));
  }

  // ── Orphan-override confirmation on sample removal ──
  // The Input Selector raises this when accepting a selection would strip
  // samples that downstream nodes still hold overrides for. It carries the
  // tally, the selection waiting to be committed, and the panel's own way
  // of putting its staging back if the removal is cancelled.
  let orphanRequest = $state<OrphanRequest | null>(null);

  /** Commit the Input Selector's pending selection onto the node. */
  function commitStagedSamples() {
    if (!node || !orphanRequest) return;
    updateNodeData(node.id, { selectedSamples: [...orphanRequest.pending] });
  }

  function orphanConfirmDiscard() {
    // Commit the Input Selector change AND strip orphan overrides +
    // orphan per-sample variants across all nodes that referenced the
    // removed samples.
    const request = orphanRequest;
    if (!request) return;
    commitStagedSamples();
    stripOrphanOverrides(request.info.map(o => o.sample));
    orphanRequest = null;
  }

  function orphanConfirmKeep() {
    // Commit the Input Selector change; leave sampleOverrides untouched
    // so they re-activate if the sample is added back.
    commitStagedSamples();
    orphanRequest = null;
  }

  function orphanCancel() {
    // Abort the edit entirely; staged selection is discarded.
    orphanRequest?.revert();
    orphanRequest = null;
  }
</script>

<div class="inspector">
  {#if data}
    <!-- Header: the node's own colour, its name, what it runs, and Delete -->
    {@const isSystem = data.nodeType === 'input_selector' || data.nodeType === 'run_reference'}
    {@const running = isSystem ? undefined : nodeVersion(data)}
    <div class="inspector-header" class:system-header={isSystem}
      style={isSystem ? '' : `background: ${data.color};`}>
      <span class="inspector-title">{data.label}</span>
      {#if !isSystem}
        <div class="inspector-meta">
          <span class="meta-chip"><span class="meta-key">Module</span><span class="meta-val">{data.module}</span></span>
          {#if running?.env}
            <span class="meta-chip"><span class="meta-key">Env</span><span class="meta-val">{running.env}</span></span>
          {/if}
        </div>
      {/if}
      <div class="inspector-action-row">
        {#if isSystem}
          <span class="system-label">
            {data.nodeType === 'input_selector' ? 'Input Selector' : 'Run Reference'}
          </span>
        {:else if data.versions?.length}
          <span class="version-tag" class:version-tag-newest={running?.newest}>
            <i class="version-dot"></i>
            <span class="version-label">{running ? `v${running.label}` : 'unregistered'}</span>
            {#if running}
              <span class="version-state">{running.newest ? 'latest' : 'pinned'}</span>
            {/if}
            <span class="version-caret" aria-hidden="true">&#9660;</span>
            <select
              class="inspector-version"
              title={versionTitle(data)}
              value={running ? String(running.label) : ''}
              onchange={(e) => switchVersion((e.currentTarget as HTMLSelectElement).value)}
            >
              {#if !running}
                <option value="" disabled>unregistered version</option>
              {/if}
              {#each data.versions as v (v.label)}
                <option value={String(v.label)}>v{v.label}</option>
              {/each}
            </select>
          </span>
        {/if}
        <button class="inspector-delete-btn" onclick={handleDeleteNode} title="Delete node" aria-label="Delete node">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/>
          </svg>
        </button>
      </div>
    </div>

    {#if data.nodeType === 'input_selector'}
      <InputSelectorPanel {node} {data} onOrphans={(request) => { orphanRequest = request; }} />
    {:else if data.nodeType === 'run_reference'}
      <RunReferencePanel {node} {data} />
    {:else}
      {@const undeclared = undeclaredParams(data)}
      {#if undeclared.length}
        {@const label = nodeVersion(data)?.label}
        <ul class="undeclared-params">
          {#each undeclared as name (name)}
            <li class="undeclared-param">
              <span><code>{name}</code> is not in v{label}, remove it</span>
              <button class="undeclared-remove" onclick={() => removeParam(name)}>Remove</button>
            </li>
          {/each}
        </ul>
      {/if}
      <MethodPanel {node} {data} />
    {/if}
  {:else}
    <div class="no-selection">
      <p>Select a node to inspect its properties.</p>
    </div>
  {/if}
</div>

{#if orphanRequest}
  {@const orphanInfo = orphanRequest.info}
  <!-- Orphaned-override confirmation modal -->
  <div
    class="orphan-modal-overlay"
    onclick={orphanCancel}
    onkeydown={(e) => { if (e.key === 'Escape') orphanCancel(); }}
    role="presentation"
  >
    <div class="orphan-modal" onclick={(e: MouseEvent) => e.stopPropagation()} role="dialog" aria-modal="true">
      <div class="orphan-modal-header">
        {#if orphanInfo.length === 1}
          <span class="orphan-modal-title">Discard {orphanInfo[0].sample}'s overrides?</span>
        {:else}
          <span class="orphan-modal-title">Discard overrides for {orphanInfo.length} samples?</span>
        {/if}
      </div>
      <div class="orphan-modal-body">
        {#if orphanInfo.length === 1}
          {@const only = orphanInfo[0]}
          <p>
            The sample <strong>"{only.sample}"</strong> is being removed from the Input Selector.
            It has overrides on {only.nodeCount} node{only.nodeCount !== 1 ? 's' : ''}.
            What would you like to do?
          </p>
        {:else}
          <p>
            {orphanInfo.length} samples are being removed from the Input Selector.
            They have overrides on downstream nodes:
          </p>
          <ul class="orphan-list">
            {#each orphanInfo as info}
              <li><strong>{info.sample}</strong> &mdash; {info.nodeCount} node{info.nodeCount !== 1 ? 's' : ''}</li>
            {/each}
          </ul>
          <p>What would you like to do?</p>
        {/if}
      </div>
      <div class="orphan-modal-footer">
        <button class="orphan-btn orphan-btn-danger" onclick={orphanConfirmDiscard}>
          Yes, discard
        </button>
        <button class="orphan-btn orphan-btn-neutral" onclick={orphanConfirmKeep}>
          No, keep them
        </button>
        <button class="orphan-btn orphan-btn-cancel" onclick={orphanCancel}>
          Cancel removal
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .inspector {
    background: #252526;
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    height: 100%;
    overflow: hidden;
  }
  .inspector-header {
    padding: 12px 12px 10px;
    background: #2d2d30;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .inspector-title {
    color: #fff;
    font-size: 17px;
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .inspector-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .meta-chip {
    display: inline-flex;
    align-items: center;
    height: 20px;
    background: rgba(0, 0, 0, 0.35);
    border-radius: 3px;
    overflow: hidden;
    font-size: 11px;
    max-width: 100%;
  }
  .meta-key {
    color: rgba(255, 255, 255, 0.6);
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0 6px;
    height: 100%;
    display: flex;
    align-items: center;
    background: rgba(0, 0, 0, 0.25);
  }
  .meta-val {
    color: #fff;
    padding: 0 7px;
    font-family: Consolas, monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  /* The version picker on the left, Delete pushed right; both 24px tall so
     their tops and bottoms line up. */
  .inspector-action-row {
    display: flex;
    align-items: flex-end;
    gap: 6px;
  }
  .version-tag {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 24px;
    box-sizing: border-box;
    padding: 0 8px;
    background: rgba(0, 0, 0, 0.5);
    color: #eee;
    border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 3px;
    font-size: 11px;
    font-weight: 600;
  }
  .version-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
    background: #F5D547;
  }
  .version-tag-newest .version-dot { background: #7CFC9A; }
  .version-tag:focus-within {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  /* The native select stays (keyboard and screen readers) and covers the
     whole tag, invisible, so a click anywhere on the tag opens it; the spans
     beneath draw what it shows. */
  .inspector-version {
    position: absolute;
    inset: 0;
    width: 100%;
    opacity: 0;
    cursor: pointer;
    font: inherit;
  }
  .inspector-version option { background: #1e1e1e; color: #ccc; }
  .version-state { color: #aaa; font-weight: 400; font-size: 10px; }
  .version-tag-newest .version-state { color: #9fd9b3; }
  .version-caret { color: #888; font-size: 9px; }
  .inspector-delete-btn {
    margin-left: auto;
    width: 24px;
    height: 24px;
    box-sizing: border-box;
    background: rgba(0, 0, 0, 0.35);
    border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 4px;
    color: #fff;
    cursor: pointer;
    padding: 0;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .inspector-delete-btn:hover {
    color: #fff;
    border-color: #E74C3C;
    background: #E74C3C;
  }
  .undeclared-params {
    list-style: none;
    margin: 0;
    padding: 6px 12px;
    background: rgba(231, 76, 60, 0.1);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .undeclared-param {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    color: #E74C3C;
    font-size: 11px;
    padding: 2px 0;
  }
  .undeclared-remove {
    background: none;
    border: 1px solid #E74C3C;
    border-radius: 3px;
    color: #E74C3C;
    font-size: 11px;
    cursor: pointer;
    padding: 1px 6px;
  }
  .no-selection {
    padding: 20px 10px;
    text-align: center;
    color: #666;
    font-size: 13px;
  }
  /* System node inspector styles: the header takes the system node's own
     gradient, as its canvas card does. */
  .system-header {
    background: linear-gradient(135deg, var(--color-system), #16A085);
  }
  .system-label {
    color: rgba(255, 255, 255, 0.85);
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.5px;
  }
  /* ── Orphan-override confirmation modal ── */
  .orphan-modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .orphan-modal {
    background: #252526;
    border: 1px solid var(--border);
    border-radius: 8px;
    width: 420px;
    max-width: 90vw;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  }
  .orphan-modal-header {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
  }
  .orphan-modal-title {
    font-size: 14px;
    font-weight: 600;
    color: #ccc;
  }
  .orphan-modal-body {
    padding: 14px 16px;
    overflow-y: auto;
    font-size: 13px;
    color: #ccc;
    line-height: 1.45;
  }
  .orphan-modal-body p {
    margin: 0 0 8px 0;
  }
  .orphan-modal-body strong {
    color: #E9A847;
    font-weight: 600;
  }
  .orphan-list {
    margin: 4px 0 10px 0;
    padding-left: 18px;
    font-size: 12px;
  }
  .orphan-list li {
    margin-bottom: 2px;
  }
  .orphan-modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--border);
    flex-wrap: wrap;
  }
  .orphan-btn {
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
  }
  .orphan-btn-danger {
    background: #8B3A3A;
    color: white;
    border-color: #8B3A3A;
  }
  .orphan-btn-danger:hover { background: #a04545; }
  .orphan-btn-neutral {
    background: var(--accent);
    color: white;
    border-color: var(--accent);
  }
  .orphan-btn-neutral:hover { background: #3a80c9; }
  .orphan-btn-cancel {
    background: #2d2d30;
    color: #ccc;
  }
  .orphan-btn-cancel:hover { border-color: var(--accent); }
</style>
