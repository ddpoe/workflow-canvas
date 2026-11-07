<script lang="ts">
  /**
   * The History tab's run detail panel: loads a run, renders its hero, tabs
   * and body, and owns the log stream.
   *
   * The run actions, the canvas actions and the artifact list are children;
   * the formatting helpers live in runDetailFormat.ts.
   */
  import { selectRun, jumpToPipelineRun, takePendingSection, runSectionRequest } from './historyStore.js';
  import type { RunDetailSection } from './historyStore.js';
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
    runPillKind,
    PILL_LABEL,
    cancelReasonText,
  } from './historyUtils.js';
  import {
    changedOutputs,
    formatMetric,
    formatParamValue,
    formatSampleReads,
    pickHighlights,
  } from './runDetailFormat.js';
  import CacheKeyInputsCard from './CacheKeyInputsCard.svelte';
  import RunActions from './RunActions.svelte';
  import CanvasActions from './CanvasActions.svelte';
  import ArtifactList from './ArtifactList.svelte';
  import LogPane from '../shared/LogPane.svelte';
  import { withEndingTraceback } from '../shared/errorSummary.js';
  import ErrorBox from '../shared/ErrorBox.svelte';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import {
    RUN_DETAIL_DEFAULT,
    RUN_DETAIL_MAX,
    RUN_DETAIL_MIN,
    RUN_DETAIL_STEP,
    runDetailWidth,
  } from './runDetailWidth.js';

  /** The hero's border: coloured only for an exception (History's rule). */
  function heroBorder(status: string): string {
    switch (status) {
      case 'failed': return 'var(--status-failed)';
      case 'cancelled': return 'var(--status-cancelled)';
      case 'running':
      case 'queued':
      case 'pending': return 'var(--status-active)';
      default: return 'var(--border)';
    }
  }

  interface Props {
    runId: string;
  }

  let { runId }: Props = $props();

  // ---- The left edge: dragging or arrow keys set the panel's width ----

  let resizing = $state(false);
  /** Where the drag started: the pointer's x and the width then. */
  let grab = { x: 0, width: 0 };

  function onEdgeDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    e.preventDefault();
    resizing = true;
    grab = { x: e.clientX, width: $runDetailWidth };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  }

  function onEdgeMove(e: PointerEvent): void {
    if (!resizing) return;
    // The panel's right side stays put: moving the edge left widens it by as much.
    runDetailWidth.set(grab.width + (grab.x - e.clientX));
  }

  function onEdgeUp(e: PointerEvent): void {
    resizing = false;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
  }

  /** Left widens (the edge moves left), right narrows; the store keeps the bounds. */
  function onEdgeKey(e: KeyboardEvent): void {
    const step = e.key === 'ArrowLeft' ? RUN_DETAIL_STEP : e.key === 'ArrowRight' ? -RUN_DETAIL_STEP : 0;
    if (!step) return;
    e.preventDefault();
    runDetailWidth.update(w => w + step);
  }

  type Section = RunDetailSection;

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
  // Same phase names as the Inspector's Output tab: both read the same SSE
  // endpoint.
  type LogPhase = 'idle' | 'connecting' | 'streaming' | 'succeeded' | 'failed' | 'cancelled';
  let logLines = $state<LogLine[]>([]);
  let logPhase = $state<LogPhase>('idle');
  // The stream dropped before its terminal frame.
  let logConnectionLost = $state(false);
  // The terminal frame's traceback: shown after the log when the log does
  // not already hold its last line (see shownLines).
  let logTraceback = $state<string | null>(null);
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
      // Overview, unless the run was opened on another tab (a link to a
      // run's output opens it on Artifacts).
      activeSection = takePendingSection(runId);
      // The action row, its dialogs and the artifact list are children that
      // unmount while `run` is null, so their local state (renaming, the
      // confirms, the expanded directories, the lightbox) resets with them.
      // Reset log-stream state so the Output tab re-connects for the new run.
      logLines = [];
      logPhase = 'idle';
      logConnectionLost = false;
      logTraceback = null;
      logFullMode = false;
      cancelledDescendants = null;
      fetchRun(runId)
        .then(r => { run = r; })
        .catch(err => { loadError = err instanceof Error ? err.message : String(err); });
    }
  });

  $effect(() => {
    // Another view asked an open run for a tab (✗ Failed on its card): the
    // runId effect above does not re-run for the same run, so apply it here.
    // Declared after the runId effect so a freshly opened run takes its
    // pending tab there first.
    const req = $runSectionRequest;
    if (!req || req.runId !== runId) return;
    activeSection = req.section;
    takePendingSection(runId);
    runSectionRequest.set(null);
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
    logConnectionLost = false;
    logTraceback = null;
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
          logTraceback = p.error_traceback ?? null;
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
        logConnectionLost = true;
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

  /** The run is still going: its log follows the newest line. A finished
   *  run's log replays fast over the same stream but opens at its top. */
  let runIsLive = $derived(
    run?.status === 'running' || run?.status === 'queued' || run?.status === 'pending',
  );

  /** Live until the stream's ending lands: `run` is fetched once, so a run
   *  opened while running still reads running after it ends. */
  let logIsLive = $derived(
    runIsLive && logPhase !== 'succeeded' && logPhase !== 'failed' && logPhase !== 'cancelled',
  );

  /** The log bar's note: the stream's own state, never the run's error. */
  let logNote = $derived(
    logPhase === 'connecting' ? 'Connecting…'
      : logPhase === 'streaming' ? 'Streaming…'
        : logConnectionLost ? 'Connection lost'
          : null,
  );

  /** The log as shown: what the run printed, then the traceback its ending
   *  carried when the log does not already hold that ending's last line (a
   *  launch failure's, or wfc's own one-line ending after the method ran). */
  let shownLines = $derived(withEndingTraceback(logLines, logTraceback));

  function displayName(r: WfcRun): string {
    // Prefer a user-set nid (overrides the auto-version vN label).
    // Fall back to name (legacy field) → runName → method.
    if (r.nid && !/^v\d+$/.test(r.nid)) return r.nid;
    return r.name || r.runName || r.method;
  }
</script>

<div class="detail-panel" class:resizing style="width: {$runDetailWidth}px;">
  <!-- A focusable separator is a widget (WAI-ARIA window splitter); svelte's check does not know it. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
  <div class="edge" role="separator" tabindex="0" aria-orientation="vertical"
       aria-label="Run panel width" aria-valuenow={$runDetailWidth}
       aria-valuemin={RUN_DETAIL_MIN} aria-valuemax={RUN_DETAIL_MAX} data-testid="run-detail-edge"
       title="Drag to change the panel's width; double-click to reset"
       onpointerdown={onEdgeDown} onpointermove={onEdgeMove}
       onpointerup={onEdgeUp} onpointercancel={onEdgeUp} onkeydown={onEdgeKey}
       ondblclick={() => runDetailWidth.set(RUN_DETAIL_DEFAULT)}></div>
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
    <!-- The run detail hero (style guide): a 3px module bar, the status
         pill, a neutral border unless the status is an exception. -->
    <div class="hero" data-testid="run-hero"
         style="--module: {moduleColor(run.module)}; border-color: {heroBorder(run.status)};">
      <div class="hero-accent" style="background: {moduleColor(run.module)};"></div>
      <div class="hero-body">
        <div class="hero-row-1">
          <span class="hist-pill {runPillKind(run)}" data-testid="run-hero-pill">{PILL_LABEL[runPillKind(run)]}</span>
          {#if isArchived}
            <span class="archived-pill" title="Archived — hidden from default list">🗄 Archived</span>
          {/if}
          <span class="hero-time">{formatRelativeTime(run.timestamp)}</span>
          <span class="hero-duration">{formatDuration(run.duration)}</span>
        </div>

        <div class="hero-method">{displayName(run)}</div>
        <div class="hero-sub">
          in <span class="hero-module">{run.module}</span>
          {#if run.version != null}
            · <span class="hero-version" data-testid="run-version">v{run.version}</span>
          {/if}
          {#if run.dataSource === COLLAPSED_SAMPLE && run.bundledSamples && run.bundledSamples.length > 0}
            · on <span class="hero-source" title={run.bundledSamples.join(', ')}>
              {run.bundledSamples.length} samples ({run.bundledSamples.join(', ')})
            </span>
          {:else if run.dataSource}
            · on <span class="hero-source">{run.dataSource}</span>
          {/if}
        </div>

        {#if run.status === 'failed'}
          <!-- The failure lives in the hero, which every tab shows, in the
               side-panel error box laid out like the Inspector's; the
               traceback is on the Output tab, never repeated here. -->
          <div class="hero-err">
            <ErrorBox testid="run-hero-error" message={errorMessage}
                      onSeeOutput={() => { activeSection = 'output'; }} />
          </div>
        {/if}

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
          <span class="tab-label">{tab.label}{#if tab.key === 'output' && run.status === 'failed'}<span class="tab-x" aria-hidden="true">✗</span><span class="sr-only"> (failed)</span>{/if}</span>
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
              {#if run.version != null}
                <span class="fact-label">Method version</span>
                <span class="fact-mono">v{run.version}</span>
              {/if}
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

          {#if run.status === 'cancelled'}
            {@const why = cancelReasonText(run)}
            <!-- The step and sample of a failed run come before its id;
                 the id is never shown on its own. -->
            <div class="cancel-banner" data-testid="cancel-reason">
              {why.text}{#if why.cause}{#if why.cause.method}{': '}<code>{why.cause.method}</code>{#if why.cause.sample}{' on '}<code>{why.cause.sample}</code>{/if}{/if}
                <button
                  type="button"
                  class="fact-link"
                  onclick={() => why.cause && selectRun(why.cause.runId)}
                >r_{why.cause.runId}</button>{/if}
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

          <!-- Cache-key inputs: always the last block of Overview. -->
          <CacheKeyInputsCard {run} />
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
          <ArtifactList
            runId={run.id}
            {artifacts}
            {artifactState}
            changedOutputs={changedOutputs(run.outputAvailability)}
          />
        {/if}

      {:else if activeSection === 'output'}
        {#if shownLines.length === 0 && logPhase !== 'connecting' && logNote === null}
          <div class="empty">No output captured.</div>
        {:else}
          {#key runId}<LogPane lines={shownLines} live={logIsLive} note={logNote}
                   noteTone={logNote === 'Connection lost' ? 'failed' : 'muted'}>
            {#snippet actions()}
              {#if !logFullMode && (logPhase === 'succeeded' || logPhase === 'failed' || logPhase === 'cancelled')}
                <button type="button" class="log-bar-btn" onclick={loadFullLog}>Load full log</button>
              {:else if logFullMode}
                <span class="output-hint">full log</span>
              {/if}
            {/snippet}
          </LogPane>{/key}
        {/if}
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
    --text-secondary: var(--text-primary);
    --text-muted: var(--text-primary);

    /* The width is runDetailWidth's (360-720 px), set inline. */
    background: var(--bg-panel);
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
    font-family: 'Mukta Vaani', 'Segoe UI', sans-serif;
    font-size: 12px;
    color: var(--text-primary);
    position: relative;
  }
  .detail-panel.resizing { cursor: col-resize; user-select: none; }

  /* The left edge's grab strip: a thin accent line on hover, focus or drag. */
  .edge {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    width: 6px;
    cursor: col-resize;
    touch-action: none;
    z-index: 5;
  }
  .edge::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    width: 2px;
    background: transparent;
    transition: background 0.12s;
  }
  .edge:hover::after, .edge:focus-visible::after, .resizing .edge::after { background: var(--accent); }
  .edge:focus-visible { outline: none; }

  /* Header strip — the error and loading states' bare header; the loaded
     panel's header lives in RunActions. */
  .detail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .crumb {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--text-secondary);
    min-width: 0;
  }
  .icon-btn {
    width: 26px;
    height: 24px;
    background: none;
    border: none;
    border-radius: 3px;
    color: var(--text-muted);
    font-size: 13px;
    cursor: pointer;
    line-height: 1;
  }
  .icon-btn:hover { color: var(--text-primary); }

  /* Error / loading */
  .detail-error, .detail-loading {
    padding: 20px;
    text-align: center;
    font-size: 12px;
  }
  .detail-error { color: var(--status-failed-ink); }
  .detail-loading { color: var(--text-secondary); }

  .archived-pill {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-secondary);
    padding: 2px 8px;
    border: 1px solid var(--border);
    background: rgba(62, 62, 66, 0.3);
    border-radius: 3px;
    letter-spacing: 0.3px;
  }

  /* Hero card */
  /* The hero's body carries a 10% tint of its module colour, as History's
     cards do (SOP Run detail hero). */
  .hero {
    position: relative;
    margin: 12px;
    background: color-mix(in srgb, var(--module) 10%, var(--bg-panel));
    border: 2px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
    flex-shrink: 0;
  }
  .hero-accent { height: 3px; border-radius: 4px 4px 0 0; }
  .hero-body { padding: 10px 14px 12px; }
  .hero-row-1 {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
  .hero-time { font-size: 11px; color: var(--text-secondary); }
  .hero-duration {
    margin-left: auto;
    font-size: 11px;
    color: var(--text-secondary);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .hero-method {
    font-size: 17px;
    font-weight: 600;
    color: var(--text-primary);
    letter-spacing: -0.2px;
    font-family: 'Consolas', 'Courier New', monospace;
    word-break: break-word;
  }
  .hero-sub {
    font-size: 12px;
    color: var(--text-secondary);
    margin-top: 2px;
  }
  .hero-module { color: var(--module-ink); font-weight: 600; }
  .hero-source {
    color: #d0d0d0;
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
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    color: var(--accent);
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
    color: var(--text-primary);
    border: 1px solid var(--border);
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
    background: var(--accent);
    border-color: var(--accent);
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
    color: var(--text-muted);
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
    background: var(--bg-header);
    border: 1px solid var(--border);
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
  .fact-label { color: var(--text-muted); }
  .fact-value { color: var(--text-primary); }
  .fact-mono {
    color: var(--text-primary);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .fact-mono.strong { font-weight: 600; }
  .fact-link {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent);
    font-family: 'Consolas', 'Courier New', monospace;
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--accent) 30%, transparent);
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
    border-color: var(--accent);
    color: #fff;
  }
  /* "Cached from" chip — visually distinct from parent chips (amber tint)
     so at a glance the user can tell a run reused outputs rather than
     executing fresh. The ♻ glyph doubles the signal. */
  .cache-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: color-mix(in srgb, var(--marker-light) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--marker-light) 45%, transparent);
    border-radius: 3px;
    padding: 2px 8px;
    color: var(--marker-light);
    text-decoration: none;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    cursor: pointer;
    width: fit-content;
  }
  .cache-chip:hover {
    background: color-mix(in srgb, var(--marker-light) 16%, transparent);
    color: #fff;
  }
  .parent-slot {
    color: #ddd;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .parent-arrow {
    color: var(--text-muted);
  }
  .parent-run {
    color: var(--accent);
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
    color: var(--text-muted);
    letter-spacing: 0.6px;
    text-transform: uppercase;
  }
  .hl-value {
    font-size: 17px;
    font-weight: 600;
    color: var(--accent);
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
    color: var(--text-secondary);
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    width: 45%;
    flex-shrink: 0;
  }
  .flat-val {
    color: var(--text-primary);
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    flex: 1;
    word-break: break-all;
  }
  .metric-val { color: var(--accent); font-weight: 600; }

  .empty {
    color: var(--text-muted);
    font-size: 11px;
    text-align: center;
    padding: 14px;
  }

  /* Cancel banner */
  .cancel-banner {
    background: color-mix(in srgb, var(--status-cancelled) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--status-cancelled) 35%, transparent);
    border-radius: 3px;
    padding: 8px 10px;
    font-size: 11px;
    color: #c6d0de;
    margin-bottom: 6px;
  }

  .cascade-block {
    margin-top: 6px;
    background: color-mix(in srgb, var(--status-failed) 5%, transparent);
    border: 1px solid color-mix(in srgb, var(--status-failed) 20%, transparent);
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
    color: var(--accent);
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10px;
  }

  /* Output tab: the log box is the shared LogPane; these are the controls
     this panel puts in its bar. */
  .output-hint {
    font-size: 10px;
    color: var(--text-muted);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .log-bar-btn {
    font: inherit;
    font-size: 10.5px;
    line-height: 17px;
    padding: 0 8px;
    color: var(--text-primary);
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
  }
  .log-bar-btn:hover { border-color: var(--accent); }

  /* The hero's failure box (shared ErrorBox) under the "in <module>" line. */
  .hero-err { margin-top: 10px; }
  .tab-x { color: var(--status-failed-ink); margin-left: 3px; }
  .tab.active .tab-x { color: #fff; }
</style>
