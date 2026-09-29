<script lang="ts">
  /**
   * The History tab's run detail panel: loads a run, renders its hero, tabs
   * and body, and owns the log stream.
   *
   * The run actions, the canvas actions and the artifact list are children;
   * the formatting helpers live in runDetailFormat.ts.
   */
  import { selectRun, jumpToPipelineRun } from './historyStore.js';
  import {
    fetchRun,
    fetchCancelledDescendants,
    listArtifacts,
    openRunLogStream,
  } from './historyApi.js';
  import type { WfcRun, Artifact } from './historyApi.js';
  import { COLLAPSED_SAMPLE } from '../shared/types.js';
  import {
    formatTimestamp,
    formatDuration,
    formatRelativeTime,
    statusColor,
  } from './historyUtils.js';
  import {
    formatMetric,
    formatParamValue,
    formatSampleReads,
    pickHighlights,
  } from './runDetailFormat.js';
  import RunActions from './RunActions.svelte';
  import CanvasActions from './CanvasActions.svelte';
  import ArtifactList from './ArtifactList.svelte';

  interface Props {
    runId: string;
  }

  let { runId }: Props = $props();

  type Section = 'overview' | 'params' | 'metrics' | 'artifacts' | 'output';

  let run = $state<WfcRun | null>(null);
  let artifacts = $state<Artifact[]>([]);
  let artifactState = $state<'idle' | 'loading' | 'done'>('idle');
  // The Files list's own error (e.g. a run whose outputs cannot be read),
  // shown in place of the list rather than as an empty list.
  let artifactError = $state<string | null>(null);
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

  let isArchived = $derived(!!run?.archivedAt);

  $effect(() => {
    if (runId) {
      loadError = null;
      run = null;
      artifacts = [];
      artifactState = 'idle';
      artifactError = null;
      activeSection = 'overview';
      // The action row, its dialogs and the artifact list are children that
      // unmount while `run` is null, so their local state (renaming, the
      // confirms, the expanded directories, the lightbox) resets with them.
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
    const es = openRunLogStream(runId, logFullMode);
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
          artifactError = err instanceof Error ? err.message : String(err);
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
    <!-- Header strip, action row, confirm dialogs and toast -->
    <RunActions
      bind:run
      {artifacts}
      onError={(msg) => { loadError = msg; }}
    />

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
          {#if run.dataSource === COLLAPSED_SAMPLE && run.bundledSamples && run.bundledSamples.length > 0}
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
              {#if formatSampleReads(run.sampleInputs).length > 0}
                {@const readLines = formatSampleReads(run.sampleInputs)}
                <span class="fact-label">Sample{readLines.length === 1 ? ' read' : ' reads'}</span>
                <div class="sample-read-list" data-testid="sample-reads">
                  {#each readLines as line, i (i)}
                    <span class="sample-read" data-testid="sample-read">{line}</span>
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
        {#if artifactError}
          <div class="detail-error">{artifactError}</div>
        {:else}
          <ArtifactList runId={run.id} {artifacts} {artifactState} />
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

    <CanvasActions {run} />
  {/if}
</div>

<style>
  .detail-panel {
    /* Remap the grey text tokens for this panel only: every `color:
       var(--text-secondary|--text-muted)` inside .detail-panel resolves
       to the primary color so captions, counts, sizes, and dim labels
       stay readable. Borders/dividers use `--border` and are unaffected.
       The children inherit these, as they are DOM descendants. */
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

  /* Header strip — the error and loading states' bare header; the loaded
     panel's header lives in RunActions. */
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
  /* Recorded sample reads — one plain line per read, listed under the
     parent chips. Not clickable: a sample read has no source run. */
  .sample-read-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .sample-read {
    color: #cfcfd4;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
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

  /* The Output tab's "Load full log" button; the panel footer's copies of
     these rules live in CanvasActions. */
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
</style>
