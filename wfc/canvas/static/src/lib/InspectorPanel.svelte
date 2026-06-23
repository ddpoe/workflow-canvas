<script lang="ts">
  import { selectedNode, selectedNodeId, nodes, edges, updateNodeData,
           deleteNodes, runState, modules } from './stores.js';
  import { pushState } from './history.js';
  import { get } from 'svelte/store';
  import type { CanvasNodeData, ParamDef, SampleInfo, CompletedRun } from './types.js';
  import ValueList from './ValueList.svelte';
  import { pipelineRunActor, paramEditorAggregator,
           awaitAllCommitted, nodeHasDirtyEditors } from './machines/root.js';
  import { onDestroy } from 'svelte';

  // Subscribe to the pipelineRunActor so we re-render when a child's
  // snapshot changes. We don't read context.nodeRefs[id] reactively —
  // instead, on each parent snapshot tick, we resolve the child for the
  // currently-selected node and read its snapshot. ADR-016 §Step 7.
  let actorTick = $state(0);
  const _sub = pipelineRunActor.subscribe(() => { actorTick += 1; });
  // Subscribe to the param-editor aggregator too so per-node dirty
  // state derivations refresh when any child registers/unregisters or
  // transitions between editing/settled. Replaces the legacy
  // `$dirtyParams` reactive read (ADR-016 Phase 2 expand).
  let aggregatorTick = $state(0);
  const _aggSub = paramEditorAggregator.subscribe(() => { aggregatorTick += 1; });
  onDestroy(() => { _sub.unsubscribe(); _aggSub.unsubscribe(); });

  let activeTab = $state<'config' | 'output'>('config');
  let paramSubTab = $state<'all' | 'per-sample'>('all');
  let selectedOverrideSample = $state<string>('');
  let sampleList = $state<SampleInfo[]>([]);
  let completedRuns = $state<CompletedRun[]>([]);

  // Builder Output tab state — last-run-only scope (Option C). The run_id
  // comes from the current pipeline's node_states (populated on each poll);
  // clicking Run again overwrites it. No session accumulation, no
  // per-node multi-run dropdown.
  //
  // The streaming child of the spawned nodeRunActor is the source of
  // truth for `logPhase` / `logLines` / terminal payload (ADR-016
  // single-source invariant). We mirror its snapshot into local $state
  // on each tick so the UI keeps the captured lines / terminal frame
  // even after the parent leaves `running` and tears down the streaming
  // child. There is no longer a local EventSource — the streaming
  // machine wraps `subscribeSSE` inside the actor tree.
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
  // The legacy `causalityUpstream` + `lastRunMeta` $state pair plus its
  // parallel fetchRun → fetchRun chase was removed in review iteration
  // 1. The actor's `cancelled.becauseUpstream` substate plus
  // `context.upstreamNodeId` / `upstreamRunId` already carry everything
  // the banner needs (see `cancellationBanner` below); the upstream
  // node's label is looked up from the live `$nodes` store rather than
  // a separate /api/runs/<id> fetch.

  // Input Selector state
  let inputSelectorTab = $state<'select' | 'settings'>('select');
  let inputSearchQuery = $state('');
  let stagedSamples = $state<string[]>([]);
  let lastInitNodeId = $state<string | null>(null);

  // Run Reference state
  let runRefTab = $state<'select' | 'output'>('select');
  let runSearchQuery = $state('');
  let stagedRunId = $state<string>('');
  let expandedRunIds = $state<Record<string, boolean>>({});

  let node = $derived($selectedNode);
  // `updateNodeData` mutates `node.data` in place (needed so SvelteFlow
  // doesn't lose its drag bookkeeping) and then re-emits the nodes array.
  // Runes-mode `$derived` uses strict equality, so returning `node?.data`
  // directly would be short-circuited on same-ref and deep field changes
  // (paramValues, variants, sampleOverrides) would never reach the
  // template. Return a shallow clone so downstream bindings — notably
  // ValueList's `baseValue` after a commit — always re-evaluate.
  let data = $derived.by(() => {
    void $nodes;
    return node?.data ? { ...node.data } as CanvasNodeData : undefined;
  });
  let currentFanMode = $derived(data?.fanMode ?? 'out');

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
   * Null for system nodes (input_selector / run_reference) and for method
   * nodes whose module/method isn't in the registry — in which case the
   * Inputs/Outputs sections fall back to canvas edge info only.
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
  // first Run). ADR-016 §Step 7 — this is the actor-tree readout.
  let nodeActorSnap = $derived.by(() => {
    void actorTick;
    if (!node) return null;
    const refs = pipelineRunActor.getSnapshot().context.nodeRefs;
    const child = refs[node.id];
    return child ? child.getSnapshot() : null;
  });

  // Resolves to the most recent Run row for the currently-selected method
  // node in the current pipeline execution, or null if none exists yet
  // (pending / cache-hit-only / system node).
  let lastRunId = $derived.by(() => {
    void actorTick;
    return nodeActorSnap?.context.runId ?? null;
  });

  /**
   * Selected node's most recent failure reason, read from the spawned
   * `nodeRunActor`'s context. Only shown when the actor is in the
   * `failed` or `completed_with_failures` state so stale errors from
   * earlier attempts don't shadow a currently-running node.
   */
  let nodeError = $derived.by(() => {
    void actorTick;
    const snap = nodeActorSnap;
    if (!snap) return null;
    const v = snap.value;
    const stateKey = typeof v === 'string' ? v : Object.keys(v)[0];
    if (stateKey !== 'failed' && stateKey !== 'completed_with_failures') return null;
    return snap.context.error_message ?? null;
  });

  // ADR-015 Phase D Bug 4 + Bug 5: cache-hit detection.  When the
  // spawned nodeRunActor is in the `cached` substate, the Output tab
  // must (a) render a banner naming the original run id (Bug 5) and
  // (b) NOT render the "Connecting…" log-phase placeholder (Bug 4).
  let cacheHitBanner = $derived.by(() => {
    void actorTick;
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
  // its upstream payload. Replaces the legacy fetchRun-then-fetchRun
  // chase that the Output tab used to do.
  //
  // For `becauseUpstream`, the banner needs the upstream node's label
  // (e.g. "Filter") so the user sees "Cancelled because Filter failed".
  // The actor only stores the upstream node's *id* — we look up the
  // current label in the canvas `$nodes` store. If the upstream node
  // was deleted between failure and render, fall back to the id.
  let cancellationBanner = $derived.by(() => {
    void actorTick;
    void $nodes;
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
  let nodeErrorExpanded = $state(false);

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
   * UX-3 sample ordering: overridden samples pinned to top, then
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

  function updateVariants(paramName: string, newVariants: Record<string, unknown>) {
    if (!node) return;
    const current = node.data.variants ?? {};
    const next = { ...current };
    if (Object.keys(newVariants).length === 0) {
      delete next[paramName];
    } else {
      next[paramName] = newVariants;
    }
    updateNodeData(node.id, { variants: next });
  }

  // True when this node has any dirty (editing) param rows. Reads
  // through the param-editor aggregator (ADR-016 Phase 2 expand);
  // `aggregatorTick` participates in $derived deps so this re-evaluates
  // on every aggregator transition.
  let nodeHasDirty = $derived.by(() => {
    void aggregatorTick;
    if (!node) return false;
    return nodeHasDirtyEditors(node.id);
  });

  function updateSampleOverride(sample: string, paramName: string, value: unknown) {
    if (!node) return;
    const current = node.data.sampleOverrides ?? {};
    const sampleMap = { ...(current[sample] ?? {}) };
    if (value === undefined || value === '' || value === null) {
      delete sampleMap[paramName];
    } else {
      sampleMap[paramName] = value;
    }
    const next = { ...current };
    if (Object.keys(sampleMap).length === 0) {
      delete next[sample];
    } else {
      next[sample] = sampleMap;
    }
    updateNodeData(node.id, { sampleOverrides: next });
  }

  function updateSampleVariants(sample: string, paramName: string, newVariants: Record<string, unknown>) {
    if (!node) return;
    const current = node.data.sampleVariants ?? {};
    const sampleMap = { ...(current[sample] ?? {}) };
    if (Object.keys(newVariants).length === 0) {
      delete sampleMap[paramName];
    } else {
      sampleMap[paramName] = newVariants;
    }
    const next = { ...current };
    if (Object.keys(sampleMap).length === 0) {
      delete next[sample];
    } else {
      next[sample] = sampleMap;
    }
    updateNodeData(node.id, { sampleVariants: next });
  }

  function clearSampleOverrideAndVariants(sample: string, paramName: string) {
    updateSampleOverride(sample, paramName, '');
    updateSampleVariants(sample, paramName, {});
  }

  function validateParam(param: ParamDef, value: unknown): string | null {
    if (!param.constraints) return null;
    const c = param.constraints;
    if (c.min !== undefined && typeof value === 'number' && value < c.min) return `must be ≥ ${c.min}`;
    if (c.max !== undefined && typeof value === 'number' && value > c.max) return `must be ≤ ${c.max}`;
    if (c.min !== undefined && typeof value === 'string' && value !== '') {
      const n = parseFloat(value);
      if (!isNaN(n) && n < c.min) return `must be ≥ ${c.min}`;
    }
    if (c.max !== undefined && typeof value === 'string' && value !== '') {
      const n = parseFloat(value);
      if (!isNaN(n) && n > c.max) return `must be ≤ ${c.max}`;
    }
    return null;
  }

  function getValidationError(param: ParamDef): string | null {
    if (!data) return null;
    const val = data.paramValues[param.name] ?? param.default;
    return validateParam(param, val);
  }

  function constraintHint(param: ParamDef): string {
    if (!param.constraints) return '';
    const c = param.constraints;
    if (c.min !== undefined && c.max !== undefined) return `${c.min}–${c.max}`;
    if (c.min !== undefined) return `≥ ${c.min}`;
    if (c.max !== undefined) return `≤ ${c.max}`;
    return '';
  }

  function moduleColor(moduleName: string): string {
    const mod = $modules.find(m => m.name === moduleName);
    return mod?.color ?? '#888';
  }

  // ─── Track 1 (ADR-017): column_of_input combobox resolution ───
  //
  // For every param on the selected node whose `column_of_input` is set
  // and `new_column` is false, walk the canvas edges to find the upstream
  // node feeding that named slot, then fetch
  // `/api/contracts/{module}.{method}/output_columns?slot=<src>&params=<json>`
  // (or `&run_id=<id>` for a `run_reference` upstream).
  //
  // Cache shape: `{ [paramName]: { strict, from_params, patterns, all } | null }`
  // — a `null` entry means "lookup failed / no upstream / no contract", which
  // ValueList treats as "render plain input" (existing behavior).
  type ColumnOptionsResp = {
    strict: string[];
    from_params: string[];
    patterns: string[];
    all: string[];
  };
  let columnOptionsByParam = $state<Record<string, ColumnOptionsResp | null>>({});

  /**
   * Derive a stable fingerprint for the current "what params need column
   * resolution?" set, including upstream identity + the upstream's current
   * params (so a sweep on the upstream's chmap re-resolves downstream).
   */
  let columnResolutionKey = $derived.by(() => {
    if (!node || data?.nodeType !== 'method') return '';
    const params = data?.params ?? [];
    const parts: string[] = [];
    for (const p of params) {
      if (!p.column_of_input || p.new_column) continue;
      const slot = p.column_of_input;
      const edge = $edges.find(e => e.target === node!.id && e.targetHandle === slot);
      if (!edge) { parts.push(`${p.name}:NONE`); continue; }
      const upstream = $nodes.find(n => n.id === edge.source);
      if (!upstream) { parts.push(`${p.name}:GONE`); continue; }
      const ud = upstream.data as CanvasNodeData;
      // Run-reference upstream → key by run_id.
      if (ud.nodeType === 'run_reference') {
        parts.push(`${p.name}:RR:${ud.selectedRunId ?? ''}:${edge.sourceHandle ?? ''}`);
        continue;
      }
      // Method upstream → key by module/method/source slot/params hash.
      const paramHash = JSON.stringify(ud.paramValues ?? {});
      parts.push(`${p.name}:M:${ud.module}/${ud.method}:${edge.sourceHandle ?? ''}:${paramHash}`);
    }
    return parts.join('|');
  });

  /**
   * Resolve a single param's column options against the upstream node.
   * Returns the response or `null` for fail/no-upstream/no-contract.
   */
  async function resolveColumnsFor(p: ParamDef): Promise<ColumnOptionsResp | null> {
    if (!node || !p.column_of_input || p.new_column) return null;
    const slot = p.column_of_input;
    const edge = get(edges).find(e => e.target === node!.id && e.targetHandle === slot);
    if (!edge) return null;
    const upstream = get(nodes).find(n => n.id === edge.source);
    if (!upstream) return null;
    const ud = upstream.data as CanvasNodeData;
    const sourceSlot = edge.sourceHandle ?? '';

    let url: string;
    if (ud.nodeType === 'run_reference') {
      if (!ud.selectedRunId) return null;
      // For run_reference, the contract method is keyed off the source
      // run's method — the endpoint accepts run_id and looks it up.
      // We don't know the method name here; the endpoint requires
      // method_full in the path. The backend resolves it by reading the
      // run's stored method when run_id is given against the special
      // sentinel `__run_reference__`. If no such convention exists,
      // fall back to omitting (returns null gracefully).
      url = `/api/contracts/__run_reference__/output_columns?slot=${encodeURIComponent(sourceSlot)}&run_id=${encodeURIComponent(ud.selectedRunId)}`;
    } else {
      if (!ud.module || !ud.method) return null;
      const methodFull = `${ud.module}.${ud.method}`;
      const paramsJson = encodeURIComponent(JSON.stringify(ud.paramValues ?? {}));
      url = `/api/contracts/${encodeURIComponent(methodFull)}/output_columns?slot=${encodeURIComponent(sourceSlot)}&params=${paramsJson}`;
    }
    try {
      const resp = await fetch(url);
      if (!resp.ok) return null;
      const body = await resp.json() as ColumnOptionsResp;
      return body;
    } catch {
      return null;
    }
  }

  /**
   * Effect: re-resolve column options whenever the resolution key changes
   * (selected node, its params, or any upstream's identity/params).
   * Runs lookups in parallel; updates `columnOptionsByParam` atomically.
   */
  $effect(() => {
    void columnResolutionKey;
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
      const next: Record<string, ColumnOptionsResp | null> = {};
      for (const p of targets) {
        next[p.name] = await resolveColumnsFor(p);
      }
      if (!cancelled && currentNode === node) {
        columnOptionsByParam = next;
      }
    })();
    return () => { cancelled = true; };
  });

  function updateLabel(value: string) {
    if (!node) return;
    updateNodeData(node.id, { label: value });
  }

  // NID prefix/suffix: [A-Za-z0-9_-]* only, max 32 chars. Applied at display
  // time to auto-generated v# NIDs. See
  // docs/superpowers/specs/2026-04-18-nid-naming-and-runs-preview-design.md.
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

  // In-place mutations of node.data don't bubble through `data = $derived(node?.data)`
  // because the object reference doesn't change — so read deep fields through
  // a $nodes-dependent derivation (see `currentFanMode` above for the pattern).
  let currentNidPrefix = $derived.by(() => { void $nodes; return data?.nidPrefix ?? ''; });
  let currentNidSuffix = $derived.by(() => { void $nodes; return data?.nidSuffix ?? ''; });

  function handleDeleteNode() {
    if (!node) return;
    pushState();
    deleteNodes([node.id]);
  }

  async function fetchSamples() {
    try {
      const resp = await fetch('/api/wfc/samples');
      if (resp.ok) sampleList = await resp.json();
    } catch { /* noop */ }
  }

  async function fetchCompletedRuns() {
    try {
      const resp = await fetch('/api/wfc/completed-runs');
      if (resp.ok) completedRuns = await resp.json();
    } catch { /* noop */ }
  }

  // Filtered views
  let filteredSamples = $derived(
    sampleList.filter(s =>
      s.name.toLowerCase().includes(inputSearchQuery.toLowerCase()) ||
      s.file_type.toLowerCase().includes(inputSearchQuery.toLowerCase())
    )
  );

  let filteredRuns = $derived(
    completedRuns.filter(r =>
      r.method.toLowerCase().includes(runSearchQuery.toLowerCase()) ||
      r.module.toLowerCase().includes(runSearchQuery.toLowerCase()) ||
      (r.sample ?? '').toLowerCase().includes(runSearchQuery.toLowerCase()) ||
      r.id.includes(runSearchQuery)
    )
  );

  // Input Selector helpers
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

  // ── UX-4: Orphan-override confirmation on sample removal ──
  // When removing a sample from the Input Selector, check whether any
  // node's `sampleOverrides` references that sample.  If so, prompt the
  // user before committing so they can discard, keep (dormant), or cancel.
  let orphanModalOpen = $state(false);
  let orphanPendingSamples = $state<string[]>([]);
  let orphanInfo = $state<Array<{ sample: string; nodeCount: number; nodeIds: string[] }>>([]);

  /**
   * Scan every canvas node's `sampleOverrides` map for entries keyed on
   * any of `removedSamples`.  Returns per-sample counts of the nodes
   * that would become orphan-holders if these samples were dropped.
   */
  function findOrphanOverrides(removedSamples: string[]):
    Array<{ sample: string; nodeCount: number; nodeIds: string[] }> {
    const all = get(nodes);
    const result: Array<{ sample: string; nodeCount: number; nodeIds: string[] }> = [];
    for (const sample of removedSamples) {
      const nodeIds: string[] = [];
      for (const n of all) {
        const d = n.data as CanvasNodeData;
        const override = (d.sampleOverrides ?? {})[sample];
        const svar = (d.sampleVariants ?? {})[sample];
        const hasOverride = !!(override && Object.keys(override).length > 0);
        const hasVariants = !!(svar && Object.values(svar).some(vd => Object.keys(vd ?? {}).length > 0));
        if (hasOverride || hasVariants) {
          nodeIds.push(n.id);
        }
      }
      if (nodeIds.length > 0) {
        result.push({ sample, nodeCount: nodeIds.length, nodeIds });
      }
    }
    return result;
  }

  function commitStagedSamples() {
    if (!node) return;
    updateNodeData(node.id, { selectedSamples: [...orphanPendingSamples] });
  }

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

    // Stash the pending commit and pop the confirmation modal.
    orphanPendingSamples = [...stagedSamples];
    orphanInfo = orphans;
    orphanModalOpen = true;
  }

  function orphanConfirmDiscard() {
    // Commit the Input Selector change AND strip orphan overrides +
    // orphan per-sample variants across all nodes that referenced the
    // removed samples.
    commitStagedSamples();
    const removedSet = new Set(orphanInfo.map(o => o.sample));
    nodes.update(allNodes => {
      for (const n of allNodes) {
        const data = n.data as CanvasNodeData;
        const overrides = data.sampleOverrides;
        if (overrides) {
          let changed = false;
          for (const key of Object.keys(overrides)) {
            if (removedSet.has(key)) {
              delete overrides[key];
              changed = true;
            }
          }
          if (changed) data.sampleOverrides = { ...overrides };
        }
        const svars = data.sampleVariants;
        if (svars) {
          let changed = false;
          for (const key of Object.keys(svars)) {
            if (removedSet.has(key)) {
              delete svars[key];
              changed = true;
            }
          }
          if (changed) data.sampleVariants = { ...svars };
        }
      }
      return [...allNodes];
    });
    orphanModalOpen = false;
    orphanInfo = [];
    orphanPendingSamples = [];
  }

  function orphanConfirmKeep() {
    // Commit the Input Selector change; leave sampleOverrides untouched
    // so they re-activate if the sample is added back.
    commitStagedSamples();
    orphanModalOpen = false;
    orphanInfo = [];
    orphanPendingSamples = [];
  }

  function orphanCancel() {
    // Abort the edit entirely; staged selection is discarded.
    if (node) stagedSamples = [...(node.data.selectedSamples ?? [])];
    orphanModalOpen = false;
    orphanInfo = [];
    orphanPendingSamples = [];
  }

  // Run Reference helpers
  function initStagedRun() {
    if (!node) return;
    stagedRunId = node.data.selectedRunId ?? '';
  }

  function toggleExpandRun(runId: string) {
    expandedRunIds = { ...expandedRunIds, [runId]: !expandedRunIds[runId] };
  }

  function acceptRunSelection() {
    if (!node || !stagedRunId) return;
    const run = completedRuns.find(r => r.id === stagedRunId);
    if (!run) return;
    // Every output slot of the referenced run becomes a handle on the node.
    // Names come from run.output_slots (authoritative: what the run actually
    // produced); types come from the current method contract with a graceful
    // fallback when the contract has drifted since the run executed.
    const mods = get(modules);
    const methodDef = mods
      .find(m => m.name === run.module)
      ?.methods.find(me => me.name === run.method);
    const outputs = run.output_slots.length > 0
      ? run.output_slots.map(slotName => {
          const def = methodDef?.outputs.find(o => o.name === slotName);
          return {
            name: slotName,
            type: def?.type ?? 'csv',
            description: def?.description,
          };
        })
      : [{ name: 'output', type: 'csv' }];
    updateNodeData(node.id, {
      selectedRunId: stagedRunId,
      outputs,
      label: `${run.method} #${run.id}`,
    });
  }

  function formatRelativeTime(isoDate: string): string {
    const diff = Date.now() - new Date(isoDate).getTime();
    const days = Math.floor(diff / 86400000);
    if (days < 1) return 'today';
    if (days === 1) return 'yesterday';
    if (days < 14) return `${days} days ago`;
    const weeks = Math.floor(days / 7);
    if (weeks < 5) return `${weeks} weeks ago`;
    const months = Math.floor(days / 30);
    if (months < 12) return `${months} month${months !== 1 ? 's' : ''} ago`;
    const years = Math.floor(days / 365);
    return `${years} year${years !== 1 ? 's' : ''} ago`;
  }

  $effect(() => {
    if (node) {
      const isNewNode = node.id !== lastInitNodeId;
      if (isNewNode) lastInitNodeId = node.id;
      if (node.data.nodeType === 'input_selector') {
        fetchSamples();
        if (isNewNode) initStagedSamples();
      }
      if (node.data.nodeType === 'run_reference') {
        fetchCompletedRuns();
        if (isNewNode) initStagedRun();
      }
    } else {
      lastInitNodeId = null;
    }
  });

  // Refresh completed runs when a pipeline finishes (running -> not running)
  let wasRunning = $state(false);
  $effect(() => {
    const running = $runState.running;
    if (wasRunning && !running && data?.nodeType === 'run_reference') {
      fetchCompletedRuns();
    }
    wasRunning = running;
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
    if (historicalLoadedForRunId === lastRunId) return;
    // If the live streaming child already captured a full log (it
    // reached a final state AND we have lines), the historical fetch
    // would be redundant — and worse, it'd briefly flash the badge
    // back to "Connecting…" while replaying.  Skip in that case.
    if (
      (logPhase === 'succeeded' || logPhase === 'failed' || logPhase === 'cancelled') &&
      logLines.length > 0
    ) {
      historicalLoadedForRunId = lastRunId;
      return;
    }
    historicalLoadedForRunId = lastRunId;

    logPhase = 'connecting';
    const url = `/api/wfc/run/${encodeURIComponent(lastRunId)}/stream-logs?full=1`;
    const es = new EventSource(url);
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
      es.close();
    };
    return () => es.close();
  });

  // `loadFullLog` was removed alongside the local EventSource. Full-mode
  // dispatch through the streaming machine is a future enhancement.
  void logFullMode;
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
          <span class="inspector-module">{data.module}{data.version ? ' \u00b7 v' + data.version : ''}</span>
        {/if}
      </div>
      <button class="inspector-delete-btn" onclick={handleDeleteNode} title="Delete node">
        &#128465;
      </button>
    </div>

    {#if data.nodeType === 'input_selector'}
      <!-- ═══ Input Selector Inspector ═══ -->
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
              <div class="card-meta">
                {#if sample.registered_at}{formatRelativeTime(sample.registered_at)}{/if}
                {#if sample.file_size} &middot; {(sample.file_size / 1024).toFixed(1)} KB{/if}
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
    {:else if data.nodeType === 'run_reference'}
      <!-- ═══ Run Reference Inspector ═══ -->
      <div class="sys-tabs">
        <button class="sys-tab" class:active={runRefTab === 'select'} onclick={() => { runRefTab = 'select'; }}>Select Run</button>
        <button class="sys-tab" class:active={runRefTab === 'output'} onclick={() => { runRefTab = 'output'; }}>Output</button>
      </div>
      <div class="tab-content">
        {#if runRefTab === 'select'}
          <div class="card-search-bar">
            <input class="card-search-input" type="text" placeholder="Search runs..." bind:value={runSearchQuery} />
            <button class="card-filter-btn" title="Filter options">&#x2699;</button>
          </div>
          {#if filteredRuns.length > 0}
            <div class="card-count">{filteredRuns.length} completed run{filteredRuns.length !== 1 ? 's' : ''}</div>
          {/if}
          {#each filteredRuns as run}
            {@const checked = stagedRunId === run.id}
            {@const expanded = expandedRunIds[run.id] ?? checked}
            {@const paramEntries = Object.entries(run.params ?? {})}
            <div class="card-wrap" class:card-checked-run={checked}
              onclick={() => { stagedRunId = run.id; }}>
              <input type="checkbox" class="corner-check corner-check-run" checked={checked}
                onclick={(e: MouseEvent) => e.stopPropagation()}
                onchange={() => { stagedRunId = run.id; }} />
              <div class="card-row">
                <div>
                  <span class="card-name" style="color: {moduleColor(run.module)}">{run.method}</span>
                  <span class="card-dot">&middot;</span>
                  <span class="card-sample">{run.sample || 'no sample'}</span>
                </div>
                <span class="run-status-dot">&bull;</span>
              </div>
              <div class="card-meta">Run #{run.id}{#if run.finished_at} &middot; {formatRelativeTime(run.finished_at)}{/if}</div>
              {#if expanded && paramEntries.length > 0}
                <table class="params-table"><tbody>
                  {#each paramEntries as [key, val]}
                    <tr><td class="params-key">{key}</td><td class="params-val">{val}</td></tr>
                  {/each}
                </tbody></table>
                {#if run.output_slots.length > 0}
                  <div class="output-slots-section">
                    <div class="output-slots-label">Outputs</div>
                    {#each run.output_slots as slot}
                      <span class="type-badge type-badge-orange">{slot}</span>
                    {/each}
                  </div>
                {/if}
              {:else if !expanded && paramEntries.length > 0}
                <div class="params-summary">{paramEntries.slice(0, 3).map(([k, v]) => `${k}=${v}`).join(', ')}</div>
                <div class="expand-params" onclick={(e: MouseEvent) => { e.stopPropagation(); toggleExpandRun(run.id); }}>
                  &#x25B8; {paramEntries.length} param{paramEntries.length !== 1 ? 's' : ''} &mdash; click to expand
                </div>
              {/if}
            </div>
          {/each}
          {#if filteredRuns.length === 0}
            <span class="empty-hint">No completed runs found.</span>
          {/if}
          <button class="accept-btn" onclick={acceptRunSelection} disabled={!stagedRunId}>Accept Selected Run</button>
        {:else}
          <div class="param-form">
            <div class="field">
              <label>Label</label>
              <input type="text" value={data.label} oninput={(e: Event) => updateLabel((e.target as HTMLInputElement).value)} />
            </div>
            {#if data.selectedRunId}
              {@const selectedRun = completedRuns.find(r => r.id === data.selectedRunId)}
              {#if selectedRun}
                <div class="run-detail">
                  <span class="detail-label">Method:</span> <span class="detail-value">{selectedRun.method}</span>
                  <span class="detail-label">Module:</span> <span class="detail-value">{selectedRun.module}</span>
                  <span class="detail-label">Run:</span> <span class="detail-value">#{selectedRun.id}</span>
                  {#if selectedRun.output_slots.length > 0}
                    <span class="detail-label">Outputs:</span> <span class="detail-value">{selectedRun.output_slots.join(', ')}</span>
                  {/if}
                </div>
              {/if}
            {:else}
              <span class="empty-hint">No run selected yet.</span>
            {/if}
          </div>
        {/if}
      </div>
    {:else}
      <!-- ═══ Method Node Inspector ═══ -->

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
                    <span class="io-type">{slot.type}</span>
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
                    <span class="io-type">{slot.type}</span>
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
                  onclick={() => { void awaitAllCommitted(); }}
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
                    onVariantsChange={(next) => updateVariants(param.name, next)} />
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
                            onclick={() => clearSampleOverrideAndVariants(selectedOverrideSample, param.name)}>
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
                        onBaseChange={(v) => updateSampleOverride(selectedOverrideSample, param.name, v)}
                        onVariantsChange={(next) => updateSampleVariants(selectedOverrideSample, param.name, next)} />
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
            <!-- ADR-015 Phase D Bug 5: cache-hit banner UI surface.
                 Renders in place of the streaming log block; no
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
                <!-- "Load full log" button removed pending full-mode
                     fanout through the streaming machine; the streaming
                     actor's invoke currently passes `fullMode: false`. -->
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
    {/if}
  {:else}
    <div class="no-selection">
      <p>Select a node to inspect its properties.</p>
    </div>
  {/if}
</div>

{#if orphanModalOpen}
  <!-- UX-4: Orphaned-override confirmation modal -->
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
    border-left: 1px solid #3e3e42;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    height: 100%;
    overflow: hidden;
  }
  .inspector-header {
    padding: 10px 12px;
    background: #2d2d30;
    border-bottom: 1px solid #3e3e42;
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
  .tabs {
    display: flex;
    border-bottom: 1px solid #3e3e42;
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
    color: #4A90D9;
    border-bottom-color: #4A90D9;
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
    border: 1px solid #3e3e42;
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
    border: 1px solid #3e3e42;
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

  .field-label {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .constraint-hint {
    font-size: 12px;
    color: #4A90D9;
    cursor: help;
    width: 14px;
    height: 14px;
    border: 1px solid #4A90D9;
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
    border-color: #4A90D9;
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
  .toggle { position: relative; display: inline-block; width: 28px; height: 14px; }
  .toggle input { display: none; }
  .toggle-slider {
    position: absolute;
    inset: 0;
    background: #3e3e42;
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
  .output-json, .log-content {
    font-size: 11px;
    color: #aaa;
    font-family: 'Consolas', monospace;
    line-height: 1.5;
    white-space: pre-wrap;
    margin: 0;
  }

  /* Builder Output tab (step 8) — last-run-scoped log stream */
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
  .causality-sample { color: #8a98ac; }
  /* ADR-015 Phase D Bug 5: cache-hit banner. */
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
  .output-full-btn {
    margin-left: auto;
    background: none;
    border: 1px solid #3e3e42;
    color: #ccc;
    border-radius: 3px;
    font-size: 10px;
    padding: 2px 6px;
    cursor: pointer;
  }
  .output-full-btn:hover { border-color: #4A90D9; color: #4A90D9; }
  .log-pane {
    background: #1e1e1e;
    border: 1px solid #3e3e42;
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
    color: #4A90D9;
  }
  .override-dot {
    color: #E9A847;
    font-size: 10px;
  }
  .override-active {
    border-color: #E9A847 !important;
    background: rgba(233, 168, 71, 0.08) !important;
  }
  .clear-override-btn {
    background: none;
    border: 1px solid #3e3e42;
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
  .no-selection {
    padding: 20px 10px;
    text-align: center;
    color: #666;
    font-size: 13px;
  }
  /* System node inspector styles */
  .system-label {
    color: #1ABC9C !important;
    text-transform: uppercase;
    font-size: 10px !important;
    letter-spacing: 0.5px;
  }
  /* ── System node tabs ── */
  .sys-tabs {
    display: flex;
    border-bottom: 1px solid #3e3e42;
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
    color: #4A90D9;
    border-bottom-color: #4A90D9;
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
    border: 1px solid #3e3e42;
    border-radius: 4px;
    color: #ccc;
    font-size: 13px;
    outline: none;
  }
  .card-filter-btn {
    padding: 7px 8px;
    background: #1e1e1e;
    border: 1px solid #3e3e42;
    border-radius: 4px;
    font-size: 12px;
    color: #888;
    cursor: pointer;
  }
  .card-filter-btn:hover {
    color: #ccc;
    border-color: #4A90D9;
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
    border: 1px solid #3e3e42;
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
  .corner-check-run { accent-color: #4A90D9; }
  .card-checked-input {
    background: rgba(233, 168, 71, 0.08);
    border-color: rgba(233, 168, 71, 0.3);
  }
  .card-checked-run {
    background: rgba(74, 144, 217, 0.08);
    border-color: rgba(74, 144, 217, 0.3);
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
    color: #4A90D9;
    border-color: rgba(74, 144, 217, 0.3);
    background: rgba(74, 144, 217, 0.1);
  }
  .type-badge-orange {
    color: #F39C12;
    border-color: rgba(243, 156, 18, 0.3);
    background: rgba(243, 156, 18, 0.1);
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
    background: #4A90D9;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .accept-btn:hover { background: #3a80c9; }
  .accept-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  /* ── Fan-out toggle ── */
  .fanout-toggle {
    margin-top: 8px;
    padding: 6px 8px;
    background: #1e1e1e;
    border: 1px solid #3e3e42;
    border-radius: 4px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .fanout-label-text { font-size: 12px; color: #ccc; font-weight: 600; }
  .fanout-text { display: flex; flex-direction: column; gap: 2px; }
  .fanout-help { font-size: 10px; color: #777; line-height: 1.3; }
  /* ── Run card specific ── */
  .card-dot { opacity: 0.3; margin: 0 2px; }
  .card-sample { color: #E9A847; font-size: 13px; }
  .run-status-dot { font-size: 16px; color: #50C878; }
  .params-summary {
    font-size: 11px;
    color: #666;
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-family: Consolas, monospace;
  }
  .expand-params {
    font-size: 10px;
    color: #666;
    margin-top: 2px;
    cursor: pointer;
  }
  .expand-params:hover { color: #4A90D9; }
  .params-table {
    margin-top: 4px;
    font-size: 11px;
    font-family: Consolas, monospace;
    width: 100%;
    border-collapse: collapse;
  }
  .params-key { color: #888; padding-right: 6px; white-space: nowrap; }
  .params-val { color: #ccc; }
  .output-slots-section {
    margin-top: 5px;
    padding-top: 4px;
    border-top: 1px solid #3e3e42;
  }
  .output-slots-label {
    font-size: 10px;
    color: #888;
    text-transform: uppercase;
    margin-bottom: 3px;
  }
  .empty-hint {
    color: #555;
    font-size: 12px;
    font-style: italic;
    padding: 4px;
  }
  .run-detail {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 2px 8px;
    padding: 6px;
    background: #1e1e1e;
    border-radius: 3px;
    border: 1px solid #3e3e42;
  }
  .detail-label {
    font-size: 11px;
    color: #888;
  }
  .detail-value {
    font-size: 11px;
    color: #ccc;
  }
  /* ── UX-4: Orphan-override confirmation modal ── */
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
    border: 1px solid #3e3e42;
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
    border-bottom: 1px solid #3e3e42;
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
    border-top: 1px solid #3e3e42;
    flex-wrap: wrap;
  }
  .orphan-btn {
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid #3e3e42;
  }
  .orphan-btn-danger {
    background: #8B3A3A;
    color: white;
    border-color: #8B3A3A;
  }
  .orphan-btn-danger:hover { background: #a04545; }
  .orphan-btn-neutral {
    background: #4A90D9;
    color: white;
    border-color: #4A90D9;
  }
  .orphan-btn-neutral:hover { background: #3a80c9; }
  .orphan-btn-cancel {
    background: #2d2d30;
    color: #ccc;
  }
  .orphan-btn-cancel:hover { border-color: #4A90D9; }
</style>
