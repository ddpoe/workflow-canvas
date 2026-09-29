<script lang="ts">
  import { SvelteFlow, Background, Controls, type NodeTypes, type EdgeTypes, type Node, type Edge } from '@xyflow/svelte';
  import type { Connection } from '@xyflow/system';
  import '@xyflow/svelte/dist/style.css';
  import './app.css';

  import CustomNode from './lib/builder/CustomNode.svelte';
  import Sidebar from './lib/builder/Sidebar.svelte';
  import InspectorPanel from './lib/inspector/InspectorPanel.svelte';
  import Toolbar from './lib/builder/Toolbar.svelte';
  import { isDuplicateSlotEdge as isDuplicateSlotEdgeOn } from './lib/graph/legality.js';
  import RunsPreview from './lib/builder/RunsPreview.svelte';
  import DevToolbar from './lib/builder/DevToolbar.svelte';
  import HistoryView from './lib/history/HistoryView.svelte';
  import { selectRun } from './lib/history/historyStore.js';
  import RegistryTab from './lib/registry/RegistryTab.svelte';
  import FlowHelper from './lib/builder/FlowHelper.svelte';
  import DeletableEdge from './lib/builder/DeletableEdge.svelte';
  import ConfirmDialog from './lib/shared/ConfirmDialog.svelte';
  import LockSummary from './lib/builder/LockSummary.svelte';
  import GraftToast from './lib/shared/GraftToast.svelte';
  import { confirmDialogState, graftToastState, centerOnNodeRequest } from './lib/shared/uiState.js';
  import { nodes, edges, selectedNodeId, nextNodeId, deleteNodes, loadSamples, runState, setPipelineError, flashToast, dismissFlash, modules as modulesStore } from './lib/builder/stores.js';
  // Side-effect import — starts the singleton pipelineRunActor and (in
  // DEV) wires the Stately Inspector.
  import './lib/machines/root.js';
  import { pushState, initKeyboardShortcuts } from './lib/builder/undo.js';
  import { nodeFromDragPayload } from './lib/builder/nodeData.js';
  import { fetchDemoPipelineDocument, fetchDevStatus } from './lib/builder/api.js';
  // Playwright bootstrap. `?fixture=<key>` seeds the canvas
  // synchronously before mount and may ask for a starting tab. The seeding
  // itself lives in `lib/__fixtures__/seed.ts`, imported statically so it
  // stays synchronous for every seeded spec.
  import { seedFixtureFromQuery } from './lib/__fixtures__/seed.js';
  import type { XYPosition } from '@xyflow/system';
  import { get } from 'svelte/store';

  const _fixtureTab = seedFixtureFromQuery();
  let activeTab = $state<'builder' | 'registry' | 'history'>(_fixtureTab ?? 'builder');
  // Preview is on by default — it's the cheapest way for the user to see
  // what jobs the current canvas will compile to before committing a run.
  // A collapsed pull-tab on the right edge of the flow area reopens it
  // when the user hides it via the RunsPreview close button.
  let previewOpen = $state(true);
  let flowScreenToFlowPosition: ((pos: XYPosition) => XYPosition) | null = $state(null);
  let flowFitView: (() => Promise<boolean>) | null = $state(null);

  // Panel widths (resizable)
  let sidebarWidth = $state(210);
  let inspectorWidth = $state(240);
  let dragging = $state<'sidebar' | 'inspector' | null>(null);
  let devMode = $state(false);

  // `?pipeline=demo` — demo pre-wiring (`wfc demo` opens the browser on
  // this URL). Fetches GET /api/pipelines/demo and hands the document to
  // loadPipeline() — NEVER the seedFixture store-poking path — so each
  // node's real method-specific slots resolve against the registry. The
  // modules store is populated asynchronously by Sidebar's fetchModules;
  // loadPipeline must wait for it or every method node falls back to the
  // generic data/output CSV pair.
  const _pipelineKey = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search).get('pipeline')
    : null;
  function waitForModules(timeoutMs: number): Promise<void> {
    return new Promise(resolve => {
      let done = false;
      let unsub: (() => void) | null = null;
      const finish = () => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        if (unsub) unsub();
        resolve();
      };
      const timer = setTimeout(finish, timeoutMs);
      unsub = modulesStore.subscribe(m => { if (m.length > 0) finish(); });
      if (done && unsub) unsub();
    });
  }
  async function loadDemoPipeline(): Promise<void> {
    try {
      const json = await fetchDemoPipelineDocument();
      if (json === null) return; // no demo scaffolded — inert
      const { loadPipeline } = await import('./lib/builder/pipeline.js');
      await waitForModules(10_000);
      loadPipeline(json);
    } catch { /* inert on any failure — normal canvas boot continues */ }
  }
  if (_pipelineKey === 'demo') loadDemoPipeline();

  $effect(() => {
    fetchDevStatus()
      .then(data => { if (data?.dev) devMode = true; })
      .catch(() => {});
    loadSamples();
  });

  function onResizeStart(panel: 'sidebar' | 'inspector', e: MouseEvent) {
    e.preventDefault();
    dragging = panel;
    const startX = e.clientX;
    const startW = panel === 'sidebar' ? sidebarWidth : inspectorWidth;

    function onMove(ev: MouseEvent) {
      const delta = ev.clientX - startX;
      if (panel === 'sidebar') {
        sidebarWidth = Math.max(160, Math.min(400, startW + delta));
      } else {
        inspectorWidth = Math.max(180, Math.min(500, startW - delta));
      }
    }
    function onUp() {
      dragging = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  // Context menu state
  let contextMenu = $state<{ x: number; y: number; nodeId: string } | null>(null);

  const nodeTypes: NodeTypes = {
    custom: CustomNode as any,
  };

  const edgeTypes: EdgeTypes = {
    deletable: DeletableEdge as any,
  };

  function onDrop(event: DragEvent) {
    event.preventDefault();
    if (!event.dataTransfer) return;
    const json = event.dataTransfer.getData('application/json');
    if (!json) return;

    const parsed = JSON.parse(json);
    const position = flowScreenToFlowPosition
      ? flowScreenToFlowPosition({ x: event.clientX, y: event.clientY })
      : { x: event.clientX - (event.currentTarget as HTMLElement).getBoundingClientRect().left,
          y: event.clientY - (event.currentTarget as HTMLElement).getBoundingClientRect().top };

    pushState();
    const id = nextNodeId();
    // One writer per node type, in `nodeData.ts`: the payload decides which
    // (a system node carries `_systemNode`; a method is its bare MethodDef).
    nodes.update(n => [...n, nodeFromDragPayload(id, position, parsed)]);
  }

  function onDragOver(event: DragEvent) {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
  }

  function handleNodeClick({ node }: { node: Node; event: MouseEvent | TouchEvent }) {
    selectedNodeId.set(node.id);
  }

  function handleNodeDragStop({ nodes: draggedNodes }: { nodes: Node[] }) {
    // Sync SvelteFlow's internally tracked positions back to our store
    // so that future store updates don't snap nodes to stale positions.
    nodes.update($nodes => {
      const posMap = new Map(draggedNodes.map(n => [n.id, n.position]));
      for (const n of $nodes) {
        const pos = posMap.get(n.id);
        if (pos) {
          n.position = pos;
        }
      }
      return [...$nodes];
    });
  }

  function handlePaneClick() {
    selectedNodeId.set(null);
  }

  let rejectMessage = $state<string | null>(null);
  let rejectTimer: ReturnType<typeof setTimeout> | null = null;
  function showReject(msg: string) {
    rejectMessage = msg;
    if (rejectTimer) clearTimeout(rejectTimer);
    rejectTimer = setTimeout(() => { rejectMessage = null; }, 2500);
  }

  // Is this node id a run_reference system node?
  function isReferenceSource(nodeId: string | null | undefined): boolean {
    if (!nodeId) return false;
    return get(nodes).some(n => n.id === nodeId && n.data?.nodeType === 'run_reference');
  }

  // A second edge into an occupied (target, targetHandle) slot is refused.
  // The rule is the pure predicate in lib/graph/legality.ts — a preview of
  // the server's structural validation, kept aligned by the shared shape
  // corpus; this binds it to the canvas stores.
  function isDuplicateSlotEdge(connection: Connection): boolean {
    return isDuplicateSlotEdgeOn(connection, get(edges), isReferenceSource);
  }

  // Svelte Flow hands this a connection candidate during drag. Return false
  // to prevent the drop.
  function isValidConnection(conn: Connection): boolean {
    return !isDuplicateSlotEdge(conn);
  }

  function handleConnect(connection: Connection) {
    // Defense-in-depth: isValidConnection should have already blocked dupes,
    // but re-check here in case a keyboard/API path bypasses it.
    const handle = connection.targetHandle ?? null;
    if (isDuplicateSlotEdge(connection)) {
      showReject(`Input slot '${handle ?? "(default)"}' on '${connection.target}' already has an edge. Only one edge per slot is supported.`);
      return;
    }
    pushState();
    const newEdge: Edge = {
      id: `e_${connection.source}_${connection.target}_${Date.now()}`,
      type: 'deletable',
      source: connection.source,
      target: connection.target,
      sourceHandle: connection.sourceHandle ?? null,
      targetHandle: connection.targetHandle ?? null,
    };
    edges.update(e => [...e, newEdge]);
  }

  function handleDelete({ nodes: deletedNodes, edges: deletedEdges }: { nodes: Node[]; edges: Edge[] }) {
    const hasNodes = deletedNodes.length > 0;
    const hasEdges = deletedEdges.length > 0;
    if (!hasNodes && !hasEdges) return;
    // One snapshot for the whole event. Deleting nodes goes through the
    // store, which records the snapshot and clears the selection, so only an
    // edge-only delete records one here.
    if (hasNodes) deleteNodes(deletedNodes.map(n => n.id));
    else pushState();
    if (hasEdges) {
      const ids = new Set(deletedEdges.map(e => e.id));
      edges.update(es => es.filter(e => !ids.has(e.id)));
    }
  }

  function handleKeydown(event: KeyboardEvent) {
    // Backspace deletes the selected node. It is also Svelte Flow's own
    // default delete key, so a press can reach the flow's `ondelete` as
    // well; the Delete key is bound to nothing. Both paths hand their ids to
    // the store, which records one snapshot and ignores a delete that
    // removes nothing, so one press is one undo step either way.
    if (event.key === 'Backspace') {
      const target = event.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') return;
      const id = get(selectedNodeId);
      if (id) {
        event.preventDefault();
        deleteNodes([id]);
      }
    }
  }

  function handleContextMenu(event: MouseEvent) {
    // Find if right-click was on a node
    const nodeEl = (event.target as HTMLElement).closest('.svelte-flow__node');
    if (!nodeEl) {
      contextMenu = null;
      return;
    }
    event.preventDefault();
    const nodeId = nodeEl.getAttribute('data-id');
    if (nodeId) {
      contextMenu = { x: event.clientX, y: event.clientY, nodeId };
      selectedNodeId.set(nodeId);
    }
  }

  function contextMenuDelete() {
    if (!contextMenu) return;
    deleteNodes([contextMenu.nodeId]);
    contextMenu = null;
  }

  function dismissContextMenu() {
    contextMenu = null;
  }

  $effect(() => { initKeyboardShortcuts(); });

  // Centering bridge. RunDetailPanel's [Jump to node] action publishes
  // a node id to `centerOnNodeRequest`; we subscribe here (where the
  // SvelteFlow `fitView` helper is bound) and animate the viewport to that
  // node, then reset the request so the same id can be requested again.
  $effect(() => {
    const id = $centerOnNodeRequest;
    if (!id) return;
    if (flowFitView) {
      // `fitView` accepts `{ nodes: [{ id }] }` to fit the view to a
      // specific subset of nodes. Padding/duration tuned for "center
      // the node, animate smoothly, leave breathing room around it".
      flowFitView({
        nodes: [{ id }],
        duration: 400,
        padding: 0.4,
      } as any);
    }
    centerOnNodeRequest.set(null);
  });
</script>

<div class="app-root">
  <Toolbar {activeTab} onTabChange={(tab) => { activeTab = tab; }} />
  <div class="main-content" class:resizing={dragging !== null} style:display={activeTab === 'builder' ? 'flex' : 'none'}>
    <div style="width: {sidebarWidth}px; flex-shrink: 0;">
      <Sidebar />
    </div>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="resize-handle" onmousedown={(e: MouseEvent) => onResizeStart('sidebar', e)}></div>
    <div class="canvas-column">
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="canvas-area" ondrop={onDrop} ondragover={onDragOver} onkeydown={handleKeydown}
           oncontextmenu={handleContextMenu} role="application">
        <SvelteFlow
          {nodeTypes}
          nodes={$nodes}
          edges={$edges}
          nodesDraggable={true}
          nodesConnectable={true}
          elementsSelectable={true}
          panOnDrag={true}
          selectionOnDrag={false}
          selectionKey="Shift"
          onconnect={handleConnect}
          isValidConnection={isValidConnection}
          edgeTypes={edgeTypes}
          defaultEdgeOptions={{ type: 'deletable' }}
          onnodeclick={handleNodeClick}
          onnodedragstop={handleNodeDragStop}
          onpaneclick={() => { handlePaneClick(); dismissContextMenu(); }}
          ondelete={handleDelete}
          oninit={() => {}}
        >
          <Background gap={20} size={2} color="#444" />
          <Controls />
          <FlowHelper onReady={({ screenToFlowPosition, fitView: fv }) => {
            flowScreenToFlowPosition = screenToFlowPosition;
            flowFitView = fv;
          }} />
        </SvelteFlow>
        {#if devMode}
          <DevToolbar fitView={flowFitView} />
        {/if}
      </div>
      {#if previewOpen}
        <RunsPreview onClose={() => { previewOpen = false; }}
          onOpenRun={(runId) => { selectRun(runId); activeTab = 'history'; }} />
      {:else}
        <!-- Collapsed pull-tab: visible along the bottom edge of the
             canvas column so the user always knows the preview exists. -->
        <button class="preview-reopen-tab"
                onclick={() => { previewOpen = true; }}
                title="Show Runs Preview — live projection of the jobs this canvas will compile to">
          <span class="reopen-chevron">&#9650;</span>
          Runs Preview
        </button>
      {/if}
    </div>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="resize-handle" onmousedown={(e: MouseEvent) => onResizeStart('inspector', e)}></div>
    <div style="width: {inspectorWidth}px; flex-shrink: 0;">
      <InspectorPanel />
    </div>
  </div>
  {#if $runState.pipelineError}
    <div class="pipeline-error-banner" role="alert" data-testid="pipeline-error-banner">
      <div class="pe-icon" aria-hidden="true">
        {#if $runState.pipelineError.kind === 'dirty_repo'}⚠{:else if $runState.pipelineError.kind === 'not_runnable_docker'}🐳{:else if $runState.pipelineError.kind === 'not_runnable_git'}🔧{:else}✖{/if}
      </div>
      <div class="pe-body">
        <div class="pe-message">{$runState.pipelineError.message}</div>
        {#if $runState.pipelineError.hint}
          <div class="pe-hint">{$runState.pipelineError.hint}</div>
        {/if}
      </div>
      <button
        type="button"
        class="pe-close"
        aria-label="Dismiss error"
        title="Dismiss"
        onclick={() => setPipelineError(null)}
      >×</button>
    </div>
  {/if}
  {#if rejectMessage}
    <div class="reject-toast" role="alert">{rejectMessage}</div>
  {/if}
  {#if $flashToast}
    <button
      type="button"
      class="flash-toast flash-{$flashToast.kind}"
      onclick={dismissFlash}
      title="Dismiss"
    >{$flashToast.message}</button>
  {/if}
  {#if contextMenu}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="context-overlay" onclick={dismissContextMenu} oncontextmenu={(e) => { e.preventDefault(); dismissContextMenu(); }}>
      <div class="context-menu" style="left: {contextMenu.x}px; top: {contextMenu.y}px;">
        <button class="context-item delete" onclick={contextMenuDelete}>
          <span class="context-icon">&#10005;</span> Delete Node
        </button>
      </div>
    </div>
  {/if}
  <div class="history-tab-container" style:display={activeTab === 'registry' ? 'flex' : 'none'}>
    <RegistryTab visible={activeTab === 'registry'} />
  </div>
  <div class="history-tab-container" style:display={activeTab === 'history' ? 'flex' : 'none'}>
    <HistoryView visible={activeTab === 'history'} />
  </div>
  {#if $confirmDialogState}
    <ConfirmDialog
      variant={$confirmDialogState.variant}
      currentName={$confirmDialogState.currentName}
      targetName={$confirmDialogState.targetName}
      runningPipelineLabel={$confirmDialogState.runningPipelineLabel}
      onCancel={() => $confirmDialogState!.resolve(false)}
      onConfirm={() => $confirmDialogState!.resolve(true)}
    />
  {/if}
  <LockSummary />
  {#if $graftToastState}
    <GraftToast
      message={$graftToastState.message}
      detail={$graftToastState.detail}
      onJump={$graftToastState.onJump}
      onDismiss={() => graftToastState.set(null)}
    />
  {/if}
</div>

<style>
  .app-root {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    background: #1a1a1a;
  }
  .main-content {
    display: flex;
    flex: 1;
    overflow: hidden;
  }
  .canvas-column {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-width: 0;
  }
  .canvas-area {
    flex: 1;
    position: relative;
    overflow: hidden;
    min-height: 0;
  }
  /* Collapsed-preview reopen tab — sits along the bottom of the canvas
     column when the preview is hidden. Thin, unobtrusive, labeled so the
     user knows the feature exists even on first load. */
  .preview-reopen-tab {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    height: 24px;
    flex-shrink: 0;
    background: #252526;
    border: none;
    border-top: 1px solid var(--border);
    color: #888;
    font-size: 11px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }
  .preview-reopen-tab:hover {
    background: #2d2d30;
    color: #ccc;
  }
  .reopen-chevron { font-size: 9px; line-height: 1; }
  .resize-handle {
    width: 4px;
    cursor: col-resize;
    background: transparent;
    flex-shrink: 0;
    z-index: 10;
    transition: background 0.15s;
  }
  .reject-toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    background: #3b1e1e;
    border: 1px solid #c94b4b;
    color: #ffdede;
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 13px;
    max-width: 560px;
    z-index: 1000;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  }
  /* Transient confirmation / low-stakes-failure toast. Lives at the bottom
     of the canvas, auto-dismisses, click to dismiss early. Distinct from the
     persistent pipeline-error-banner (top-center). */
  .flash-toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 13px;
    font-family: inherit;
    max-width: 560px;
    z-index: 1000;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
    cursor: pointer;
    border: 1px solid;
  }
  .flash-toast.flash-success {
    background: #1e3b28;
    border-color: #4bc97a;
    color: #d8f3d8;
  }
  .flash-toast.flash-error {
    background: #3b1e1e;
    border-color: #c94b4b;
    color: #ffdede;
  }
  .flash-toast.flash-info {
    background: #1e2a3b;
    border-color: #4b8ac9;
    color: #d8e8ff;
  }
  /* Pipeline-level error banner — persistent (user-dismissed) rather than
     auto-fading like reject-toast, because these represent actionable
     failures the user needs to address before the next Run. */
  .pipeline-error-banner {
    position: fixed;
    top: 60px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: flex-start;
    gap: 12px;
    background: #3b1e1e;
    border: 1px solid #c94b4b;
    color: #ffdede;
    padding: 12px 14px 12px 16px;
    border-radius: 6px;
    font-size: 13px;
    line-height: 1.4;
    max-width: 720px;
    min-width: 360px;
    z-index: 1001;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  }
  .pipeline-error-banner .pe-icon {
    font-size: 18px;
    color: #ff9a9a;
    line-height: 1.1;
    flex-shrink: 0;
  }
  .pipeline-error-banner .pe-body {
    flex: 1;
    min-width: 0;
  }
  .pipeline-error-banner .pe-message {
    white-space: pre-wrap;
    word-break: break-word;
    font-weight: 500;
  }
  .pipeline-error-banner .pe-hint {
    margin-top: 6px;
    color: #f0c4c4;
    font-size: 12px;
    font-style: italic;
  }
  .pipeline-error-banner .pe-close {
    background: transparent;
    border: none;
    color: #ffdede;
    font-size: 20px;
    line-height: 1;
    cursor: pointer;
    padding: 0 4px;
    margin-left: 4px;
    flex-shrink: 0;
    opacity: 0.75;
  }
  .pipeline-error-banner .pe-close:hover {
    opacity: 1;
  }
  .resize-handle:hover,
  .resizing .resize-handle {
    background: var(--accent);
  }
  .history-tab-container {
    flex: 1;
    overflow: hidden;
  }
  .context-overlay {
    position: fixed;
    inset: 0;
    z-index: 1000;
  }
  .context-menu {
    position: fixed;
    background: #2d2d30;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 4px 0;
    min-width: 140px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
    z-index: 1001;
  }
  .context-item {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 6px 12px;
    background: none;
    border: none;
    color: #ccc;
    font-size: 12px;
    cursor: pointer;
    text-align: left;
  }
  .context-item:hover {
    background: #094771;
  }
  .context-item.delete:hover {
    background: #5a1d1d;
    color: #E74C3C;
  }
  .context-icon {
    font-size: 10px;
    width: 14px;
    text-align: center;
  }
</style>
