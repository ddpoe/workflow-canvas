<script lang="ts">
  /**
   * The Inspector's method-node body: the failure summary, the Config and
   * Output tabs, the declared wiring, the naming affixes, the parameter rows
   * (all-samples and per-sample), and the last run's log stream. The header
   * above it is the Inspector's.
   *
   * The non-markup logic — the override and variant writes, the constraint
   * hint and the `column_of_input` lookup — lives in `./nodeEdits.ts`. The
   * `$derived` and `$effect` that drive the lookup live here, where their
   * dependencies are read.
   */
  import type { Node } from '@xyflow/svelte';
  import {
    nodes, edges, modules, selectedNodeId, updateNodeData,
  } from '../builder/stores.js';
  import { displaySlotType } from '../shared/slotColor.js';
  import type { CanvasNodeData } from '../shared/types.js';
  import ValueList from './ValueList.svelte';
  import { pipelineRunActor, paramEditorAggregator,
           awaitAllCommitted, nodeHasDirtyEditors } from '../machines/root.js';
  import { openLockSummary } from '../builder/lockFlow.js';
  import { onDestroy, untrack } from 'svelte';
  import { openNodeHistoricalLogStream, type ColumnOptionsResp } from './api.js';
  import { RenderTick } from './renderTick.svelte.js';
  import {
    clearSampleOverrideAndVariants, columnResolutionKey, constraintHint,
    resolveColumnOptions, updateSampleOverride, updateSampleVariants,
    updateVariants,
  } from './nodeEdits.js';

  let { node, data }: {
    node: Node<CanvasNodeData> | undefined;
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
  let nodeErrorExpanded = $state(false);

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
  let logTerminalStatus = $state<string | null>(null);
  let logTerminalError = $state<string | null>(null);
  let logTerminalTraceback = $state<string | null>(null);
  // Tracks which runId we've already loaded historical logs for, so the
  // post-run fallback only fetches once per (node, runId) — not on every
  // parent-actor tick after the node entered a terminal state.
  let historicalLoadedForRunId = $state<string | null>(null);
  let logFullMode = $state(false);

  // ── Inputs / Outputs wiring for the Inspector's Config tab ──
  // These read from the registered method contract (modules store) plus
  // live canvas edges so the Inspector always reflects what's actually
  // declared by the method and what's actually wired in the graph.
  // Read-only; clicking a chip selects the wired neighbour in the canvas.

  type SlotWireSource = {
    id: string;
    label: string;
    module: string | undefined;
    handle: string;
    nodeType: CanvasNodeData['nodeType'];
  };
  type InputWiring = {
    slot: { name: string; type: string; description?: string; multi?: boolean };
    source: SlotWireSource | null;
  };
  type OutputWiring = {
    slot: { name: string; type: string; description?: string; multi?: boolean };
    consumers: SlotWireSource[];
  };

  /**
   * The declared method contract for the currently-selected method node.
   * Null for method nodes whose module/method isn't in the registry — in
   * which case the Inputs/Outputs sections fall back to canvas edge info
   * only.
   */
  let methodDef = $derived.by(() => {
    if (!data || data.nodeType !== 'method') return null;
    const mod = $modules.find(m => m.name === data.module);
    return mod?.methods.find(me => me.name === data.method) ?? null;
  });

  function describeNeighbour(nodeId: string, handle: string | undefined | null): SlotWireSource | null {
    const n = $nodes.find(x => x.id === nodeId);
    if (!n) return null;
    const d = n.data as CanvasNodeData;
    return {
      id: n.id,
      label: d.label || n.id,
      module: d.module,
      handle: handle ?? '',
      nodeType: d.nodeType,
    };
  }

  /**
   * For each declared input slot on the current method, locate the canvas
   * edge whose targetHandle matches and surface the source node + handle.
   * Slots with no matching edge surface as `source: null` (rendered as
   * "unwired" in the template).
   */
  let inputWiring = $derived.by<InputWiring[]>(() => {
    if (!node || data?.nodeType !== 'method') return [];
    const declared = methodDef?.inputs ?? [];
    return declared.map(slot => {
      const edge = $edges.find(e => e.target === node!.id && e.targetHandle === slot.name);
      if (!edge) return { slot, source: null };
      return { slot, source: describeNeighbour(edge.source, edge.sourceHandle) };
    });
  });

  /**
   * Symmetric for outputs: list every canvas edge leaving this node's
   * output handle and surface each downstream node + its target handle.
   * A slot with no outgoing edge surfaces as `consumers: []`.
   */
  let outputWiring = $derived.by<OutputWiring[]>(() => {
    if (!node || data?.nodeType !== 'method') return [];
    const declared = methodDef?.outputs ?? [];
    return declared.map(slot => {
      const outs = $edges.filter(e => e.source === node!.id && e.sourceHandle === slot.name);
      const consumers: SlotWireSource[] = [];
      for (const e of outs) {
        const c = describeNeighbour(e.target, e.targetHandle);
        if (c) consumers.push(c);
      }
      return { slot, consumers };
    });
  });

  function jumpToNode(id: string): void {
    selectedNodeId.set(id);
  }

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
   * Selected node's most recent failure reason, read from the spawned
   * `nodeRunActor`'s context. Only shown when the actor is in the
   * `failed` or `completed_with_failures` state so stale errors from
   * earlier attempts don't shadow a currently-running node.
   */
  let nodeError = $derived.by(() => {
    void actorTick.count;
    const snap = nodeActorSnap;
    if (!snap) return null;
    const v = snap.value;
    const stateKey = typeof v === 'string' ? v : Object.keys(v)[0];
    if (stateKey !== 'failed' && stateKey !== 'completed_with_failures') return null;
    return snap.context.error_message ?? null;
  });

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

  // NID prefix/suffix: [A-Za-z0-9_-]* only, max 32 chars. Applied at display
  // time to auto-generated v# NIDs.
  const NID_AFFIX_RE = /^[A-Za-z0-9_-]*$/;
  const NID_AFFIX_MAX = 32;

  function nidAffixError(v: string): string | null {
    if (v.length > NID_AFFIX_MAX) return `max ${NID_AFFIX_MAX} chars`;
    if (!NID_AFFIX_RE.test(v)) return 'letters, digits, _ or - only';
    return null;
  }

  function updateNidPrefix(value: string) {
    if (!node) return;
    updateNodeData(node.id, { nidPrefix: value });
  }

  function updateNidSuffix(value: string) {
    if (!node) return;
    updateNodeData(node.id, { nidSuffix: value });
  }

  // In-place mutations of node.data don't bubble through the `data` prop
  // because the object reference doesn't change — so read deep fields
  // through a `nodesTick`-dependent derivation.
  let currentNidPrefix = $derived.by(() => { void nodesTick.count; return data?.nidPrefix ?? ''; });
  let currentNidSuffix = $derived.by(() => { void nodesTick.count; return data?.nidSuffix ?? ''; });

  // ─── column_of_input combobox resolution ───
  //
  // Cache shape: `{ [paramName]: { strict, from_params, patterns, all } | null }`
  // — a `null` entry means "lookup failed / no upstream / no contract", which
  // ValueList treats as "render plain input".
  let columnOptionsByParam = $state<Record<string, ColumnOptionsResp | null>>({});

  /**
   * Fingerprint of the current "what params need column resolution?" set,
   * including upstream identity + the upstream's current params (so a sweep
   * on the upstream's chmap re-resolves downstream).
   */
  let resolutionKey = $derived(columnResolutionKey(node, data, $edges, $nodes));

  /**
   * Effect: re-resolve column options whenever the resolution key changes
   * (selected node, its params, or any upstream's identity/params).
   * Updates `columnOptionsByParam` atomically.
   */
  $effect(() => {
    void resolutionKey;
    const currentNode = node;
    if (!currentNode || data?.nodeType !== 'method') {
      columnOptionsByParam = {};
      return;
    }
    const params = data?.params ?? [];
    const targets = params.filter(p => p.column_of_input && !p.new_column);
    if (targets.length === 0) {
      columnOptionsByParam = {};
      return;
    }
    let cancelled = false;
    (async () => {
      const next = await resolveColumnOptions(currentNode, targets);
      if (!cancelled && currentNode === node) {
        columnOptionsByParam = next;
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
    logTerminalStatus = null;
    logTerminalError = null;
    logTerminalTraceback = null;
    logFullMode = false;
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
          terminalStatus: string | null;
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
        logTerminalStatus = snap.context.terminalStatus;
        logTerminalError = snap.context.terminalError;
        logTerminalTraceback = snap.context.terminalTraceback;
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
          logTerminalStatus = p.status ?? null;
          logTerminalError = p.error_message ?? null;
          logTerminalTraceback = p.error_traceback ?? null;
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
        logTerminalStatus = 'failed';
        logTerminalError = 'Connection lost';
        logTerminalTraceback = null;
      }
      // Wire failure is a definitive outcome — mark loaded so we don't
      // refetch in a loop on a genuinely broken stream.
      historicalLoadedForRunId = lastRunId;
      es.close();
    };
    return () => es.close();
  });

  // The Output tab has no full-log action: full-mode dispatch through the
  // streaming machine is not implemented, so `logFullMode` stays false.
  void logFullMode;
</script>

<!-- Failure summary: visible on BOTH tabs so the user doesn't need to
     open Output to see what went wrong. Long messages collapse to the
     first line by default; click expands. "View log" jumps to Output
     tab where the full streamed log is already wired up. -->
{#if nodeError}
  {@const firstLine = nodeError.split('\n')[0]}
  {@const isMulti = nodeError.includes('\n') || nodeError.length > firstLine.length}
  <div class="node-error-box" data-testid="node-error-box">
    <div class="node-error-head">
      <span class="ne-icon" aria-hidden="true">✖</span>
      <span class="ne-title">This step failed</span>
      {#if lastRunId}
        <button class="ne-link" type="button"
                onclick={() => { activeTab = 'output'; }}
                title="Open Output tab for the failed run">View log</button>
      {/if}
    </div>
    {#if nodeErrorExpanded || !isMulti}
      <pre class="ne-body">{nodeError}</pre>
    {:else}
      <div class="ne-body-line">{firstLine}</div>
    {/if}
    {#if isMulti}
      <button class="ne-toggle" type="button"
              onclick={() => { nodeErrorExpanded = !nodeErrorExpanded; }}>
        {nodeErrorExpanded ? 'Collapse' : 'Show full error'}
      </button>
    {/if}
  </div>
{/if}

<!-- Tabs -->
<div class="tabs">
  <button class="tab" class:active={activeTab === 'config'} onclick={() => { activeTab = 'config'; }}>Config</button>
  <button class="tab" class:active={activeTab === 'output'} onclick={() => { activeTab = 'output'; }}>Output</button>
</div>

<!-- Tab content -->
<div class="tab-content">
  {#if activeTab === 'config'}
    <!-- Inputs / Outputs wiring (read-only, above Config form) -->
    <div class="io-sections">
      <div class="io-section">
        <div class="section-label">Inputs</div>
        {#if inputWiring.length === 0}
          <div class="io-empty">No declared inputs.</div>
        {:else}
          {#each inputWiring as { slot, source } (slot.name)}
            <div class="io-row">
              <span class="io-name">{slot.name}</span>
              <span class="io-type">{displaySlotType(slot.type)}</span>
              {#if slot.multi}<span class="io-badge" title="Multi-input (fan-in)">multi</span>{/if}
              <span class="io-arrow">←</span>
              {#if source}
                <button class="io-chip" type="button"
                  onclick={() => jumpToNode(source.id)}
                  title={source.nodeType === 'method'
                    ? `${source.module ?? ''} · ${source.label} · ${source.handle}`
                    : source.label}>
                  <span class="io-chip-label">{source.label}</span>
                  {#if source.handle && source.nodeType === 'method'}
                    <span class="io-chip-slot">· {source.handle}</span>
                  {/if}
                </button>
              {:else}
                <span class="io-unwired">unwired</span>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <div class="io-section">
        <div class="section-label">Outputs</div>
        {#if outputWiring.length === 0}
          <div class="io-empty">No declared outputs.</div>
        {:else}
          {#each outputWiring as { slot, consumers } (slot.name)}
            <div class="io-row">
              <span class="io-name">{slot.name}</span>
              <span class="io-type">{displaySlotType(slot.type)}</span>
              <span class="io-arrow">→</span>
              {#if consumers.length === 0}
                <span class="io-unwired">no consumers</span>
              {:else}
                <div class="io-consumers">
                  {#each consumers as c (c.id + ':' + c.handle)}
                    <button class="io-chip" type="button"
                      onclick={() => jumpToNode(c.id)}
                      title={`${c.module ?? ''} · ${c.label} · ${c.handle}`}>
                      <span class="io-chip-label">{c.label}</span>
                      {#if c.handle}<span class="io-chip-slot">· {c.handle}</span>{/if}
                    </button>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
    </div>

    <!-- All Samples / Per Sample sub-tabs -->
    <div class="sub-tabs">
      <button class="sub-tab" class:active={paramSubTab === 'all'}
        onclick={() => { paramSubTab = 'all'; }}>All Samples</button>
      <button class="sub-tab" class:active={paramSubTab === 'per-sample'}
        onclick={() => { paramSubTab = 'per-sample'; }}>Per Sample</button>
    </div>

    {@const prefixVal = currentNidPrefix}
    {@const suffixVal = currentNidSuffix}
    {@const prefixErr = nidAffixError(prefixVal)}
    {@const suffixErr = nidAffixError(suffixVal)}
    <div class="param-form">
      <!-- Naming: prefix/suffix applied to auto-NIDs at display time -->
      <div class="naming-section">
        <div class="section-label">Naming</div>
        <div class="naming-grid">
          <div class="field">
            <label>NID prefix <span class="optional">(optional)</span></label>
            <input type="text"
              class:invalid={prefixErr}
              value={prefixVal}
              maxlength={NID_AFFIX_MAX}
              placeholder="e.g., strict_"
              oninput={(e: Event) => updateNidPrefix((e.target as HTMLInputElement).value)} />
            {#if prefixErr}<span class="validation-error">{prefixErr}</span>{/if}
          </div>
          <div class="field">
            <label>NID suffix <span class="optional">(optional)</span></label>
            <input type="text"
              class:invalid={suffixErr}
              value={suffixVal}
              maxlength={NID_AFFIX_MAX}
              placeholder="e.g., _rerun"
              oninput={(e: Event) => updateNidSuffix((e.target as HTMLInputElement).value)} />
            {#if suffixErr}<span class="validation-error">{suffixErr}</span>{/if}
          </div>
        </div>
        <div class="naming-preview">
          Auto-NID preview:
          <code>{prefixVal}v1{suffixVal}</code>,
          <code>{prefixVal}v2{suffixVal}</code>,
          <code>{prefixVal}v3{suffixVal}</code> …
          <span class="muted">Custom per-run names bypass prefix/suffix.</span>
        </div>
      </div>

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
          <div class="param-block">
            <div class="param-label-row">
              <span class="pname">{param.name}</span>
              {#if param.type || param.contractType}
                <span class="ptype">{param.type ?? param.contractType}</span>
              {/if}
              {#if param.required}<span class="required">*</span>{/if}
              {#if param.constraints && hint}
                <span class="constraint-hint" title={hint}>ⓘ</span>
              {/if}
              {#if param.description}
                <span class="param-desc" title={param.description}>?</span>
              {/if}
            </div>
            <ValueList
              nodeId={node?.id ?? ''}
              {param}
              baseValue={data.paramValues[param.name] ?? param.default}
              variants={variants}
              columnOptions={columnOptionsByParam[param.name] ?? null}
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
                  <span class="pname">{param.name}</span>
                  {#if param.type || param.contractType}
                    <span class="ptype">{param.type ?? param.contractType}</span>
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
                  columnOptions={columnOptionsByParam[param.name] ?? null}
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
          <span class="output-run-id">#{lastRunId}</span>
          <!-- No "Load full log" button: full-mode fanout through the
               streaming machine is not implemented, and the streaming
               actor's invoke passes `fullMode: false`. -->
          {#if logFullMode}{/if}
        </div>
        <pre class="log-pane">{#each logLines as l}<span class={l.kind === 'stderr' ? 'log-stderr' : 'log-stdout'}>{l.line}
</span>{/each}</pre>
        {#if (logPhase === 'failed' || logPhase === 'cancelled') && (logTerminalError || logTerminalTraceback)}
          <div class="err-block">
            {#if logTerminalError}<div class="err-msg">{logTerminalError}</div>{/if}
            {#if logTerminalTraceback}<pre class="err-trace">{logTerminalTraceback}</pre>{/if}
          </div>
        {/if}
      </div>
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
    color: #666;
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
  .field label { font-size: 12px; color: #888; }
  .field input, .field select {
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
  .required { color: #E74C3C; font-weight: 600; }

  /* ValueList per-param layout */
  .param-block { margin-bottom: 14px; }
  .param-block:last-child { margin-bottom: 0; }
  .param-label-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
  .param-label-row .pname {
    font-size: 15px;
    font-weight: 600;
    color: #f0f0f0;
    letter-spacing: .01em;
  }
  .param-label-row .ptype {
    font-family: Consolas, monospace;
    font-size: 10px;
    color: #8aa8d0;
    background: #1b2430;
    padding: 2px 6px;
    border-radius: 3px;
    text-transform: lowercase;
    position: relative;
    top: 3px;
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
    color: #ccc;
    background: #1e1e1e;
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
  .invalid {
    border-color: #E74C3C !important;
  }
  .validation-error {
    font-size: 11px;
    color: #E74C3C;
    margin-top: 1px;
  }
  /* NID naming section */
  .naming-section {
    margin-bottom: 10px;
    padding: 10px 10px 12px;
    background: #1f1f21;
    border: 1px solid #2e2e32;
    border-radius: 4px;
  }

  /* Inputs / Outputs — read-only view of canvas wiring */
  .io-sections {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-bottom: 10px;
  }
  .io-section {
    padding: 10px 10px 12px;
    background: #1f1f21;
    border: 1px solid #2e2e32;
    border-radius: 4px;
  }
  .io-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    padding: 4px 0;
    border-bottom: 1px solid #26262a;
    font-size: 12px;
  }
  .io-row:last-child { border-bottom: none; }
  .io-name {
    color: #ddd;
    font-family: Consolas, monospace;
  }
  .io-type {
    color: #888;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    background: #252528;
    border: 1px solid #303034;
    border-radius: 3px;
    padding: 1px 5px;
  }
  .io-badge {
    color: #7fb2f3;
    font-size: 10px;
    border: 1px solid #2f4466;
    border-radius: 3px;
    padding: 1px 5px;
  }
  .io-arrow {
    color: #555;
    font-size: 14px;
    margin: 0 2px;
  }
  .io-unwired {
    color: #777;
    font-style: italic;
    font-size: 11px;
  }
  .io-empty {
    color: #666;
    font-size: 11px;
    font-style: italic;
  }
  .io-consumers {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .io-chip {
    background: #252528;
    border: 1px solid #3a3a42;
    color: #cfcfd4;
    border-radius: 3px;
    padding: 2px 7px;
    font-size: 11px;
    font-family: inherit;
    cursor: pointer;
    display: inline-flex;
    align-items: baseline;
    gap: 3px;
  }
  .io-chip:hover {
    border-color: var(--accent);
    color: #fff;
  }
  .io-chip-slot {
    color: #888;
    font-family: Consolas, monospace;
    font-size: 10px;
  }
  .section-label {
    font-size: 10px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 6px;
  }
  .naming-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .naming-grid .field { margin: 0; }
  .naming-grid label { font-size: 11px; color: #bbb; }
  .optional { color: #666; font-weight: 400; }
  .naming-preview {
    font-size: 11px;
    color: #9aa0a6;
    margin-top: 8px;
    line-height: 1.5;
  }
  .naming-preview code {
    background: #1a1a1a;
    color: #4fc3f7;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: Consolas, monospace;
    font-size: 10.5px;
  }
  .naming-preview .muted { color: #666; display: block; margin-top: 2px; }

  /* Builder Output tab — last-run-scoped log stream */
  .output-empty {
    padding: 12px;
    color: #7f8ea3;
    font-size: 11px;
    text-align: center;
  }
  .causality-banner {
    background: rgba(127, 142, 163, 0.10);
    border: 1px solid rgba(127, 142, 163, 0.35);
    border-radius: 3px;
    padding: 8px 10px;
    font-size: 11px;
    color: #c6d0de;
    margin: 8px 8px 4px;
  }
  .causality-method { font-weight: 600; color: #e6edf3; }
  /* Cache-hit banner. */
  .cache-hit-banner {
    background: rgba(80, 200, 120, 0.08);
    border: 1px solid rgba(80, 200, 120, 0.35);
    border-radius: 3px;
    padding: 8px 10px;
    margin: 8px 8px 4px;
    color: #c6d0de;
    font-size: 11px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .cache-hit-title { font-weight: 600; color: #50C878; }
  .cache-hit-body { color: #c6d0de; }
  .cache-hit-run { font-family: Consolas, monospace; color: #e6edf3; }
  .cache-hit-key {
    font-family: Consolas, monospace;
    color: #8a98ac;
    font-size: 10px;
    margin-top: 2px;
  }
  .cache-hit-empty { color: #8a98ac; font-style: italic; margin-top: 2px; }
  .output-block {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 4px 8px 8px;
    min-height: 0;
  }
  .output-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 4px;
  }
  .output-status {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 3px;
    background: rgba(127, 142, 163, 0.15);
    color: #c6d0de;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .output-status-streaming { background: rgba(74, 144, 217, 0.20); color: #7fb4ed; }
  .output-status-succeeded { background: rgba(60, 180, 100, 0.15); color: #9fd9b3; }
  .output-status-failed    { background: rgba(231, 76, 60, 0.18); color: #e79993; }
  .output-status-cancelled { background: rgba(200, 160, 60, 0.16); color: #d8c08a; }
  .output-run-id {
    font-family: 'Consolas', monospace;
    font-size: 10px;
    color: var(--accent, #4A90D9);
  }
  .log-pane {
    background: #1e1e1e;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: #d0d0d0;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    line-height: 1.4;
    max-height: 300px;
    overflow: auto;
    padding: 6px 8px;
    margin: 0;
    white-space: pre-wrap;
  }
  .log-stderr { color: #e79993; }
  .log-stdout { color: #d0d0d0; }
  .err-block {
    background: rgba(231, 76, 60, 0.08);
    border: 1px solid rgba(231, 76, 60, 0.30);
    border-radius: 3px;
    padding: 8px 10px;
  }
  /* Top-of-inspector failure summary — matches err-block but sits above the
     tabs so it's visible before the user has selected Output. */
  .node-error-box {
    margin: 8px 10px 0;
    background: rgba(231, 76, 60, 0.08);
    border-left: 3px solid #E74C3C;
    border-top: 1px solid rgba(231, 76, 60, 0.25);
    border-right: 1px solid rgba(231, 76, 60, 0.25);
    border-bottom: 1px solid rgba(231, 76, 60, 0.25);
    border-radius: 3px;
    padding: 8px 10px;
    font-size: 11.5px;
  }
  .node-error-head {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }
  .ne-icon { color: #E74C3C; font-weight: 700; }
  .ne-title { color: #E74C3C; font-weight: 600; flex: 1; }
  .ne-link {
    background: transparent;
    border: 1px solid rgba(231, 76, 60, 0.5);
    color: #ffbaba;
    font-size: 10.5px;
    padding: 1px 6px;
    border-radius: 3px;
    cursor: pointer;
  }
  .ne-link:hover { background: rgba(231, 76, 60, 0.15); }
  .ne-body {
    margin: 0;
    color: #ecc;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.4;
    max-height: 180px;
    overflow: auto;
  }
  .ne-body-line {
    color: #ecc;
    font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ne-toggle {
    margin-top: 4px;
    background: transparent;
    border: none;
    color: #f0c4c4;
    font-size: 10.5px;
    padding: 0;
    cursor: pointer;
    text-decoration: underline dotted;
  }
  .ne-toggle:hover { color: #ffdede; }
  .err-msg { color: var(--color-failed, #E74C3C); font-size: 11px; font-weight: 600; }
  .err-trace {
    margin: 6px 0 0;
    color: #c88;
    font-size: 10px;
    white-space: pre-wrap;
    line-height: 1.4;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .sub-tabs {
    display: flex;
    gap: 2px;
    background: #1e1e1e;
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
    color: #888;
    font-size: 11px;
    cursor: pointer;
  }
  .sub-tab.active {
    background: #2d2d30;
    color: var(--accent);
  }
  .override-dot {
    color: #E9A847;
    font-size: 10px;
  }
  .clear-override-btn {
    background: none;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: #888;
    font-size: 10px;
    padding: 2px 6px;
    cursor: pointer;
    align-self: flex-start;
    margin-top: 3px;
  }
  .clear-override-btn:hover {
    color: #E9A847;
    border-color: #E9A847;
  }
  .param-desc {
    font-size: 11px;
    color: #666;
    cursor: help;
  }
  .empty-hint {
    color: #555;
    font-size: 12px;
    font-style: italic;
    padding: 4px;
  }
</style>
