<script lang="ts">
  /**
   * The Inspector's method-node body: the failure summary, the Config and
   * Output tabs, the parameter rows (all-samples and per-sample), and the
   * last run's log stream. The header above it is the Inspector's.
   *
   * The non-markup logic — the override and variant writes, the constraint
   * hint and the `options_from` lookup — lives in `./nodeEdits.ts`. The
   * `$derived` and `$effect` that drive the lookup live here, where their
   * dependencies are read.
   */
  import type { Node } from '@xyflow/svelte';
  import {
    nodes, edges, pipelineVariables, updateNodeData,
  } from '../builder/stores.js';
  import type { CanvasNodeData } from '../shared/types.js';
  import type { ParamOptions } from './options.js';
  import ValueList from './ValueList.svelte';
  import LogPane from '../shared/LogPane.svelte';
  import { withEndingTraceback } from '../shared/errorSummary.js';
  import ErrorBox from '../shared/ErrorBox.svelte';
  import { pipelineRunActor, paramEditorAggregator,
           awaitAllCommitted, nodeHasDirtyEditors } from '../machines/root.js';
  import { openLockSummary } from '../builder/lockFlow.js';
  import { onDestroy, tick, untrack } from 'svelte';
  import { inspectParamRequest } from '../shared/uiState.js';
  import { openNodeHistoricalLogStream } from './api.js';
  import { RenderTick } from './renderTick.svelte.js';
  import {
    clearSampleOverrideAndVariants, constraintHint, lookupPipeline,
    optionsLookupKey, optionsLookupPlan, resolveOptionsLookup,
    updateSampleOverride, updateSampleVariants, updateVariants,
  } from './nodeEdits.js';

  let { node, data }: {
    node: Node<CanvasNodeData> | null;
    data: CanvasNodeData;
  } = $props();

  // Re-render ticks. Each tracks a change source runes don't see on their
  // own, and derivations read `<tick>.count` to register the dependency.
  // `renderTick.svelte.ts` owns the counter and the teardown, so the
  // subscribe-and-count pattern is written once for the whole Inspector.
  //
  //   - `actorTick` — the pipelineRunActor, so we re-render when a child's
  //     snapshot changes. We don't read context.nodeRefs[id] reactively;
  //     instead, on each parent snapshot tick, we resolve the child for the
  //     currently-selected node and read its snapshot.
  //   - `aggregatorTick` — the param-editor aggregator, so per-node dirty
  //     state derivations refresh when any child registers/unregisters or
  //     transitions between editing/settled.
  //   - `nodesTick` — the canvas `nodes` store, whose deep field changes
  //     `updateNodeData` makes in place.
  const actorTick = new RenderTick(pipelineRunActor);
  const aggregatorTick = new RenderTick(paramEditorAggregator);
  const nodesTick = new RenderTick(nodes);
  onDestroy(() => { actorTick.dispose(); aggregatorTick.dispose(); nodesTick.dispose(); });

  let activeTab = $state<'config' | 'output'>('config');
  let paramSubTab = $state<'all' | 'per-sample'>('all');
  let selectedOverrideSample = $state<string>('');

  // Landing from a Built-from `param ↗` link: when the request names this
  // node, show Config / All Samples, scroll the parameter's row into view
  // and highlight it for about 1.2 s, then reset the request.
  let rootEl: HTMLDivElement | undefined = $state();
  let landedParam = $state<string | null>(null);
  let landTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const req = $inspectParamRequest;
    const id = node?.id;
    if (!req || req.nodeId !== id) return;
    inspectParamRequest.set(null);
    untrack(() => {
      activeTab = 'config';
      paramSubTab = 'all';
      landedParam = req.param;
      if (landTimer) clearTimeout(landTimer);
      landTimer = setTimeout(() => { landedParam = null; landTimer = null; }, 1200);
      tick().then(() => {
        const row = rootEl?.querySelector(`.param-block[data-param="${CSS.escape(req.param)}"]`);
        row?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      });
    });
  });
  onDestroy(() => { if (landTimer) clearTimeout(landTimer); });

  // Builder Output tab state — last-run-only scope. The run_id
  // comes from the current pipeline's node_states (populated on each poll);
  // clicking Run again overwrites it. No session accumulation, no
  // per-node multi-run dropdown.
  //
  // The streaming child of the spawned nodeRunActor is the source of
  // truth for `logPhase` / `logLines` / terminal payload. We mirror its
  // snapshot into local $state on each tick so the UI keeps the captured
  // lines / terminal frame even after the parent leaves `running` and
  // tears down the streaming child. The live stream has no local
  // EventSource — the streaming machine wraps `subscribeSSE` inside the
  // actor tree.
  type LogLine = { kind: 'stdout' | 'stderr'; line: string };
  type LogPhase = 'idle' | 'connecting' | 'streaming' | 'succeeded' | 'failed' | 'cancelled';
  let logLines = $state<LogLine[]>([]);
  let logPhase = $state<LogPhase>('idle');
  // The stream dropped before its terminal frame. The live streaming
  // machine reports that by its own terminal error text, "Connection lost"
  // (streaming.machine.ts); the historical fetch below sets it directly.
  let logConnectionLost = $state(false);
  // The terminal frame's traceback: shown after the log when the log does
  // not already hold its last line (see shownLines).
  let logTraceback = $state<string | null>(null);
  // Tracks which runId we've already loaded historical logs for, so the
  // post-run fallback only fetches once per (node, runId) — not on every
  // parent-actor tick after the node entered a terminal state.
  let historicalLoadedForRunId = $state<string | null>(null);

  // Snapshot of the spawned `nodeRunActor` for the currently-selected
  // node. Recomputes whenever the parent ticks (selection change OR
  // child state change). Null for nodes not yet spawned (e.g. before
  // first Run). This is the actor-tree readout.
  let nodeActorSnap = $derived.by(() => {
    void actorTick.count;
    if (!node) return null;
    const refs = pipelineRunActor.getSnapshot().context.nodeRefs;
    const child = refs[node.id];
    return child ? child.getSnapshot() : null;
  });

  // Resolves to the most recent Run row for the currently-selected method
  // node in the current pipeline execution, or null if none exists yet
  // (pending / cache-hit-only / system node).
  let lastRunId = $derived.by(() => {
    void actorTick.count;
    return nodeActorSnap?.context.runId ?? null;
  });

  /**
   * Whether the selected node's run failed (or partly failed), read from
   * the spawned `nodeRunActor`. Only its `failed` and
   * `completed_with_failures` states count, so an earlier attempt's error
   * never shadows a node that is running again.
   */
  let nodeFailed = $derived.by(() => {
    void actorTick.count;
    const v = nodeActorSnap?.value;
    if (!v) return false;
    const stateKey = typeof v === 'string' ? v : Object.keys(v)[0];
    return stateKey === 'failed' || stateKey === 'completed_with_failures';
  });

  /** The failed run's message, when it has one. */
  let nodeError = $derived(nodeFailed ? (nodeActorSnap?.context.error_message ?? null) : null);

  /** The node's run is still going: its log follows the newest line. A
   *  finished run's log replays fast but opens at its top. */
  let nodeIsRunning = $derived.by(() => {
    const v = nodeActorSnap?.value;
    if (!v) return false;
    return (typeof v === 'string' ? v : Object.keys(v)[0]) === 'running';
  });

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

  // Cache-hit detection.  When the spawned nodeRunActor is in the
  // `cached` substate, the Output tab must (a) render a banner naming
  // the original run id and (b) NOT render the "Connecting…" log-phase
  // placeholder.
  let cacheHitBanner = $derived.by(() => {
    void actorTick.count;
    const snap = nodeActorSnap;
    if (!snap) return null;
    const v = snap.value;
    if (v !== 'cached') return null;
    return {
      originalRunId: snap.context.originalRunId ?? null,
      cacheKey: snap.context.cacheKey ?? null,
    };
  });

  // Cancellation-cause banner text. Reads from the actor's
  // `cancelled.becauseUpstream` / `cancelled.becauseUser` substate +
  // its upstream payload.
  //
  // For `becauseUpstream`, the banner needs the upstream node's label
  // (e.g. "Filter") so the user sees "Cancelled because Filter failed".
  // The actor only stores the upstream node's *id* — we look up the
  // current label in the canvas `$nodes` store. If the upstream node
  // was deleted between failure and render, fall back to the id.
  let cancellationBanner = $derived.by(() => {
    void actorTick.count;
    void nodesTick.count;
    const snap = nodeActorSnap;
    if (!snap) return null;
    if (snap.matches({ cancelled: 'becauseUpstream' })) {
      const upstreamId = snap.context.upstreamNodeId ?? '?';
      const upstreamRunId = snap.context.upstreamRunId ?? '?';
      const upstream = $nodes.find(n => n.id === upstreamId);
      const upstreamLabel = upstream?.data.label ?? upstreamId;
      return {
        kind: 'upstream' as const,
        upstreamNodeId: upstreamId,
        upstreamLabel,
        upstreamRunId,
      };
    }
    if (snap.matches({ cancelled: 'becauseUser' })) {
      return { kind: 'user' as const };
    }
    return null;
  });

  function updateParam(name: string, value: unknown) {
    if (!node) return;
    const newValues = { ...node.data.paramValues, [name]: value };
    updateNodeData(node.id, { paramValues: newValues });
  }

  /**
   * List of samples visible from any Input Selector node in the graph.
   * Used by the "Per Sample" override sub-view for its picker.
   */
  let allGraphSamples = $derived((() => {
    const set = new Set<string>();
    for (const n of $nodes) {
      if (n.data.nodeType === 'input_selector') {
        for (const s of n.data.selectedSamples ?? []) set.add(s);
      }
    }
    return Array.from(set);
  })());

  /**
   * Sample ordering: overridden samples pinned to top, then
   * alphabetical, then remaining alphabetical.
   */
  let orderedSamples = $derived((() => {
    const overrides = data?.sampleOverrides ?? {};
    const svars = data?.sampleVariants ?? {};
    const touched = new Set<string>([...Object.keys(overrides), ...Object.keys(svars)]);
    const overridden = Array.from(touched)
      .filter(s => allGraphSamples.includes(s))
      .sort();
    const remaining = allGraphSamples.filter(s => !overridden.includes(s)).sort();
    return [...overridden, ...remaining];
  })());

  // True when this node has any dirty (editing) param rows. Reads
  // through the param-editor aggregator;
  // `aggregatorTick.count` participates in $derived deps so this
  // re-evaluates on every aggregator transition.
  let nodeHasDirty = $derived.by(() => {
    void aggregatorTick.count;
    if (!node) return false;
    return nodeHasDirtyEditors(node.id);
  });

  // ─── options_from lookup ───
  //
  // One entry per `options_from` param: its inputs and every upstream wired
  // to them with that upstream's answer (`null` where the lookup failed or
  // was not asked). An absent entry is a lookup still pending.
  let optionsByParam = $state<Record<string, ParamOptions>>({});

  /**
   * The lookup for every `options_from` param: the wiring and the request
   * each upstream sends (its value sets and pin, or its run). Recomputed
   * when the canvas or the pipeline variables change.
   */
  let lookupPlan = $derived(optionsLookupPlan(
    node, data, $edges, $nodes, lookupPipeline($nodes, $edges, $pipelineVariables)));

  /** Changes only when the wiring or a request changes, not on this node's own edits. */
  let lookupKey = $derived(optionsLookupKey(lookupPlan));

  /**
   * Effect: re-run the lookup whenever its key changes, and replace
   * `optionsByParam` in one write.
   */
  $effect(() => {
    if (!lookupKey) {
      optionsByParam = {};
      return;
    }
    const currentNode = node;
    const plan = untrack(() => lookupPlan);
    let cancelled = false;
    (async () => {
      const next = await resolveOptionsLookup(plan);
      if (!cancelled && currentNode === node) {
        optionsByParam = next;
      }
    })();
    return () => { cancelled = true; };
  });

  // Reset Output tab state whenever the selected node or its last run_id
  // changes, so switching nodes / running again doesn't leak stale lines.
  $effect(() => {
    // Re-run when node identity or last run_id changes.
    void node?.id;
    void lastRunId;
    logLines = [];
    logPhase = 'idle';
    logConnectionLost = false;
    logTraceback = null;
    historicalLoadedForRunId = null;
  });

  $effect(() => {
    // Subscribe to the streaming child of the currently-selected node's
    // nodeRunActor. The streaming machine is invoked by `nodeRunActor`
    // on entry to `running` (with `runId` + `fullMode` as input) and torn
    // down when it leaves; we mirror its snapshot into local $state so
    // the UI keeps the captured lines / terminal frame even after the
    // parent transitions to a terminal/cancelled state.
    if (activeTab !== 'output') return;
    if (!node || !data || data.nodeType !== 'method') return;

    // The streaming child only exists while the parent nodeRunActor is
    // in `running`. Look it up by invoke id.
    const parentSnap = nodeActorSnap;
    if (!parentSnap) return;
    const streamingChild = (parentSnap as unknown as {
      children?: Record<string, { subscribe: (cb: (s: unknown) => void) => { unsubscribe: () => void } } | undefined>;
    }).children?.streaming;

    if (streamingChild) {
      type StreamingSnap = {
        // Either a final-state string ('succeeded' | 'failed' | 'cancelled')
        // or a nested object {'connection-alive': 'connecting' | 'streaming'}.
        value: string | Record<string, string>;
        context: {
          lines: LogLine[];
          terminalError: string | null;
          terminalTraceback: string | null;
        };
      };
      const applySnap = (snap: StreamingSnap) => {
        const v = snap.value;
        // The streaming machine's nested shape: { 'connection-alive':
        // 'connecting' | 'streaming' } for the active phase, or a
        // plain string ('succeeded' | 'failed' | 'cancelled') for finals.
        const phase: LogPhase =
          typeof v === 'object' && v !== null && 'connection-alive' in v
            ? ((v as Record<string, string>)['connection-alive'] as LogPhase)
            : v === 'succeeded' || v === 'failed' || v === 'cancelled'
              ? (v as LogPhase)
              : 'idle';
        logPhase = phase;
        logLines = snap.context.lines;
        logConnectionLost = phase === 'failed' && snap.context.terminalError === 'Connection lost';
        logTraceback = snap.context.terminalTraceback;
      };
      // Apply initial snapshot synchronously — xstate v5 actor.subscribe
      // does NOT fire the callback for actors that have already reached
      // a final state (`terminal`).  Without this, opening Output on a
      // run that already completed leaves the badge stuck at the reset
      // 'idle' value until the historical-fetch fallback fires.
      const childAny = streamingChild as unknown as { getSnapshot?: () => StreamingSnap };
      const initialSnap = childAny.getSnapshot?.();
      if (initialSnap) applySnap(initialSnap);
      const sub = streamingChild.subscribe((s: unknown) => applySnap(s as StreamingSnap));
      return () => sub.unsubscribe();
    }

    // Fallback: no streaming child. Either the run finished before the
    // streaming actor could connect (HEARTBEAT and RUN_OK arriving in the
    // same poll tick — the dogfood race the heartbeat fixture exposes)
    // or the user opened the Output tab on a node whose run has already
    // ended. Fetch the persisted log via the same SSE endpoint with
    // `full=1`; the handler emits historical lines then a terminal frame
    // and closes. Guarded by historicalLoadedForRunId so we don't
    // re-fetch on every parent-actor tick after the run completes.
    if (!lastRunId) return;
    // Read this fallback's own bookkeeping ($state it also writes below)
    // through `untrack` so the effect does NOT depend on it. Otherwise the
    // `logPhase = 'connecting'` and guard writes re-queue the effect, whose
    // cleanup `es.close()` then tears down the EventSource before an
    // output-less run's single terminal frame arrives — stranding the badge
    // on "Connecting…".
    if (untrack(() => historicalLoadedForRunId) === lastRunId) return;
    // If the live streaming child already captured a full log (it
    // reached a final state AND we have lines), the historical fetch
    // would be redundant — and worse, it'd briefly flash the badge
    // back to "Connecting…" while replaying.  Skip in that case.
    const phaseNow = untrack(() => logPhase);
    if (
      (phaseNow === 'succeeded' || phaseNow === 'failed' || phaseNow === 'cancelled') &&
      untrack(() => logLines.length) > 0
    ) {
      historicalLoadedForRunId = lastRunId;
      return;
    }
    // `historicalLoadedForRunId` is set only once the stream reaches a
    // terminal/error frame (in the handlers below), not here. If a
    // reschedule still tears the stream down early, the guard stays unset
    // and the next tick refetches — self-healing rather than stuck.

    logPhase = 'connecting';
    const es = openNodeHistoricalLogStream(lastRunId);
    const accumulated: LogLine[] = [];
    es.onmessage = ev => {
      try {
        const p = JSON.parse(ev.data) as {
          type?: string;
          data?: string;
          status?: string;
          error_message?: string;
          error_traceback?: string;
        };
        if (p.type === 'stdout' || p.type === 'stderr') {
          logPhase = 'streaming';
          accumulated.push({ kind: p.type, line: p.data ?? '' });
          logLines = [...accumulated];
        } else if (p.type === 'terminal') {
          // Same status branching as the streaming machine — keeps the
          // historical-fetch fallback's badge consistent with the live path.
          logPhase =
            p.status === 'success'
              ? 'succeeded'
              : p.status === 'cancelled'
                ? 'cancelled'
                : 'failed';
          logTraceback = p.error_traceback ?? null;
          // Mark loaded only now that the terminal frame has landed.
          historicalLoadedForRunId = lastRunId;
          es.close();
        }
      } catch {
        /* malformed frame — ignore */
      }
    };
    es.onerror = () => {
      // Historical fetch — wire failure here means the persisted-log
      // stream couldn't be replayed. Synthesize a `failed` badge with
      // a Connection lost message, matching the live path.
      const isFinal =
        logPhase === 'succeeded' || logPhase === 'failed' || logPhase === 'cancelled';
      if (!isFinal) {
        logPhase = 'failed';
        logConnectionLost = true;
      }
      // Wire failure is a definitive outcome — mark loaded so we don't
      // refetch in a loop on a genuinely broken stream.
      historicalLoadedForRunId = lastRunId;
      es.close();
    };
    return () => es.close();
  });

</script>

<!-- Failure summary: above the tabs so it reads the same on Config and
     Output. The message is cut to two lines (hover for all of it); the
     traceback is the Output tab's stderr, never repeated here. -->
{#if nodeFailed}
  <div class="node-error-wrap">
    <ErrorBox testid="node-error-box" message={nodeError}
              onSeeOutput={lastRunId ? () => { activeTab = 'output'; } : undefined} />
  </div>
{/if}

<!-- Tabs -->
<div class="tabs">
  <button class="tab" class:active={activeTab === 'config'} onclick={() => { activeTab = 'config'; }}>Config</button>
  <button class="tab" class:active={activeTab === 'output'} onclick={() => { activeTab = 'output'; }}>Output{#if nodeFailed}<span class="tab-x" aria-hidden="true">✗</span><span class="sr-only"> (failed)</span>{/if}</button>
</div>

<!-- Tab content -->
<div class="tab-content">
  {#if activeTab === 'config'}
    <!-- All Samples / Per Sample sub-tabs -->
    <div class="sub-tabs">
      <button class="sub-tab" class:active={paramSubTab === 'all'}
        onclick={() => { paramSubTab = 'all'; }}>All Samples</button>
      <button class="sub-tab" class:active={paramSubTab === 'per-sample'}
        onclick={() => { paramSubTab = 'per-sample'; }}>Per Sample</button>
    </div>

    <div class="param-form" bind:this={rootEl}>
      {#if paramSubTab === 'all'}
        <!-- Node-level header: Lock All commits every dirty row on this node. -->
        <div class="node-param-header">
          <span class="nph-spacer"></span>
          <button class="lock-all" type="button"
            onclick={() => openLockSummary('lock')}
            disabled={!nodeHasDirty}
            title={nodeHasDirty ? 'commit every dirty row on this node' : 'nothing to lock'}>
            🔒 Lock All
          </button>
        </div>

        <!-- ─── All Samples: one ValueList per param ─── -->
        {#each data.params as param}
          {@const hint = constraintHint(param)}
          {@const variants = (data.variants ?? {})[param.name] ?? {}}
          <div class="param-block" data-param={param.name} class:landed={landedParam === param.name}>
            <div class="param-label-row">
              <span class="pname cap-trim">{param.name}</span>
              {#if param.type || param.contractType}
                <span class="ptype"><span class="cap-trim">{param.type ?? param.contractType}</span></span>
              {/if}
              {#if param.required}<span class="required">*</span>{/if}
              {#if param.constraints && hint}
                <span class="constraint-hint" title={hint}>ⓘ</span>
              {/if}
              {#if param.description}
                <span class="param-desc" title={param.description}>
                  <!-- Drawn, not typed, so the ? sits at the circle's centre whatever the font. -->
                  <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                    <circle cx="7" cy="7" r="6.25" fill="none" stroke="currentColor" stroke-width="1"/>
                    <path d="M5.3 5.4 C5.3 4.4 6 3.8 7 3.8 C8 3.8 8.7 4.4 8.7 5.3 C8.7 6.1 8.1 6.4 7.6 6.8 C7.2 7.1 7 7.4 7 8"
                      fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
                    <circle cx="7" cy="10.1" r="0.75" fill="currentColor"/>
                  </svg>
                </span>
              {/if}
            </div>
            <ValueList
              nodeId={node?.id ?? ''}
              {param}
              baseValue={data.paramValues[param.name] ?? param.default}
              variants={variants}
              options={optionsByParam[param.name]}
              onBaseChange={(v) => updateParam(param.name, v)}
              onVariantsChange={(next) => { if (node) updateVariants(node, param.name, next); }} />
          </div>
        {/each}
      {:else}
        <!-- ─── Per Sample: pick a sample, edit per-param overrides ─── -->
        {#if orderedSamples.length === 0}
          <span class="empty-hint">
            No samples selected in the graph. Connect an Input Selector to author per-sample overrides.
          </span>
        {:else}
          <div class="field">
            <label>Sample</label>
            <select bind:value={selectedOverrideSample}>
              <option value="">-- pick a sample --</option>
              {#each orderedSamples as s}
                {@const hasOverride = !!(data.sampleOverrides ?? {})[s] || !!(data.sampleVariants ?? {})[s]}
                <option value={s}>{hasOverride ? '● ' : ''}{s}</option>
              {/each}
            </select>
          </div>

          {#if selectedOverrideSample}
            {@const overrides = (data.sampleOverrides ?? {})[selectedOverrideSample] ?? {}}
            {@const sampleVars = (data.sampleVariants ?? {})[selectedOverrideSample] ?? {}}
            {#each data.params as param}
              {@const hasOverride = param.name in overrides}
              {@const paramSampleVariants = sampleVars[param.name] ?? {}}
              {@const hasSampleVariants = Object.keys(paramSampleVariants).length > 0}
              {@const effective = hasOverride ? overrides[param.name] : (data.paramValues[param.name] ?? param.default ?? '')}
              <div class="param-block">
                <div class="param-label-row">
                  <span class="pname cap-trim">{param.name}</span>
                  {#if param.type || param.contractType}
                    <span class="ptype"><span class="cap-trim">{param.type ?? param.contractType}</span></span>
                  {/if}
                  {#if hasOverride || hasSampleVariants}
                    <span class="override-dot" title="Overridden for this sample">●</span>
                  {/if}
                  {#if hasOverride || hasSampleVariants}
                    <button class="clear-override-btn"
                      onclick={() => { if (node) clearSampleOverrideAndVariants(node, selectedOverrideSample, param.name); }}>
                      Clear override
                    </button>
                  {/if}
                </div>
                <ValueList
                  nodeId={node?.id ?? ''}
                  {param}
                  baseValue={effective}
                  variants={paramSampleVariants}
                  options={optionsByParam[param.name]}
                  dirtyKeySuffix={`::ovr:${selectedOverrideSample}`}
                  onBaseChange={(v) => { if (node) updateSampleOverride(node, selectedOverrideSample, param.name, v); }}
                  onVariantsChange={(next) => { if (node) updateSampleVariants(node, selectedOverrideSample, param.name, next); }} />
              </div>
            {/each}
          {/if}
        {/if}
      {/if}
    </div>
  {:else}
    <!-- Output tab: last-run-scoped log stream for this method node. -->
    {#if !lastRunId && !cacheHitBanner && !cancellationBanner}
      <div class="output-empty">
        Run this pipeline to see output for this node.
      </div>
    {:else if cacheHitBanner}
      <!-- Cache-hit banner. Renders in place of the streaming log block; no
           "Connecting…" placeholder spawns because the parent
           nodeRunActor is in `cached` (not `running`) so the
           streaming child was never invoked. -->
      <div
        class="cache-hit-banner"
        data-testid="cache-hit-banner">
        <div class="cache-hit-title">Cache hit — outputs reused</div>
        <div class="cache-hit-body">
          {#if cacheHitBanner.originalRunId}
            Reused output from run
            <span class="cache-hit-run">#{cacheHitBanner.originalRunId}</span>.
          {:else}
            Reused cached output (original run id unavailable).
          {/if}
          {#if cacheHitBanner.cacheKey}
            <div class="cache-hit-key">key: {cacheHitBanner.cacheKey}</div>
          {/if}
        </div>
        <div class="cache-hit-empty">
          No logs available for this node — execution was skipped.
        </div>
      </div>
    {:else}
      {#if cancellationBanner}
        <div class="causality-banner" data-banner-kind={cancellationBanner.kind}>
          {#if cancellationBanner.kind === 'upstream'}
            Cancelled because
            <span class="causality-method">{cancellationBanner.upstreamLabel}</span>
            failed (run #{cancellationBanner.upstreamRunId}).
          {:else}
            Cancelled by user.
          {/if}
        </div>
      {/if}
      {#if !lastRunId}
        <!-- Cancelled before it ran (an upstream step failed): the banner
             says why, and there is no log to show. -->
      {:else if shownLines.length === 0 && (logPhase === 'succeeded' || logPhase === 'failed' || logPhase === 'cancelled') && logNote === null}
        <div class="output-empty">No output captured.</div>
      {:else}
        {#key lastRunId}<LogPane lines={shownLines} live={nodeIsRunning} note={logNote}
                 noteTone={logNote === 'Connection lost' ? 'failed' : 'muted'}>
          {#snippet actions()}<span class="output-run-id" title="Run #{lastRunId}">#{lastRunId}</span>{/snippet}
        </LogPane>{/key}
      {/if}
    {/if}
  {/if}
</div>

<style>
  .tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .tab {
    flex: 1;
    padding: 7px;
    text-align: center;
    font-size: 12px;
    color: var(--text-muted);
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
  }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }
  .tab-content {
    flex: 1;
    padding: 12px;
    overflow-y: auto;
  }
  .param-form { display: flex; flex-direction: column; gap: 10px; }
  .field { display: flex; flex-direction: column; gap: 3px; }
  .field label { font-size: 12px; color: var(--text-secondary); }
  .field select {
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 6px 8px !important;
    color: var(--text-primary);
    font-size: 13px;
    outline: none;
    width: 100%;
    box-sizing: border-box;
    min-height: 28px;
  }
  .required { color: var(--status-failed-ink); font-weight: 600; }

  /* ValueList per-param layout */
  .param-block { margin-bottom: 14px; }
  .param-block:last-child { margin-bottom: 0; }
  /* The row a Built-from link lands on, for about 1.2 s: the controls blue. */
  .param-block { border-radius: 4px; transition: background-color .4s, box-shadow .4s; }
  .param-block.landed {
    background: color-mix(in oklab, var(--accent) 14%, transparent);
    box-shadow: 0 0 0 4px color-mix(in oklab, var(--accent) 14%, transparent), 0 0 0 5px var(--accent);
  }
  /* Name, type, required mark and help badge share one centre line: each
     text box is trimmed to cap height, so flex centring lines up the letters
     rather than the fonts' ascenders and descenders. */
  .param-label-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 20px;
    margin-bottom: 6px;
  }
  .cap-trim {
    text-box: trim-both cap alphabetic;
  }
  .param-label-row .pname {
    font-size: 15px;
    font-weight: 600;
    color: #f0f0f0;
    letter-spacing: .01em;
  }
  /* An asterisk is drawn at the top of its glyph box; drop it onto the line. */
  .param-label-row .required {
    line-height: 1;
    transform: translateY(0.2em);
  }
  .param-label-row .ptype {
    display: inline-flex;
    align-items: center;
    height: 16px;
    font-family: Consolas, monospace;
    font-size: 10px;
    color: #8aa8d0;
    background: #1b2430;
    padding: 0 6px;
    border-radius: 3px;
    text-transform: lowercase;
  }

  /* Node-level Lock All header */
  .node-param-header {
    display: flex;
    align-items: center;
    margin-bottom: 10px;
  }
  .node-param-header .nph-spacer { flex: 1; }
  .lock-all {
    font-size: 11px;
    color: var(--text-primary);
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 4px 10px;
    cursor: pointer;
    font-family: inherit;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .lock-all:hover:not(:disabled) { background: #262626; border-color: #555; }
  .lock-all:disabled { color: #555; border-color: #2a2a2d; cursor: not-allowed; }

  .constraint-hint {
    font-size: 12px;
    color: var(--accent);
    cursor: help;
    width: 14px;
    height: 14px;
    border: 1px solid var(--accent);
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-weight: 600;
    flex-shrink: 0;
  }
  /* Builder Output tab — last-run-scoped log stream */
  .output-empty {
    padding: 12px;
    color: var(--status-cancelled-ink);
    font-size: 11px;
    text-align: center;
  }
  .causality-banner {
    background: color-mix(in srgb, var(--status-cancelled) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--status-cancelled) 35%, transparent);
    border-radius: 3px;
    padding: 8px 10px;
    font-size: 11px;
    color: #c6d0de;
    margin: 8px 8px 4px;
  }
  .causality-method { font-weight: 600; color: #e6edf3; }
  /* Cache-hit banner. */
  .cache-hit-banner {
    background: color-mix(in srgb, var(--status-ok) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--status-ok) 35%, transparent);
    border-radius: 3px;
    padding: 8px 10px;
    margin: 8px 8px 4px;
    color: #c6d0de;
    font-size: 11px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .cache-hit-title { font-weight: 600; color: var(--status-ok); }
  .cache-hit-body { color: #c6d0de; }
  .cache-hit-run { font-family: Consolas, monospace; color: #e6edf3; }
  .cache-hit-key {
    font-family: Consolas, monospace;
    color: #8a98ac;
    font-size: 10px;
    margin-top: 2px;
  }
  .cache-hit-empty { color: #8a98ac; font-style: italic; margin-top: 2px; }
  .output-run-id {
    font-family: 'Consolas', monospace;
    font-size: 10px;
    color: var(--text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
    max-width: 90px;
  }
  /* The failure box sits above the tabs, the same on Config and Output. */
  .node-error-wrap { margin: 8px 10px 0; }
  .tab-x { color: var(--status-failed-ink); font-weight: 700; margin-left: 4px; }
  .sub-tabs {
    display: flex;
    gap: 2px;
    background: var(--bg-input);
    padding: 3px;
    border-radius: 4px;
    margin-bottom: 10px;
  }
  .sub-tab {
    flex: 1;
    padding: 5px 8px;
    background: none;
    border: none;
    border-radius: 3px;
    color: var(--text-secondary);
    font-size: 11px;
    cursor: pointer;
  }
  .sub-tab.active {
    background: var(--bg-header);
    color: var(--accent);
  }
  .override-dot {
    color: var(--marker-light);
    font-size: 10px;
  }
  .clear-override-btn {
    background: none;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-secondary);
    font-size: 10px;
    padding: 2px 6px;
    cursor: pointer;
    align-self: flex-start;
    margin-top: 3px;
  }
  .clear-override-btn:hover {
    color: var(--marker-light);
    border-color: var(--marker-light);
  }
  .param-desc {
    display: inline-flex;
    color: #999;
    cursor: help;
    flex-shrink: 0;
  }
  .param-desc:hover { color: var(--accent); }
  .param-desc svg { display: block; }
  .empty-hint {
    color: #555;
    font-size: 12px;
    font-style: italic;
    padding: 4px;
  }
</style>
