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
    <!-- Header -->
    <div class="inspector-header">
      <div class="inspector-header-text">
        <span class="inspector-title">{data.label}</span>
        {#if data.nodeType === 'input_selector' || data.nodeType === 'run_reference'}
          <span class="inspector-module system-label">
            {data.nodeType === 'input_selector' ? 'Input Selector' : 'Run Reference'}
          </span>
        {:else}
          <span class="inspector-module">{data.module}{data.version ? ' · v' + data.version : ''}</span>
        {/if}
      </div>
      <button class="inspector-delete-btn" onclick={handleDeleteNode} title="Delete node">
        &#128465;
      </button>
    </div>

    {#if data.nodeType === 'input_selector'}
      <InputSelectorPanel {node} {data} onOrphans={(request) => { orphanRequest = request; }} />
    {:else if data.nodeType === 'run_reference'}
      <RunReferencePanel {node} {data} />
    {:else}
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
    padding: 10px 12px;
    background: #2d2d30;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .inspector-header-text {
    display: flex;
    flex-direction: column;
    gap: 1px;
    flex: 1;
    min-width: 0;
  }
  .inspector-delete-btn {
    background: none;
    border: 1px solid transparent;
    border-radius: 3px;
    color: #666;
    font-size: 16px;
    cursor: pointer;
    padding: 2px 4px;
    flex-shrink: 0;
    line-height: 1;
  }
  .inspector-delete-btn:hover {
    color: #E74C3C;
    border-color: #E74C3C;
    background: rgba(231, 76, 60, 0.1);
  }
  .inspector-title { color: #ccc; font-size: 14px; font-weight: 600; }
  .inspector-module { color: #666; font-size: 11px; }
  .no-selection {
    padding: 20px 10px;
    text-align: center;
    color: #666;
    font-size: 13px;
  }
  /* System node inspector styles */
  .system-label {
    color: var(--color-system) !important;
    text-transform: uppercase;
    font-size: 10px !important;
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
