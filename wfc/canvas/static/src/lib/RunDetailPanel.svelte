<script lang="ts">
  import {
    selectRun,
    showDescendants,
    setFavoriteOptimistic,
    renameRunOptimistic,
    deleteRunOptimistic,
    setArchivedOptimistic,
    jumpToPipelineRun,
  } from './historyStore.js';
  import { fetchLineagePipeline } from './historyApi.js';
  import {
    confirmReplaceIfDirty,
    checkRunningBlock,
    graftRunReference,
    loadPipeline,
    canvasPipelineId,
  } from './pipeline.js';
  import { showFlash, pipelineName as canvasPipelineName, nodes } from './stores.js';
  import { graftToastState, centerOnNodeRequest } from './uiState.js';
  import { get } from 'svelte/store';
  import {
    fetchRun,
    fetchCancelledDescendants,
    listArtifacts,
    artifactDownloadUrl,
    deriveArtifactType,
  } from './historyApi.js';
  import type { WfcRun, Artifact } from './historyApi.js';
  import {
    formatTimestamp,
    formatDuration,
    formatBytes,
    formatRelativeTime,
    statusColor,
  } from './historyUtils.js';

  interface Props {
    runId: string;
  }

  let { runId }: Props = $props();

  type Section = 'overview' | 'params' | 'metrics' | 'artifacts' | 'output';

  let run = $state<WfcRun | null>(null);
  let artifacts = $state<Artifact[]>([]);
  let artifactState = $state<'idle' | 'loading' | 'done'>('idle');
  let loadError = $state<string | null>(null);
  let activeSection = $state<Section>('overview');

  // Output / log streaming state. Populated when the Output tab is active;
  // backed by an EventSource against /api/wfc/run/{id}/stream-logs.
  type LogLine = { kind: 'stdout' | 'stderr'; line: string };
  // Same phase names as InspectorPanel — both surfaces consume the same
  // SSE endpoint and share the badge text/color contract.
  type LogPhase = 'idle' | 'connecting' | 'streaming' | 'succeeded' | 'failed' | 'cancelled';
  let logLines = $state<LogLine[]>([]);
  let logPhase = $state<LogPhase>('idle');
  let logTerminalStatus = $state<string | null>(null);
  let logTerminalError = $state<string | null>(null);
  let logTerminalTraceback = $state<string | null>(null);
  let logFullMode = $state(false);

  // Cascaded skips — runs cancelled because this run (or its subtree) failed.
  // Loaded lazily on the overview tab for failed runs.
  let cancelledDescendants = $state<WfcRun[] | null>(null);

  // Action row / local UI state
  let renaming = $state(false);
  let renameValue = $state('');
  let confirmDelete = $state(false);
  let confirmArchive = $state(false);
  let copiedToast = $state<string | null>(null);
  let expandedDirs = $state<Set<string>>(new Set());

  let isArchived = $derived(!!run?.archivedAt);

  $effect(() => {
    if (runId) {
      loadError = null;
      run = null;
      artifacts = [];
      artifactState = 'idle';
      activeSection = 'overview';
      renaming = false;
      confirmDelete = false;
      confirmArchive = false;
      expandedDirs = new Set();
      // Reset log-stream state so the Output tab re-connects for the new run.
      logLines = [];
      logPhase = 'idle';
      logTerminalStatus = null;
      logTerminalError = null;
      logTerminalTraceback = null;
      logFullMode = false;
      cancelledDescendants = null;
      fetchRun(runId)
        .then(r => { run = r; })
        .catch(err => { loadError = err instanceof Error ? err.message : String(err); });
    }
  });

  $effect(() => {
    // Load cascaded-skips only for failed runs; fire-and-forget, empty array
    // on any error so the UI stays quiet.
    if (!run || run.status !== 'failed' || cancelledDescendants !== null) return;
    fetchCancelledDescendants(run.id)
      .then(rs => { cancelledDescendants = rs; })
      .catch(() => { cancelledDescendants = []; });
  });

  $effect(() => {
    // Stream stdout/stderr while the Output tab is open. Re-opens on runId or
    // full-mode change; tears down on unmount, tab switch, or run change.
    if (activeSection !== 'output' || !runId) return;
    logLines = [];
    logPhase = 'connecting';
    logTerminalStatus = null;
    logTerminalError = null;
    logTerminalTraceback = null;
    const qs = logFullMode ? '?full=1' : '';
    const url = `/api/wfc/run/${encodeURIComponent(runId)}/stream-logs${qs}`;
    const es = new EventSource(url);
    es.onmessage = (ev) => {
      try {
        const p = JSON.parse(ev.data);
        if (p.type === 'stdout' || p.type === 'stderr') {
          logLines = [...logLines, { kind: p.type, line: p.data ?? '' }];
          if (logPhase === 'connecting') logPhase = 'streaming';
        } else if (p.type === 'terminal') {
          // Backend `_log_map_terminal_status` emits success/failed/cancelled.
          logPhase =
            p.status === 'success'
              ? 'succeeded'
              : p.status === 'cancelled'
                ? 'cancelled'
                : 'failed';
          logTerminalStatus = p.status ?? null;
          logTerminalError = p.error_message ?? null;
          logTerminalTraceback = p.error_traceback ?? null;
          es.close();
        }
      } catch {
        // malformed frame — ignore
      }
    };
    es.onerror = () => {
      const isFinal =
        logPhase === 'succeeded' || logPhase === 'failed' || logPhase === 'cancelled';
      if (!isFinal) {
        logPhase = 'failed';
        logTerminalStatus = 'failed';
        logTerminalError = 'Connection lost';
        logTerminalTraceback = null;
      }
      es.close();
    };
    return () => es.close();
  });

  function loadFullLog(): void {
    logFullMode = true;
  }

  $effect(() => {
    if (activeSection === 'artifacts' && artifactState === 'idle' && runId) {
      artifactState = 'loading';
      listArtifacts(runId)
        .then(a => { artifacts = a; artifactState = 'done'; })
        .catch(err => {
          loadError = err instanceof Error ? err.message : String(err);
          artifactState = 'done';
        });
    }
  });

  let errorMessage = $derived(run?.error_message ?? undefined);
  let errorTraceback = $derived(run?.error_traceback ?? undefined);

  function displayName(r: WfcRun): string {
    // Prefer a user-set nid (overrides the auto-version vN label).
    // Fall back to name (legacy field) → runName → method.
    if (r.nid && !/^v\d+$/.test(r.nid)) return r.nid;
    return r.name || r.runName || r.method;
  }

  function flashToast(msg: string): void {
    copiedToast = msg;
    setTimeout(() => { copiedToast = null; }, 1400);
  }

  async function copy(text: string, label = 'Copied'): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      flashToast(label);
    } catch {
      flashToast('Copy failed');
    }
  }

  function shareLink(): void {
    if (!run) return;
    const url = `${window.location.origin}${window.location.pathname}?run=${encodeURIComponent(run.id)}`;
    copy(url, 'Link copied');
  }

  function exportJson(): void {
    if (!run) return;
    const payload = {
      metadata: {
        id: run.id,
        nid: run.nid,
        method: run.method,
        module: run.module,
        status: run.status,
        timestamp: run.timestamp,
        duration: run.duration,
        dataSource: run.dataSource,
        parents: run.parents,
      },
      inputs: run.inputs,
      metrics: run.metrics,
      artifacts,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${run.id}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function startRename(): void {
    if (!run) return;
    // Seed with current nid if it's a user label (not an auto v-version).
    renameValue = run.nid && !/^v\d+$/.test(run.nid) ? run.nid : '';
    renaming = true;
  }

  function commitRename(): void {
    if (!run) { renaming = false; return; }
    const next = renameValue.trim();
    renaming = false;
    const current = run.nid && !/^v\d+$/.test(run.nid) ? run.nid : '';
    if (next !== current) {
      setFavoriteOrRename(() => renameRunOptimistic(run!.id, next));
    }
  }

  function cancelRename(): void {
    renaming = false;
  }

  async function toggleFavoriteClick(): Promise<void> {
    if (!run) return;
    const next = !run.favorite;
    // Optimistic UI: the store patches `runs[]`, so update the local `run`
    // pointer too so this panel reflects the change immediately.
    run = { ...run, favorite: next };
    try {
      await setFavoriteOptimistic(run.id, next);
    } catch (err) {
      if (run) run = { ...run, favorite: !next };
    }
  }

  async function setFavoriteOrRename(op: () => Promise<void>): Promise<void> {
    try {
      await op();
      // reflect the updated value from the store
      if (run) {
        const fresh = await fetchRun(run.id).catch(() => null);
        if (fresh) run = fresh;
      }
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
    }
  }

  async function performDelete(): Promise<void> {
    if (!run) return;
    const id = run.id;
    confirmDelete = false;
    try {
      await deleteRunOptimistic(id);
      selectRun(null);
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
    }
  }

  async function toggleArchive(): Promise<void> {
    if (!run) return;
    const next = !run.archivedAt;
    if (next) {
      // require confirm for archive; unarchive is a one-click reverse.
      confirmArchive = true;
      return;
    }
    try {
      run = { ...run, archivedAt: null };
      await setArchivedOptimistic(run.id, false);
      flashToast('Unarchived');
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
    }
  }

  async function performArchive(): Promise<void> {
    if (!run) return;
    confirmArchive = false;
    const id = run.id;
    try {
      run = { ...run, archivedAt: Date.now() };
      await setArchivedOptimistic(id, true);
      flashToast('Archived');
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
    }
  }

  function toggleDir(name: string): void {
    const next = new Set(expandedDirs);
    if (next.has(name)) next.delete(name); else next.add(name);
    expandedDirs = next;
  }

  // ----- Inline image preview (US-2) -----
  // Gate: the BACKEND `is_image` flag (browser-renderable set only:
  // png/jpg/jpeg/gif/svg/webp — computed by WfcProvider.list_artifacts).
  // Deliberately NOT the wider IMAGE_EXTS grouping set below, which
  // includes pdf/tif/tiff that an <img> cannot paint. Strictly additive:
  // every artifact keeps its download-link row.
  let lightboxSrc = $state<string | null>(null);
  let lightboxAlt = $state('');
  function openLightbox(src: string, alt: string): void {
    lightboxSrc = src;
    lightboxAlt = alt;
  }
  function closeLightbox(): void {
    lightboxSrc = null;
  }
  $effect(() => {
    if (!lightboxSrc) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeLightbox();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // ----- Artifact grouping -----
  const IMAGE_EXTS = new Set(['png','jpg','jpeg','gif','svg','webp','pdf','tif','tiff']);
  const DATA_EXTS  = new Set(['h5ad','h5','hdf5','parquet','csv','tsv','json','pkl','npy','npz','arrow','feather']);

  type CatKey = 'data' | 'images' | 'directories' | 'other';

  function categoryOf(a: Artifact): CatKey {
    if (deriveArtifactType(a) === 'dir') return 'directories';
    const ext = (a.extension || '').toLowerCase();
    if (IMAGE_EXTS.has(ext)) return 'images';
    if (DATA_EXTS.has(ext)) return 'data';
    return 'other';
  }

  const CAT_ORDER: CatKey[] = ['data', 'images', 'directories', 'other'];
  const CAT_ICON: Record<CatKey, string> = {
    data: '▣', images: '◐', directories: '▤', other: '○',
  };
  const CAT_COLOR: Record<CatKey, string> = {
    data:        'var(--color-completed, #50C878)',
    images:      'var(--accent, #4A90D9)',
    directories: '#B084CC',
    other:       'var(--text-secondary, #888)',
  };
  const CAT_LABEL: Record<CatKey, string> = {
    data: 'Data', images: 'Images', directories: 'Directories', other: 'Other',
  };

  let grouped = $derived.by(() => {
    const g: Record<CatKey, Artifact[]> = { data: [], images: [], directories: [], other: [] };
    for (const a of artifacts) g[categoryOf(a)].push(a);
    return g;
  });

  let totalSize = $derived(artifacts.reduce((s, a) => s + (a.size || 0), 0));
  let totalFiles = $derived(
    artifacts.reduce((s, a) => s + (deriveArtifactType(a) === 'dir' ? (a.count || 0) : 1), 0),
  );

  function formatMetric(v: unknown): string {
    if (typeof v !== 'number') return String(v);
    return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(4);
  }

  function formatParamValue(v: unknown): string {
    if (v === null || v === undefined) return '∅';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  // ---------- Load-in-Canvas action handlers (Actions 2, 3) ----------

  // Current-canvas pipelineId.  Read from the `canvasPipelineId` store
  // (D-10): the running-block is scoped to the *user's current canvas*,
  // not whatever historical run is currently being inspected.  Earlier
  // iterations stand-in'd `run?.pipelineId` here, which made Actions 2
  // & 3 falsely block when the user clicked a row from a still-running
  // pipeline whose canvas they had already moved on from.  See review
  // iter 1 root-cause notes.
  function currentCanvasPipelineId(): string | null {
    return get(canvasPipelineId);
  }

  async function handleOpenLineage(): Promise<void> {
    if (!run) return;
    if (await checkRunningBlock(currentCanvasPipelineId())) return;
    if (!(await confirmReplaceIfDirty(`lineage of run #${run.id}`))) return;
    try {
      const json = await fetchLineagePipeline(run.id);
      loadPipeline(json);
      // Action 2: synthesized lineage is NOT a submitted pipeline, so
      // clear the canvas pipelineId.  Subsequent gates correctly treat
      // the canvas as having no in-flight identity.
      canvasPipelineId.set(null);
      showFlash(`Loaded lineage for run ${run.id} into canvas`, 'success');
    } catch (err) {
      const code = err instanceof Error ? err.message : String(err);
      if (code === 'LINEAGE_SYNTHESIS_FAILED') {
        showFlash(
          "Couldn't reconstruct lineage — the run's ancestor chain is malformed or too long.",
          'error',
        );
      } else if (code === 'LINEAGE_RUN_NOT_FOUND') {
        showFlash('Run not found.', 'error');
      } else {
        showFlash(`Failed to load lineage: ${code}`, 'error');
      }
    }
  }

  async function handleReferenceInCanvas(): Promise<void> {
    if (!run) return;
    if (await checkRunningBlock(currentCanvasPipelineId())) return;
    // No dirty-confirm: graft is additive (D-3).
    const newNodeId = graftRunReference(run.id, { method: run.method });
    // Drive the styled GraftToast (D-1). [Jump to node] selects the new
    // node and asks App.svelte to center the canvas on it (D-13). The
    // centering bridge writes a node id into `centerOnNodeRequest`;
    // App.svelte subscribes there and calls SvelteFlow's `fitView` with
    // that node — this avoids prop-drilling `fitView` through HistoryView
    // → RunDetailPanel.
    graftToastState.set({
      message: 'Reference added to Canvas',
      detail: `${run.method} · ${run.id}`,
      onJump: () => {
        nodes.update(ns => ns.map(n => ({ ...n, selected: n.id === newNodeId })));
        centerOnNodeRequest.set(newNodeId);
        graftToastState.set(null);
      },
    });
  }

  function pickHighlights(metrics: Record<string, number>): Array<{ label: string; value: string }> {
    const entries = Object.entries(metrics);
    return entries.slice(0, 4).map(([k, v]) => ({
      label: k.replace(/_/g, ' '),
      value: formatMetric(v),
    }));
  }
</script>

<div class="detail-panel">
  {#if loadError}
    <div class="detail-header">
      <span class="crumb">History</span>
      <button class="icon-btn" onclick={() => selectRun(null)} title="Close">×</button>
    </div>
    <div class="detail-error">Error: {loadError}</div>
  {:else if !run}
    <div class="detail-header">
      <span class="crumb">History</span>
      <button class="icon-btn" onclick={() => selectRun(null)} title="Close">×</button>
    </div>
    <div class="detail-loading">Loading…</div>
  {:else}
    <!-- Header strip -->
    <div class="detail-header">
      <div class="crumb">
        <span class="crumb-text">History</span>
        <span class="crumb-sep">›</span>
        <button class="crumb-id" onclick={() => copy(run!.id, 'Run ID copied')} title="Copy run ID">{run.id}</button>
      </div>
      <div class="header-actions">
        <button class="icon-btn" onclick={shareLink} title="Copy share link">⎘</button>
        <button class="icon-btn" onclick={exportJson} title="Export JSON">⤓</button>
        <button class="icon-btn" onclick={() => selectRun(null)} title="Close">×</button>
      </div>
    </div>

    <!-- Action row -->
    <div class="action-row">
      <button
        class="fav-btn"
        class:active={run.favorite}
        onclick={toggleFavoriteClick}
        title={run.favorite ? 'Unfavorite' : 'Favorite'}
      >{run.favorite ? '★' : '☆'}</button>

      {#if renaming}
        <!-- svelte-ignore a11y_autofocus -->
        <input
          class="rename-input"
          bind:value={renameValue}
          onblur={commitRename}
          onkeydown={(e) => {
            if (e.key === 'Enter') commitRename();
            else if (e.key === 'Escape') cancelRename();
          }}
          autofocus
        />
      {:else}
        <button class="rename-btn" onclick={startRename} title="Rename run">
          <span class="g">✎</span><span>Rename</span>
        </button>
      {/if}

      {#if isArchived}
        <button
          class="archive-btn unarchive"
          onclick={toggleArchive}
          title="Restore run to active list"
        >
          <span class="g">↺</span><span>Unarchive</span>
        </button>
      {:else}
        <button
          class="archive-btn"
          onclick={toggleArchive}
          title="Archive run (soft-delete; can be restored)"
        >
          <span class="g">🗄</span><span>Archive</span>
        </button>
      {/if}
    </div>

    <!-- Hero card -->
    <div class="hero">
      <div
        class="hero-accent"
        style="background: linear-gradient(90deg, {statusColor(run.status)}, var(--accent, #4A90D9));"
      ></div>
      <div class="hero-body">
        <div class="hero-row-1">
          <span
            class="status-pill"
            style="color: {statusColor(run.status)}; border-color: color-mix(in oklab, {statusColor(run.status)} 40%, transparent); background: color-mix(in oklab, {statusColor(run.status)} 15%, transparent);"
          >● {run.status}</span>
          {#if isArchived}
            <span class="archived-pill" title="Archived — hidden from default list">🗄 Archived</span>
          {/if}
          <span class="hero-time">{formatRelativeTime(run.timestamp)}</span>
          <span class="hero-duration">{formatDuration(run.duration)}</span>
        </div>

        <div class="hero-method">{displayName(run)}</div>
        <div class="hero-sub">
          in <span class="hero-module">{run.module}</span>
          {#if run.dataSource === '__all__' && run.bundledSamples && run.bundledSamples.length > 0}
            · on <span class="hero-source" title={run.bundledSamples.join(', ')}>
              {run.bundledSamples.length} samples ({run.bundledSamples.join(', ')})
            </span>
          {:else if run.dataSource}
            · on <span class="hero-source">{run.dataSource}</span>
          {/if}
        </div>

        {#if run.tags && run.tags.length > 0}
          <div class="tags-row">
            {#each run.tags as tag}
              <span class="tag">{tag}</span>
            {/each}
          </div>
        {/if}
      </div>
    </div>

    <!-- Tabs row -->
    <div class="tabs">
      {#each [
        { key: 'overview'  as Section, label: 'Overview',   count: null as number | null },
        { key: 'params'    as Section, label: 'Parameters', count: Object.keys(run.inputs ?? {}).length  as number | null },
        { key: 'metrics'   as Section, label: 'Metrics',    count: Object.keys(run.metrics ?? {}).length as number | null },
        { key: 'artifacts' as Section, label: 'Artifacts',  count: artifactState === 'done' ? artifacts.length : null },
        { key: 'output'    as Section, label: 'Output',     count: null as number | null },
      ] as tab}
        <button
          class="tab"
          class:active={activeSection === tab.key}
          class:no-meta={tab.count === null}
          onclick={() => { activeSection = tab.key; }}
        >
          <span class="tab-label">{tab.label}</span>
          {#if tab.count !== null}
            <span class="tab-divider"></span>
            <span class="tab-count">{tab.count}</span>
          {/if}
        </button>
      {/each}
    </div>

    <!-- Scrollable body -->
    <div class="body">
      {#if activeSection === 'overview'}
        <div class="overview">
          <div class="card">
            <div class="facts-grid">
              <span class="fact-label">Run ID</span>
              <span class="fact-mono">{run.id}</span>
              <span class="fact-label">NID</span>
              <span class="fact-mono strong">{run.nid}</span>
              <span class="fact-label">Started</span>
              <span class="fact-value">{formatTimestamp(run.timestamp)}</span>
              {#if run.cacheSourceRunId}
                {@const cacheSrcId = run.cacheSourceRunId}
                <span class="fact-label">Cached from</span>
                <button class="fact-link cache-chip" type="button"
                  onclick={() => selectRun(cacheSrcId)}
                  title="This run reused outputs from run #{cacheSrcId} — click to inspect the source.">
                  ♻ #{cacheSrcId}
                </button>
              {/if}
              {#if run.pipelineId}
                <span class="fact-label">Pipeline</span>
                <button
                  class="fact-link pipeline-meta"
                  type="button"
                  onclick={() => jumpToPipelineRun(run!.pipelineId!, run!.id)}
                  title="Switch to Pipelines view and highlight this run"
                >{run.pipelineId} ↗</button>
              {/if}
              {#if run.parents && run.parents.length > 0}
                <span class="fact-label">Parent{run.parents.length === 1 ? ' run' : 's'}</span>
                <div class="parent-list">
                  {#each run.parents as p (p.slot + ':' + p.sourceRunId)}
                    <button class="fact-link parent-chip" type="button"
                      onclick={() => selectRun(p.sourceRunId)}
                      title={`Slot "${p.slot}" ← run #${p.sourceRunId}`}>
                      <span class="parent-slot">{p.slot}</span>
                      <span class="parent-arrow">←</span>
                      <span class="parent-run">#{p.sourceRunId}</span>
                    </button>
                  {/each}
                </div>
              {/if}
            </div>
          </div>

          {#if run.metrics && Object.keys(run.metrics).length > 0}
            <div class="highlights">
              {#each pickHighlights(run.metrics) as m}
                <div class="card highlight">
                  <div class="hl-label">{m.label}</div>
                  <div class="hl-value">{m.value}</div>
                </div>
              {/each}
            </div>
          {/if}

          {#if run.status === 'cancelled' && run.cancelledDueToRunId != null}
            {@const cancelledDueId = run.cancelledDueToRunId}
            <div class="cancel-banner">
              Cancelled because run
              <button
                type="button"
                class="fact-link"
                onclick={() => selectRun(cancelledDueId)}
              >#{cancelledDueId}</button>
              failed.
            </div>
          {/if}

          {#if run.status === 'failed' && (errorMessage || errorTraceback)}
            <div class="err-block">
              {#if errorMessage}<div class="err-msg">{errorMessage}</div>{/if}
              {#if errorTraceback}<pre class="err-trace">{errorTraceback}</pre>{/if}
            </div>
          {/if}

          {#if run.status === 'failed' && cancelledDescendants && cancelledDescendants.length > 0}
            <div class="cascade-block">
              <div class="cascade-header">
                Cascaded skips · {cancelledDescendants.length}
                {cancelledDescendants.length === 1 ? 'run' : 'runs'} cancelled downstream
              </div>
              <div class="cascade-list">
                {#each cancelledDescendants as d}
                  <button
                    type="button"
                    class="cascade-row"
                    onclick={() => selectRun(d.id)}
                  >
                    <span class="cascade-method">{d.method}</span>
                    {#if d.dataSource}<span class="cascade-sample">· {d.dataSource}</span>{/if}
                    <span class="cascade-id">#{d.id}</span>
                  </button>
                {/each}
              </div>
            </div>
          {/if}
        </div>

      {:else if activeSection === 'params'}
        <div class="card flat-list">
          {#if !run.inputs || Object.keys(run.inputs).length === 0}
            <div class="empty">No parameters recorded.</div>
          {:else}
            {#each Object.entries(run.inputs) as [k, v]}
              <div class="flat-row">
                <span class="flat-key">{k}</span>
                <span class="flat-val">{formatParamValue(v)}</span>
              </div>
            {/each}
          {/if}
        </div>

      {:else if activeSection === 'metrics'}
        <div class="card flat-list">
          {#if !run.metrics || Object.keys(run.metrics).length === 0}
            <div class="empty">No metrics recorded.</div>
          {:else}
            {#each Object.entries(run.metrics) as [k, v]}
              <div class="flat-row">
                <span class="flat-key">{k}</span>
                <span class="flat-val metric-val">{formatMetric(v)}</span>
              </div>
            {/each}
          {/if}
        </div>

      {:else if activeSection === 'artifacts'}
        {#if artifactState !== 'done'}
          <div class="empty">Loading artifacts…</div>
        {:else if artifacts.length === 0}
          <div class="empty">No artifacts found.</div>
        {:else}
          <div class="artifacts">
            <div class="artifacts-summary">
              {artifacts.length} artifacts · {totalFiles} files · {formatBytes(totalSize)}
            </div>
            {#each CAT_ORDER as cat}
              {#if grouped[cat].length > 0}
                {@const items = grouped[cat]}
                {@const catTotal = items.reduce((s, a) => s + (a.size || 0), 0)}
                <div class="card cat-card">
                  <div class="cat-header">
                    <span
                      class="cat-icon"
                      style="color: {CAT_COLOR[cat]}; background: color-mix(in oklab, {CAT_COLOR[cat]} 18%, transparent);"
                    >{CAT_ICON[cat]}</span>
                    <span class="cat-name">{CAT_LABEL[cat]}</span>
                    <span class="cat-count">{items.length} · {formatBytes(catTotal)}</span>
                  </div>
                  {#each items as a}
                    {#if deriveArtifactType(a) === 'dir'}
                      <div class="dir-row">
                        <button class="dir-head" onclick={() => toggleDir(a.name)}>
                          <span class="dir-caret">{expandedDirs.has(a.name) ? '▾' : '▸'}</span>
                          <span class="dir-name">{a.name}</span>
                          <span
                            class="dir-pill"
                            style="color: {CAT_COLOR[cat]}; border-color: color-mix(in oklab, {CAT_COLOR[cat]} 30%, transparent); background: color-mix(in oklab, {CAT_COLOR[cat]} 15%, transparent);"
                          >DIR{a.count != null ? ` · ${a.count}` : ''}</span>
                          <span class="dir-size">{formatBytes(a.size)}</span>
                        </button>
                        {#if expandedDirs.has(a.name) && a.children}
                          <div class="dir-children">
                            {#each a.children as c}
                              {#if c.name.endsWith('/')}
                                <!-- Subdirectory: no drill-down yet; render as static row. -->
                                <div class="child-row">
                                  <span class="child-dash">─</span>
                                  <span class="child-name">{c.name}</span>
                                </div>
                              {:else}
                                <a
                                  class="child-row child-row-link"
                                  href={artifactDownloadUrl(run.id, a.name + c.name)}
                                  target="_blank"
                                  rel="noopener"
                                >
                                  <span class="child-dash">─</span>
                                  <span class="child-name">{c.name}</span>
                                  {#if c.size > 0}<span class="child-size">{formatBytes(c.size)}</span>{/if}
                                </a>
                              {/if}
                            {/each}
                          </div>
                        {/if}
                      </div>
                    {:else}
                      <a
                        class="file-row"
                        href={artifactDownloadUrl(run.id, a.name)}
                        target="_blank"
                        rel="noopener"
                      >
                        <span class="file-bullet">▪</span>
                        <span class="file-name">{a.name}</span>
                        <span
                          class="file-ext"
                          style="color: {CAT_COLOR[cat]}; border-color: color-mix(in oklab, {CAT_COLOR[cat]} 40%, transparent);"
                        >{a.extension || '—'}</span>
                        <span class="file-size">{formatBytes(a.size)}</span>
                      </a>
                      {#if a.is_image}
                        <!-- Additive inline preview: link row above stays.
                             Light card — a PNG always renders on its light
                             surface, so it must not float on a dark panel. -->
                        <button
                          class="thumb-card"
                          onclick={() => openLightbox(artifactDownloadUrl(run.id, a.name), a.name)}
                          aria-label={`Preview ${a.name}`}
                        >
                          <img
                            class="thumb-img"
                            src={artifactDownloadUrl(run.id, a.name)}
                            alt={a.name}
                            loading="lazy"
                          />
                        </button>
                      {/if}
                    {/if}
                  {/each}
                </div>
              {/if}
            {/each}
          </div>
        {/if}
        {#if lightboxSrc}
          <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
          <div class="lightbox-overlay" onclick={closeLightbox}>
            <img
              class="lightbox-img"
              src={lightboxSrc}
              alt={lightboxAlt}
              onclick={(e: MouseEvent) => e.stopPropagation()}
            />
          </div>
        {/if}

      {:else if activeSection === 'output'}
        <div class="output-block">
          <div class="output-header">
            <span class="output-status output-status-{logPhase}">
              {#if logPhase === 'connecting'}Connecting…
              {:else if logPhase === 'streaming'}Streaming
              {:else if logPhase === 'succeeded'}Success
              {:else if logPhase === 'failed'}
                {logTerminalError ? `Failed: ${logTerminalError}` : 'Failed'}
              {:else if logPhase === 'cancelled'}
                {logTerminalError ? `Cancelled: ${logTerminalError}` : 'Cancelled'}
              {:else}Idle{/if}
            </span>
            {#if !logFullMode && (logPhase === 'succeeded' || logPhase === 'failed' || logPhase === 'cancelled')}
              <button class="footer-btn" onclick={loadFullLog}>Load full log</button>
            {/if}
            {#if logFullMode}
              <span class="output-hint">full log</span>
            {/if}
          </div>
          {#if logLines.length === 0 && logPhase !== 'connecting'}
            <div class="empty">No output captured.</div>
          {:else}
            <pre class="log-pane">{#each logLines as l}<span class="log-line log-{l.kind}">{l.line}</span>
{/each}</pre>
          {/if}
          {#if (logPhase === 'failed' || logPhase === 'cancelled') && (logTerminalError || logTerminalTraceback)}
            <div class="err-block">
              {#if logTerminalError}<div class="err-msg">{logTerminalError}</div>{/if}
              {#if logTerminalTraceback}<pre class="err-trace">{logTerminalTraceback}</pre>{/if}
            </div>
          {/if}
        </div>
      {/if}
    </div>

    <!-- → View Descendants stays as-is between metadata and tabs in the
         original SPEC, but the existing component nests it in the footer.
         We add Open lineage / Reference here per SPEC §"footer" change. -->
    <div class="footer">
      <button class="footer-btn primary" onclick={() => showDescendants(run!.id)}>→ Descendants</button>
      <button class="footer-btn accent" onclick={handleOpenLineage}>Open lineage in Canvas</button>
      <button class="footer-btn" onclick={handleReferenceInCanvas}>Reference in Canvas</button>
    </div>

    {#if confirmDelete}
      <div class="confirm-overlay" role="dialog" aria-modal="true">
        <div class="confirm-card">
          <div class="confirm-title">Delete run?</div>
          <div class="confirm-body">
            Delete run <span class="confirm-id">{run.id}</span>? This cannot be undone.
          </div>
          <div class="confirm-actions">
            <button class="footer-btn" onclick={() => { confirmDelete = false; }}>Cancel</button>
            <button class="footer-btn danger" onclick={performDelete}>Delete</button>
          </div>
        </div>
      </div>
    {/if}

    {#if confirmArchive}
      <div class="confirm-overlay" role="dialog" aria-modal="true">
        <div class="confirm-card">
          <div class="confirm-title">Archive run?</div>
          <div class="confirm-body">
            Archive run <span class="confirm-id">{run.id}</span>? It will be hidden
            from the default history view but can be restored at any time.
          </div>
          <div class="confirm-actions">
            <button class="footer-btn" onclick={() => { confirmArchive = false; }}>Cancel</button>
            <button class="footer-btn primary" onclick={performArchive}>Archive</button>
          </div>
        </div>
      </div>
    {/if}

    {#if copiedToast}
      <div class="toast" aria-live="polite">{copiedToast}</div>
    {/if}
  {/if}
</div>

<style>
  .detail-panel {
    /* Remap the grey text tokens for this panel only: every `color:
       var(--text-secondary|--text-muted)` inside .detail-panel resolves
       to the primary color so captions, counts, sizes, and dim labels
       stay readable. Borders/dividers use `--border` and are unaffected. */
    --text-secondary: var(--text-primary, #ccc);
    --text-muted: var(--text-primary, #ccc);

    width: 360px;
    background: var(--bg-panel, #252526);
    border-left: 1px solid var(--border, #3e3e42);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
    font-family: 'Mukta Vaani', 'Segoe UI', sans-serif;
    font-size: 12px;
    color: var(--text-primary, #ccc);
    position: relative;
  }

  /* Header strip */
  .detail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: var(--bg-header, #2d2d30);
    border-bottom: 1px solid var(--border, #3e3e42);
    flex-shrink: 0;
  }
  .crumb {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--text-secondary, #888);
    min-width: 0;
  }
  .crumb-text, .crumb-sep { color: var(--text-muted, #666); }
  .crumb-id {
    font-family: 'Consolas', 'Courier New', monospace;
    color: var(--text-primary, #ccc);
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 180px;
  }
  .crumb-id:hover { color: var(--accent, #4A90D9); }
  .header-actions { display: flex; gap: 2px; }
  .icon-btn {
    width: 26px;
    height: 24px;
    background: none;
    border: none;
    border-radius: 3px;
    color: var(--text-muted, #666);
    font-size: 13px;
    cursor: pointer;
    line-height: 1;
  }
  .icon-btn:hover { color: var(--text-primary, #ccc); }

  /* Error / loading */
  .detail-error, .detail-loading {
    padding: 20px;
    text-align: center;
    font-size: 12px;
  }
  .detail-error { color: var(--color-failed, #E74C3C); }
  .detail-loading { color: var(--text-secondary, #888); }

  /* Action row */
  .action-row {
    display: flex;
    align-items: stretch;
    gap: 6px;
    padding: 10px 12px 0;
    flex-shrink: 0;
  }
  .fav-btn {
    width: 36px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-secondary, #888);
    border-radius: 3px;
    cursor: pointer;
    font-size: 18px;
    font-family: inherit;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .fav-btn.active {
    color: #E9A847;
    border-color: #E9A847;
    background: rgba(233, 168, 71, 0.15);
  }
  .rename-btn, .rename-input, .archive-btn {
    border-radius: 3px;
    font-size: 11px;
    font-family: inherit;
  }
  .rename-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    font-weight: 500;
    cursor: pointer;
  }
  .rename-btn:hover { border-color: var(--accent, #4A90D9); }
  .rename-input {
    flex: 1;
    padding: 0 10px;
    background: var(--bg-input, #1e1e1e);
    border: 1px solid var(--accent, #4A90D9);
    color: var(--text-primary, #ccc);
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    outline: none;
  }
  .archive-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-secondary, #888);
    font-weight: 500;
    cursor: pointer;
  }
  .archive-btn:hover {
    border-color: var(--accent, #4A90D9);
    color: var(--accent, #4A90D9);
    background: rgba(74, 144, 217, 0.10);
  }
  .archive-btn.unarchive {
    color: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
    background: rgba(74, 144, 217, 0.10);
  }
  .archive-btn.unarchive:hover {
    background: rgba(74, 144, 217, 0.18);
  }
  .g { font-size: 12px; }

  .archived-pill {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-secondary, #888);
    padding: 2px 8px;
    border: 1px solid var(--border, #3e3e42);
    background: rgba(62, 62, 66, 0.3);
    border-radius: 3px;
    letter-spacing: 0.3px;
  }

  /* Hero card */
  .hero {
    margin: 12px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    overflow: hidden;
    flex-shrink: 0;
  }
  .hero-accent { height: 3px; }
  .hero-body { padding: 14px 16px 12px; }
  .hero-row-1 {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
  .status-pill {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    padding: 2px 8px;
    border: 1px solid;
    border-radius: 3px;
  }
  .hero-time { font-size: 11px; color: var(--text-secondary, #888); }
  .hero-duration {
    margin-left: auto;
    font-size: 11px;
    color: var(--text-secondary, #888);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .hero-method {
    font-size: 17px;
    font-weight: 600;
    color: var(--text-primary, #ccc);
    letter-spacing: -0.2px;
    font-family: 'Consolas', 'Courier New', monospace;
    word-break: break-word;
  }
  .hero-sub {
    font-size: 12px;
    color: var(--text-secondary, #888);
    margin-top: 2px;
  }
  .hero-module { color: var(--accent, #4A90D9); }
  .hero-source {
    color: var(--color-completed, #50C878);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .tags-row {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 12px;
  }
  .tag {
    font-size: 10px;
    padding: 2px 8px;
    background: rgba(74, 144, 217, 0.10);
    color: var(--accent, #4A90D9);
    border-radius: 3px;
    font-weight: 500;
  }

  /* Tabs */
  .tabs {
    display: flex;
    gap: 4px;
    padding: 0 12px 10px;
    flex-shrink: 0;
  }
  .tab {
    flex: 1;
    padding: 4px 8px 3px;
    background: transparent;
    color: var(--text-primary, #fff);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    align-items: center;
    font-family: inherit;
  }
  /* Tabs without a count (only Overview today) vertically center their
     label so it doesn't sit flush to the top of the column layout. */
  .tab.no-meta {
    justify-content: center;
  }
  .tab.active {
    background: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
    color: #fff;
  }
  .tab-label {
    font-size: 10.5px;
    font-weight: 600;
    line-height: 1.3;
  }
  .tab-divider {
    align-self: stretch;
    height: 1px;
    background: rgba(62, 62, 66, 0.6);
    margin: 3px 0 2px;
  }
  .tab.active .tab-divider { background: rgba(255, 255, 255, 0.18); }
  .tab-count {
    font-size: 9.5px;
    font-family: 'Consolas', 'Courier New', monospace;
    color: var(--text-muted, #666);
    line-height: 1;
  }
  .tab.active .tab-count { color: rgba(255, 255, 255, 0.55); }

  /* Scrollable body */
  .body {
    flex: 1;
    overflow-y: auto;
    padding: 0 12px 12px;
    min-height: 0;
  }

  /* Cards */
  .card {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    overflow: hidden;
  }

  /* Overview */
  .overview { display: flex; flex-direction: column; gap: 8px; }
  .facts-grid {
    display: grid;
    grid-template-columns: auto 1fr;
    row-gap: 6px;
    column-gap: 14px;
    font-size: 11px;
    padding: 10px 12px;
  }
  .fact-label { color: var(--text-muted, #666); }
  .fact-value { color: var(--text-primary, #ccc); }
  .fact-mono {
    color: var(--text-primary, #ccc);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .fact-mono.strong { font-weight: 600; }
  .fact-link {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent, #4A90D9);
    font-family: 'Consolas', 'Courier New', monospace;
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: rgba(74, 144, 217, 0.3);
    text-align: left;
  }
  /* Per-slot parent chips — rendered when a run has fan-in (multiple
     upstreams). Each chip is clickable and jumps to the source run. */
  .parent-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .parent-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #252528;
    border: 1px solid #3a3a42;
    border-radius: 3px;
    padding: 3px 8px;
    color: #cfcfd4;
    text-decoration: none;
    font-family: inherit;
    font-size: 12px;
    cursor: pointer;
    width: fit-content;
  }
  .parent-chip:hover {
    border-color: var(--accent, #4A90D9);
    color: #fff;
  }
  /* "Cached from" chip — visually distinct from parent chips (amber tint)
     so at a glance the user can tell a run reused outputs rather than
     executing fresh. The ♻ glyph doubles the signal. */
  .cache-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(233, 168, 71, 0.10);
    border: 1px solid rgba(233, 168, 71, 0.45);
    border-radius: 3px;
    padding: 2px 8px;
    color: #e9a847;
    text-decoration: none;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    cursor: pointer;
    width: fit-content;
  }
  .cache-chip:hover {
    background: rgba(233, 168, 71, 0.18);
    color: #fff;
  }
  .parent-slot {
    color: #ddd;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .parent-arrow {
    color: #666;
  }
  .parent-run {
    color: var(--accent, #4A90D9);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .highlights {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .highlight { padding: 10px 12px; }
  .hl-label {
    font-size: 9.5px;
    color: var(--text-muted, #666);
    letter-spacing: 0.6px;
    text-transform: uppercase;
  }
  .hl-value {
    font-size: 17px;
    font-weight: 600;
    color: var(--accent, #4A90D9);
    font-family: 'Consolas', 'Courier New', monospace;
    margin-top: 3px;
    word-break: break-all;
  }

  /* Flat key/value list (params / metrics) */
  .flat-list { overflow: hidden; }
  .flat-row {
    display: flex;
    align-items: baseline;
    padding: 5px 12px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
    font-size: 11px;
  }
  .flat-row:last-child { border-bottom: none; }
  .flat-key {
    color: var(--text-secondary, #888);
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    width: 45%;
    flex-shrink: 0;
  }
  .flat-val {
    color: var(--text-primary, #ccc);
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    flex: 1;
    word-break: break-all;
  }
  .metric-val { color: var(--accent, #4A90D9); font-weight: 600; }

  .empty {
    color: var(--text-muted, #666);
    font-size: 11px;
    text-align: center;
    padding: 14px;
  }

  /* Cancel banner */
  .cancel-banner {
    background: rgba(127, 142, 163, 0.10);
    border: 1px solid rgba(127, 142, 163, 0.35);
    border-radius: 3px;
    padding: 8px 10px;
    font-size: 11px;
    color: #c6d0de;
    margin-bottom: 6px;
  }

  /* Err block */
  .err-block {
    background: rgba(231, 76, 60, 0.08);
    border: 1px solid rgba(231, 76, 60, 0.30);
    border-radius: 3px;
    padding: 8px 10px;
  }
  .err-msg { color: var(--color-failed, #E74C3C); font-size: 11px; font-weight: 600; }
  .err-trace {
    margin: 6px 0 0;
    color: #c88;
    font-size: 10px;
    white-space: pre-wrap;
    line-height: 1.4;
    font-family: 'Consolas', 'Courier New', monospace;
  }

  .cascade-block {
    margin-top: 6px;
    background: rgba(231, 76, 60, 0.05);
    border: 1px solid rgba(231, 76, 60, 0.20);
    border-radius: 3px;
    padding: 8px 10px;
  }
  .cascade-header {
    font-size: 11px;
    font-weight: 600;
    color: #c6d0de;
    margin-bottom: 6px;
  }
  .cascade-list { display: flex; flex-direction: column; gap: 2px; }
  .cascade-row {
    display: flex;
    align-items: baseline;
    gap: 6px;
    padding: 4px 6px;
    background: none;
    border: none;
    border-radius: 3px;
    cursor: pointer;
    text-align: left;
    font-size: 11px;
    color: #c6d0de;
  }
  .cascade-row:hover { background: rgba(255, 255, 255, 0.04); }
  .cascade-method { font-weight: 600; }
  .cascade-sample { color: #8a98ac; }
  .cascade-id {
    margin-left: auto;
    color: var(--accent, #4A90D9);
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10px;
  }

  /* Output / log-stream pane */
  .output-block { display: flex; flex-direction: column; gap: 8px; }
  .output-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 4px;
  }
  .output-status {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 3px 7px;
    border-radius: 999px;
    border: 1px solid var(--border, #444);
    color: var(--text-secondary, #ccc);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .output-status-streaming { color: var(--accent, #4A90D9); border-color: color-mix(in oklab, var(--accent, #4A90D9) 40%, transparent); }
  .output-status-succeeded { color: var(--color-completed, #50C878); border-color: color-mix(in oklab, var(--color-completed, #50C878) 40%, transparent); }
  .output-status-failed    { color: var(--color-failed, #E74C3C); border-color: color-mix(in oklab, var(--color-failed, #E74C3C) 40%, transparent); }
  .output-status-cancelled { color: #d8c08a; border-color: color-mix(in oklab, #d8c08a 40%, transparent); }
  .output-hint {
    font-size: 10px;
    color: var(--text-muted, #888);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .log-pane {
    margin: 0;
    max-height: 380px;
    overflow: auto;
    padding: 8px 10px;
    background: rgba(0, 0, 0, 0.25);
    border: 1px solid var(--border, #333);
    border-radius: 3px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .log-line { display: block; }
  .log-stdout { color: var(--text-primary, #ddd); }
  .log-stderr { color: var(--color-failed, #E74C3C); }

  /* Artifacts */
  .artifacts { display: flex; flex-direction: column; gap: 8px; }
  /* Inline image preview (US-2): light card so the always-light PNG does
     not float on a dark panel; click opens the lightbox. */
  .thumb-card {
    display: block;
    margin: 4px 8px 8px 24px;
    padding: 6px;
    background: #fcfcfb;
    border: 1px solid var(--border, #e1e0d9);
    border-radius: 6px;
    cursor: zoom-in;
    max-width: 260px;
  }
  .thumb-img { display: block; max-width: 100%; height: auto; }
  .lightbox-overlay {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: rgba(0, 0, 0, 0.72);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: zoom-out;
  }
  .lightbox-img {
    max-width: 92vw;
    max-height: 92vh;
    background: #fcfcfb;
    padding: 8px;
    border-radius: 6px;
    cursor: default;
  }
  .artifacts-summary {
    padding: 0 4px;
    font-size: 10px;
    color: var(--text-muted, #666);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .cat-card { overflow: hidden; }
  .cat-header {
    padding: 8px 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--bg-header, #2d2d30);
    border-bottom: 1px solid var(--border, #3e3e42);
  }
  .cat-icon {
    width: 18px;
    height: 18px;
    border-radius: 2px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
  }
  .cat-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-primary, #ccc);
  }
  .cat-count {
    font-size: 10px;
    color: var(--text-muted, #666);
    font-family: 'Consolas', 'Courier New', monospace;
    margin-left: auto;
  }

  .file-row, .dir-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    text-decoration: none;
    color: var(--text-primary, #ccc);
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
    background: none;
    border-left: none;
    border-right: none;
    border-top: none;
    width: 100%;
    font-family: inherit;
    cursor: pointer;
  }
  .file-row:last-child, .dir-row:last-child .dir-head { border-bottom: none; }
  .file-row:hover, .dir-head:hover { background: var(--bg-input, #1e1e1e); }
  .file-bullet { color: var(--text-muted, #666); font-size: 10px; width: 10px; }
  .file-name {
    flex: 1;
    font-size: 11px;
    font-family: 'Consolas', 'Courier New', monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: left;
  }
  .file-ext {
    font-size: 9px;
    padding: 1px 5px;
    border: 1px solid;
    border-radius: 2px;
    text-transform: uppercase;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .file-size {
    color: var(--text-muted, #666);
    font-size: 10px;
    min-width: 54px;
    text-align: right;
    font-family: 'Consolas', 'Courier New', monospace;
  }

  .dir-row { border-bottom: 1px solid rgba(62, 62, 66, 0.5); }
  .dir-row:last-child { border-bottom: none; }
  .dir-caret { color: var(--text-muted, #666); font-size: 9px; width: 10px; }
  .dir-name {
    flex: 1;
    font-size: 11px;
    font-family: 'Consolas', 'Courier New', monospace;
    color: var(--accent, #4A90D9);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: left;
  }
  .dir-pill {
    font-size: 9px;
    padding: 1px 5px;
    border: 1px solid;
    border-radius: 2px;
    text-transform: uppercase;
    font-family: 'Consolas', 'Courier New', monospace;
    letter-spacing: 0.3px;
  }
  .dir-size {
    color: var(--text-muted, #666);
    font-size: 10px;
    min-width: 54px;
    text-align: right;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .dir-children {
    background: var(--bg-input, #1e1e1e);
    border-top: 1px solid rgba(62, 62, 66, 0.5);
    padding: 4px 0;
  }
  .child-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 12px 3px 36px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    color: var(--text-secondary, #888);
  }
  .child-dash { color: var(--text-muted, #666); font-size: 9px; }
  .child-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .child-size {
    color: var(--text-muted, #666);
    font-size: 9.5px;
    min-width: 48px;
    text-align: right;
  }
  a.child-row-link {
    text-decoration: none;
    cursor: pointer;
  }
  a.child-row-link:hover {
    background: var(--bg-hover, rgba(255, 255, 255, 0.04));
    color: var(--text-primary, #ddd);
  }
  a.child-row-link:hover .child-name {
    text-decoration: underline;
  }

  /* Footer */
  .footer {
    padding: 10px;
    display: flex;
    gap: 6px;
    border-top: 1px solid var(--border, #3e3e42);
    background: var(--bg-header, #2d2d30);
    flex-shrink: 0;
  }
  .footer-btn {
    flex: 1;
    padding: 7px 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    border-radius: 3px;
    font-size: 11px;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
  }
  .footer-btn:hover:not(:disabled) { border-color: var(--accent, #4A90D9); }
  .footer-btn:disabled { color: var(--text-muted, #666); cursor: not-allowed; }
  .footer-btn.primary {
    background: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
    color: #fff;
    font-weight: 600;
  }
  .footer-btn.danger {
    border-color: var(--color-failed, #E74C3C);
    color: var(--color-failed, #E74C3C);
    background: rgba(231, 76, 60, 0.08);
  }

  /* Confirm dialog */
  .confirm-overlay {
    position: absolute;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 10;
  }
  .confirm-card {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    padding: 14px 16px;
    width: 100%;
  }
  .confirm-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary, #ccc);
    margin-bottom: 8px;
  }
  .confirm-body {
    font-size: 11px;
    color: var(--text-secondary, #888);
    margin-bottom: 12px;
    line-height: 1.4;
  }
  .confirm-id {
    font-family: 'Consolas', 'Courier New', monospace;
    color: var(--text-primary, #ccc);
  }
  .confirm-actions {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
  }
  .confirm-actions .footer-btn { flex: 0 0 auto; min-width: 80px; }

  /* Toast */
  .toast {
    position: absolute;
    bottom: 70px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--bg-input, #1e1e1e);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    padding: 6px 12px;
    border-radius: 3px;
    font-size: 11px;
    z-index: 11;
    pointer-events: none;
  }
</style>
