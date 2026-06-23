<script lang="ts">
  /**
   * RunsPreview — two views of the projected run matrix.
   *
   *   - "All runs" (default) — summary row per method node, with per-method
   *      cache tallies (runs / cached / new).  Click a method row to drill in.
   *   - "<method_id>"        — detail table for that method, cache-aware, with
   *                             one column per param, pencil-to-rename with
   *                             explicit Confirm, collision detection per
   *                             (sample, method), and a grouping control.
   *
   * Backend dependency: POST /api/wfc/cache-status with a list of projections
   * returns `{ key, status: 'cached'|'new', base_nid }` per projection.  When
   * the endpoint is unavailable, everything falls back to "new".
   *
   * See docs/superpowers/specs/2026-04-18-nid-naming-and-runs-preview-design.md.
   */
  import { untrack } from 'svelte';
  import { exportPipeline } from './pipeline.js';
  import { nodes as nodesStore, edges as edgesStore, pipelineName as pipelineNameStore } from './stores.js';
  import type { Node } from '@xyflow/svelte';
  import type { CanvasNodeData } from './types.js';

  let { onClose }: { onClose?: () => void } = $props();

  type CacheStatus = 'cached' | 'new' | 'loading';
  type GroupBy = 'sample' | 'variant' | 'status' | 'none';

  interface Row {
    key: string;
    nodeId: string;
    nodeLabel: string;
    method: string;
    sample: string;
    variant: string;
    params: Record<string, unknown>;
    nidPrefix: string;
    nidSuffix: string;
    baseNid: string;
    cacheStatus: CacheStatus;
    /** Committed custom name; empty = use auto-NID. */
    pendingRename: string;
    /** Edit session state. */
    editing: boolean;
    draft: string;
    /**
     * For collapsed fan-in rows (sample === "__all__"), the actual sample
     * list bundled into this single projected run. Empty for normal
     * per-sample rows — the UI shows `sample` directly in that case.
     */
    bundledSamples?: string[];
  }

  let selection = $state<string>('all');
  let groupBy = $state<GroupBy>('sample');
  let rows = $state<Row[]>([]);
  let collapsedGroups = $state<Record<string, boolean>>({});

  // exportPipeline() reads the nodes/edges/pipelineName stores via `get()`,
  // which is a non-subscribing one-shot read — Svelte 5's $derived tracks
  // dependencies during evaluation, and get() hides the stores from that
  // tracking. Touching the auto-subscribed handles here forces a re-run
  // whenever the canvas changes, so the preview updates live instead of
  // going stale until the user closes and reopens the panel.
  let pipeline = $derived.by(() => {
    void $nodesStore;
    void $edgesStore;
    void $pipelineNameStore;
    return exportPipeline();
  });

  let methodNodes = $derived((() => {
    const seen: Record<string, number> = {};
    return pipeline.nodes
      .filter(n => !n.type || n.type === 'method')
      .map(n => {
        const m = n.method ?? n.id;
        seen[m] = (seen[m] ?? 0) + 1;
        const suffix = seen[m] > 1 ? ` #${seen[m]}` : '';
        return { id: n.id, method: m, label: `${m}${suffix}` };
      });
  })());

  /**
   * Method nodes whose sample axis has collapsed to "__all__" because
   * they're downstream of an input_selector(fan_mode="in"). Collapse is
   * contagious — anything reachable via directed edges from a fan-in
   * selector runs once per variant, not once per (sample, variant).
   * Mirrors the engine's propagation logic in
   * ``wfc.snakemake_gen.load_pipeline`` so the preview matches actual
   * execution shape.
   */
  let collapsedNodeIds = $derived((() => {
    const fanInSelectors = new Set(
      pipeline.nodes
        .filter(n => n.type === 'input_selector' && (n as any).fan_mode === 'in')
        .map(n => n.id),
    );
    if (fanInSelectors.size === 0) return new Set<string>();

    // Build adjacency from links (source → [targets]).
    const adj: Record<string, string[]> = {};
    for (const link of pipeline.links ?? []) {
      (adj[link.source] ??= []).push(link.target);
    }
    // BFS from each fan-in selector through downstream nodes.
    const visited = new Set<string>();
    const queue = [...fanInSelectors];
    while (queue.length) {
      const src = queue.shift()!;
      for (const tgt of adj[src] ?? []) {
        if (!visited.has(tgt)) {
          visited.add(tgt);
          queue.push(tgt);
        }
      }
    }
    // Filter to method nodes — selectors/run_refs themselves aren't runs.
    const methodIds = new Set(
      pipeline.nodes.filter(n => !n.type || n.type === 'method').map(n => n.id),
    );
    const result = new Set<string>();
    for (const id of visited) if (methodIds.has(id)) result.add(id);
    return result;
  })());

  /**
   * Sample list bundled into every collapsed run, taken from the first
   * fan-in input_selector's `samples` array. This is what gets baked
   * into the pipeline on execution — shown in the preview's sample
   * column as "N samples: ..." instead of the raw "__all__" sentinel.
   */
  let collapsedBundledSamples = $derived((() => {
    for (const n of pipeline.nodes) {
      if (n.type === 'input_selector' && (n as any).fan_mode === 'in') {
        const ss = (n as any).samples;
        if (Array.isArray(ss)) return ss.map(String);
      }
    }
    return [];
  })());

  let projections = $derived((() => {
    const out: Omit<Row, 'baseNid' | 'cacheStatus' | 'pendingRename' | 'editing' | 'draft'>[] = [];
    const samples = pipeline.samples ?? [];
    const paramSets = pipeline.param_sets ?? {};
    const explicit = pipeline.explicit_combos ?? [];
    const nodesById: Record<string, Node<CanvasNodeData>> = {};
    for (const n of $nodesStore) nodesById[n.id] = n;

    const labelByNodeId: Record<string, string> = {};
    for (const n of methodNodes) labelByNodeId[n.id] = n.label;

    for (const node of pipeline.nodes) {
      if (node.type && node.type !== 'method') continue;
      const nodeData = nodesById[node.id]?.data;
      const nidPrefix = nodeData?.nidPrefix ?? '';
      const nidSuffix = nodeData?.nidSuffix ?? '';
      const nodeLabel = labelByNodeId[node.id] ?? node.method;
      const nodeVariants = paramSets[node.id];
      const isCollapsed = collapsedNodeIds.has(node.id);
      const push = (sample: string, variant: string, params: Record<string, unknown>) => {
        out.push({
          key: `${node.id}::${sample}::${variant}`,
          nodeId: node.id,
          nodeLabel,
          method: node.method,
          sample,
          variant,
          params,
          nidPrefix,
          nidSuffix,
          ...(isCollapsed && sample === '__all__'
            ? { bundledSamples: collapsedBundledSamples }
            : {}),
        });
      };

      if (isCollapsed) {
        // Collapsed: one row per variant, always with sample='__all__'.
        // Per-sample overrides aren't supported on collapsed pipelines
        // (engine rejects them), so explicit_combos is ignored here.
        if (nodeVariants && Object.keys(nodeVariants).length > 0) {
          for (const [vname, vparams] of Object.entries(nodeVariants)) {
            push('__all__', vname, vparams);
          }
        } else {
          push('__all__', 'default', node.params ?? {});
        }
        continue;
      }

      if (nodeVariants && Object.keys(nodeVariants).length > 0) {
        if (explicit.length > 0) {
          for (const combo of explicit) {
            const vparams = nodeVariants[combo.variant] ?? node.params ?? {};
            push(combo.sample, combo.variant, vparams);
          }
        } else {
          for (const sample of samples) {
            for (const [vname, vparams] of Object.entries(nodeVariants)) {
              push(sample, vname, vparams);
            }
          }
        }
      } else {
        for (const sample of samples) {
          push(sample, 'default', node.params ?? {});
        }
      }
    }
    return out;
  })());

  $effect(() => {
    const projs = projections;
    const prevByKey: Record<string, Row> = {};
    untrack(() => {
      for (const r of rows) prevByKey[r.key] = r;
    });
    rows = projs.map(p => {
      const prev = prevByKey[p.key];
      return {
        ...p,
        baseNid: prev?.baseNid ?? '',
        cacheStatus: prev?.cacheStatus ?? 'loading',
        pendingRename: prev?.pendingRename ?? '',
        editing: prev?.editing ?? false,
        draft: prev?.draft ?? '',
        bundledSamples: p.bundledSamples,
      };
    });
    void refreshCacheStatus(projs);
  });

  $effect(() => {
    if (selection !== 'all' && !methodNodes.some(n => n.id === selection)) {
      selection = 'all';
    }
  });

  let lastStatusFetchToken = 0;
  async function refreshCacheStatus(projs: typeof projections) {
    if (projs.length === 0) return;
    const token = ++lastStatusFetchToken;
    const body = {
      projections: projs.map(p => ({
        key: p.key,
        node_id: p.nodeId,
        method: p.method,
        sample: p.sample,
        params: p.params,
      })),
    };
    try {
      const resp = await fetch('/api/wfc/cache-status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`status ${resp.status}: ${await resp.text()}`);
      const data = await resp.json();
      if (token !== lastStatusFetchToken) return;
      const byKey: Record<string, { status: CacheStatus; base_nid: string }> = {};
      for (const entry of data.projections ?? []) {
        byKey[entry.key] = { status: entry.status, base_nid: entry.base_nid ?? '' };
      }
      rows = rows.map(r => {
        const hit = byKey[r.key];
        if (!hit) return { ...r, cacheStatus: 'new' as CacheStatus, baseNid: '' };
        return { ...r, cacheStatus: hit.status, baseNid: hit.base_nid };
      });
    } catch {
      if (token !== lastStatusFetchToken) return;
      rows = rows.map(r => ({ ...r, cacheStatus: 'new' as CacheStatus, baseNid: '' }));
    }
  }

  function autoNidFor(r: Row): string {
    if (!r.baseNid) return '—';
    return `${r.nidPrefix}${r.baseNid}${r.nidSuffix}`;
  }

  /** What's shown/used for collision: draft when editing, else committed rename, else auto. */
  function displayedNidFor(r: Row): string {
    if (r.editing) return r.draft !== '' ? r.draft : autoNidFor(r);
    if (r.pendingRename !== '') return r.pendingRename;
    return autoNidFor(r);
  }

  function startEdit(key: string) {
    rows = rows.map(r => r.key === key
      ? { ...r, editing: true, draft: r.pendingRename !== '' ? r.pendingRename : autoNidFor(r) }
      : r);
  }
  function updateDraft(key: string, value: string) {
    rows = rows.map(r => r.key === key ? { ...r, draft: value } : r);
  }
  function confirmRename(key: string) {
    rows = rows.map(r => {
      if (r.key !== key) return r;
      const auto = autoNidFor(r);
      // If draft == auto, treat as clearing the rename.
      const committed = (r.draft === '' || r.draft === auto) ? '' : r.draft;
      return { ...r, pendingRename: committed, editing: false, draft: '' };
    });
  }
  function cancelEdit(key: string) {
    rows = rows.map(r => r.key === key ? { ...r, editing: false, draft: '' } : r);
  }
  function clearRename(key: string) {
    rows = rows.map(r => r.key === key ? { ...r, pendingRename: '', editing: false, draft: '' } : r);
  }

  let collisionKeys = $derived((() => {
    const perGroup: Record<string, Record<string, number>> = {};
    for (const r of rows) {
      const g = `${r.sample}::${r.method}`;
      perGroup[g] = perGroup[g] ?? {};
      const n = displayedNidFor(r);
      if (n === '—') continue;
      perGroup[g][n] = (perGroup[g][n] ?? 0) + 1;
    }
    const bad = new Set<string>();
    for (const r of rows) {
      const n = displayedNidFor(r);
      if (n === '—') continue;
      if ((perGroup[`${r.sample}::${r.method}`]?.[n] ?? 0) > 1) bad.add(r.key);
    }
    return bad;
  })());

  function collidesWith(r: Row): string | null {
    if (!collisionKeys.has(r.key)) return null;
    const location = r.sample === '__all__' ? 'the collapsed bundle' : r.sample;
    return `collides with ${displayedNidFor(r)} in ${location}`;
  }

  let filteredRows = $derived(selection === 'all' ? rows : rows.filter(r => r.nodeId === selection));

  let paramCols = $derived((() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const r of filteredRows) {
      for (const k of Object.keys(r.params)) {
        if (!seen.has(k)) { seen.add(k); out.push(k); }
      }
    }
    return out;
  })());

  /**
   * Display text for a row's sample cell. Collapsed fan-in rows render as
   * "N samples: a, b, c" instead of the raw "__all__" sentinel; per-sample
   * rows fall through to the literal sample name.
   */
  function sampleLabel(r: Row): string {
    if (r.sample === '__all__' && r.bundledSamples && r.bundledSamples.length > 0) {
      return `${r.bundledSamples.length} samples: ${r.bundledSamples.join(', ')}`;
    }
    return r.sample;
  }

  function groupKey(r: Row): string {
    switch (groupBy) {
      case 'sample': return sampleLabel(r);
      case 'variant': return r.variant;
      case 'status': return r.cacheStatus;
      case 'none': return '';
    }
  }
  let grouped = $derived((() => {
    const out: Record<string, Row[]> = {};
    const order: string[] = [];
    for (const r of filteredRows) {
      const k = groupKey(r);
      if (!(k in out)) { out[k] = []; order.push(k); }
      out[k].push(r);
    }
    return order.map(k => ({ key: k, label: k || 'All', runs: out[k] }));
  })());

  function groupSummary(runs: Row[]): string {
    let c = 0, n = 0;
    for (const r of runs) {
      if (r.cacheStatus === 'cached') c++;
      else if (r.cacheStatus === 'new') n++;
    }
    return `${runs.length} run${runs.length === 1 ? '' : 's'} · ${c} cached · ${n} new`;
  }
  function toggleGroup(k: string) {
    collapsedGroups = { ...collapsedGroups, [k]: !collapsedGroups[k] };
  }

  function isOverrideVariant(v: string): boolean { return /__o\d+$/.test(v); }
  function valueKind(v: unknown): 'number' | 'boolean' | 'string' | 'other' {
    if (typeof v === 'number') return 'number';
    if (typeof v === 'boolean') return 'boolean';
    if (typeof v === 'string') return 'string';
    return 'other';
  }
  function fmtVal(v: unknown): string {
    if (v === undefined || v === null) return '—';
    return typeof v === 'string' ? v : JSON.stringify(v);
  }

  let summaryRows = $derived((() => methodNodes.map(mn => {
    const forNode = rows.filter(r => r.nodeId === mn.id);
    let cached = 0, newCount = 0, loading = 0;
    for (const r of forNode) {
      if (r.cacheStatus === 'cached') cached++;
      else if (r.cacheStatus === 'new') newCount++;
      else loading++;
    }
    return { ...mn, total: forNode.length, cached, newCount, loading };
  }))());

  let counts = $derived((() => {
    let cached = 0, newCount = 0, renames = 0, conflicts = 0;
    for (const r of rows) {
      if (r.cacheStatus === 'cached') cached++;
      else if (r.cacheStatus === 'new') newCount++;
      if (r.pendingRename !== '' && r.pendingRename !== autoNidFor(r)) renames++;
      if (collisionKeys.has(r.key)) conflicts++;
    }
    return { cached, new: newCount, renames, conflicts };
  })());

  function runLabel(): string {
    const jobs = counts.new;
    const renames = counts.renames;
    if (renames === 0) return `Run ${jobs} job${jobs === 1 ? '' : 's'}`;
    return `Run ${jobs} job${jobs === 1 ? '' : 's'} · +${renames} rename${renames === 1 ? '' : 's'}`;
  }
  let runDisabled = $derived(counts.conflicts > 0 || (counts.new === 0 && counts.renames === 0));

  function onNidKeydown(e: KeyboardEvent, key: string) {
    if (e.key === 'Enter') { e.preventDefault(); confirmRename(key); }
    else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(key); }
  }
</script>

<div class="runs-preview-panel">
  <div class="runs-preview-header">
    <span class="title">Runs Preview</span>
    <label class="view-picker">
      <span class="view-label">View</span>
      <select bind:value={selection}>
        <option value="all">All runs ({rows.length})</option>
        {#each methodNodes as n}
          {@const forNode = rows.filter(r => r.nodeId === n.id).length}
          <option value={n.id}>{n.label} ({forNode})</option>
        {/each}
      </select>
    </label>
    {#if selection !== 'all'}
      <label class="view-picker">
        <span class="view-label">Group by</span>
        <select bind:value={groupBy}>
          <option value="sample">sample</option>
          <option value="variant">variant</option>
          <option value="status">status</option>
          <option value="none">none (flat)</option>
        </select>
      </label>
    {/if}
    <span class="run-count">{filteredRows.length} row{filteredRows.length !== 1 ? 's' : ''}</span>
    {#if onClose}
      <button class="close-btn" onclick={onClose} title="Close">&times;</button>
    {/if}
  </div>

  {#if rows.length === 0}
    <div class="empty">No runs yet. Add method nodes + an Input Selector with samples to see the run matrix.</div>
  {:else if selection === 'all'}
    <div class="table-scroll">
      <table class="runs-table summary">
        <thead>
          <tr>
            <th>Method</th>
            <th class="th-num">Runs</th>
            <th class="th-num">Cached</th>
            <th class="th-num">New</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each summaryRows as s, i}
            <tr class:alt={i % 2 === 1} class="summary-row"
                onclick={() => { selection = s.id; }}>
              <td class="col-method">{s.label}</td>
              <td class="col-num col-total">{s.total}</td>
              <td class="col-num col-cached">
                {#if s.loading > 0}<span class="loading">…</span>{:else}{s.cached}{/if}
              </td>
              <td class="col-num col-new">
                {#if s.loading > 0}<span class="loading">…</span>{:else}{s.newCount}{/if}
              </td>
              <td class="col-drill">details ›</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <div class="table-scroll">
      <table class="runs-table">
        <thead>
          <tr>
            <th class="th-nid">NID</th>
            <th class="th-status">Status</th>
            <th>Sample</th>
            <th>Variant</th>
            {#each paramCols as p}<th>{p}</th>{/each}
            <th class="th-action">Action</th>
          </tr>
        </thead>
        <tbody>
          {#each grouped as g}
            {@const collapsed = collapsedGroups[g.key] ?? false}
            {#if g.key !== '' || grouped.length > 1}
              <tr class="group-row" onclick={() => toggleGroup(g.key)}>
                <td colspan={4 + paramCols.length + 1}>
                  <span class="group-caret">{collapsed ? '▶' : '▼'}</span>
                  {g.label} · <span class="group-summary">{groupSummary(g.runs)}</span>
                </td>
              </tr>
            {/if}
            {#if !collapsed}
              {#each g.runs as r}
                {@const collision = collidesWith(r)}
                {@const auto = autoNidFor(r)}
                {@const committed = r.pendingRename !== '' && r.pendingRename !== auto}
                {@const displayed = r.pendingRename !== '' ? r.pendingRename : auto}
                <tr class:row-renamed={committed && !collision} class:row-conflict={!!collision}>
                  <td class="col-nid">
                    {#if r.editing}
                      <div class="nid-edit">
                        <input class="nid-input" type="text"
                          value={r.draft}
                          placeholder={auto}
                          autofocus
                          onkeydown={(e: KeyboardEvent) => onNidKeydown(e, r.key)}
                          oninput={(e: Event) => updateDraft(r.key, (e.target as HTMLInputElement).value)} />
                        <button class="btn-ok" onclick={() => confirmRename(r.key)} title="Confirm (Enter)">✓</button>
                        <button class="btn-cancel" onclick={() => cancelEdit(r.key)} title="Cancel (Esc)">×</button>
                      </div>
                      {#if collision}
                        <div class="nid-hint hint-err">✗ {collision}</div>
                      {/if}
                    {:else}
                      <span class="nid-text"
                          class:is-renamed={committed}
                          class:is-loading={r.cacheStatus === 'loading'}>
                        {r.cacheStatus === 'loading' ? '…' : displayed}
                      </span>
                      <button class="btn-pencil" onclick={() => startEdit(r.key)} title="Rename">✎</button>
                      {#if committed}
                        <button class="btn-clear" onclick={() => clearRename(r.key)} title="Clear custom name">×</button>
                      {/if}
                    {/if}
                  </td>
                  <td class="col-status">
                    {#if r.cacheStatus === 'loading'}
                      <span class="pill pill-loading">● loading</span>
                    {:else if r.cacheStatus === 'cached'}
                      <span class="pill pill-cached">● cached</span>
                    {:else}
                      <span class="pill pill-new">● new</span>
                    {/if}
                  </td>
                  <td class="col-sample"
                      title={r.bundledSamples && r.bundledSamples.length > 0
                        ? r.bundledSamples.join(', ') : r.sample}
                      class:is-collapsed={r.sample === '__all__' && !!r.bundledSamples?.length}>
                    {sampleLabel(r)}
                  </td>
                  <td class="col-variant" class:override={isOverrideVariant(r.variant)}>{r.variant}</td>
                  {#each paramCols as p}
                    <td class="p-val p-{valueKind(r.params[p])}">{fmtVal(r.params[p])}</td>
                  {/each}
                  <td class="col-action">
                    {#if collision}
                      <span class="note-err">blocks Run</span>
                    {:else if committed && r.cacheStatus === 'cached'}
                      <span class="note-rename">rename only</span>
                    {:else if committed && r.cacheStatus === 'new'}
                      <span class="note-rename">custom name · will run</span>
                    {:else if r.cacheStatus === 'cached'}
                      <span class="note-muted">skip</span>
                    {:else if r.cacheStatus === 'new'}
                      <span class="note-muted">will run</span>
                    {/if}
                  </td>
                </tr>
              {/each}
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  {#if rows.length > 0}
    <div class="footer">
      <span class="tally">
        <span class="tally-new">● {counts.new} new</span>
        <span class="tally-cached">● {counts.cached} cached</span>
        {#if counts.renames > 0}<span class="tally-rename">✎ {counts.renames} rename{counts.renames === 1 ? '' : 's'}</span>{/if}
        {#if counts.conflicts > 0}<span class="tally-conflict">✗ {counts.conflicts} conflict{counts.conflicts === 1 ? '' : 's'}</span>{/if}
      </span>
      <button class="run-btn" disabled={runDisabled}
        title={counts.conflicts > 0 ? 'resolve conflicts first' : ''}>
        {runLabel()}
      </button>
    </div>
  {/if}
</div>

<style>
  .runs-preview-panel {
    background: #252526;
    border-top: 1px solid #3e3e42;
    color: #ccc;
    max-height: 420px;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    width: 100%;
  }
  .runs-preview-header {
    display: flex; align-items: center; gap: 14px;
    padding: 8px 14px; background: #2d2d30; border-bottom: 1px solid #3e3e42;
  }
  .title { color: #ccc; font-size: 13px; font-weight: 600; }
  .view-picker { display: flex; align-items: center; gap: 6px; }
  .view-label { color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  .view-picker select {
    background: #1e1e1e; border: 1px solid #3e3e42; color: #ccc;
    font-size: 12px; padding: 3px 6px; border-radius: 3px; cursor: pointer;
  }
  .view-picker select:hover { border-color: #555; }
  .view-picker select:focus { outline: none; border-color: #4A90D9; }
  .run-count { color: #4A90D9; font-size: 12px; font-weight: 600; }
  .close-btn {
    margin-left: auto; background: none; border: none; color: #888;
    font-size: 18px; cursor: pointer; line-height: 1;
  }
  .close-btn:hover { color: #E74C3C; }
  .empty { padding: 14px; color: #666; font-size: 12px; font-style: italic; }
  .table-scroll { overflow: auto; flex: 1; min-height: 0; }
  .runs-table { width: 100%; border-collapse: collapse; font-size: 12px; table-layout: auto; }
  .runs-table th, .runs-table td {
    padding: 5px 12px; text-align: left; border-bottom: 1px solid #333;
    white-space: nowrap; vertical-align: top;
  }
  .runs-table th {
    color: #888; font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.04em;
    background: #2d2d30; position: sticky; top: 0; z-index: 1;
  }
  .th-num { text-align: right; width: 80px; }
  .th-action { width: 140px; }
  .runs-table tr.alt { background: #2a2a2c; }
  .summary-row { cursor: pointer; }
  .summary-row:hover { background: #2f3439; }
  .col-drill { color: #666; font-size: 11px; }
  .summary-row:hover .col-drill { color: #4A90D9; }
  .col-method { color: #4A90D9; font-weight: 600; }
  .col-num { text-align: right; font-variant-numeric: tabular-nums; width: 80px; }
  .col-total { color: #F39C12; font-weight: 600; }
  .col-cached { color: #888; }
  .col-new { color: #7fd87f; }
  .col-cached .loading, .col-new .loading { color: #555; font-style: italic; }
  .group-row { background: #1b1f27 !important; cursor: pointer; user-select: none; }
  .group-row td {
    color: #9aa0a6; font-size: 11px; letter-spacing: 0.04em; padding: 6px 12px;
  }
  .group-caret { display: inline-block; width: 14px; color: #555; }
  .group-summary { color: #666; }
  .row-renamed { background: #1f1a24; }
  .row-conflict { background: #2a1818; }

  /* NID column: text mode + edit mode */
  .col-nid { font-family: Consolas, monospace; min-width: 180px; }
  .nid-text { color: #ccc; }
  .nid-text.is-loading { color: #555; font-style: italic; }
  .nid-text.is-renamed { color: #b085e8; font-weight: 600; }
  .btn-pencil, .btn-clear {
    background: none; border: none; cursor: pointer;
    color: #555; font-size: 12px; padding: 0 4px; margin-left: 4px;
    vertical-align: middle;
  }
  .btn-pencil:hover { color: #4fc3f7; }
  .btn-clear:hover { color: #E74C3C; }
  .nid-edit { display: inline-flex; align-items: center; gap: 4px; }
  .nid-input {
    background: #0f1a24; border: 1px solid #4fc3f7; color: #fff;
    font-family: Consolas, monospace; font-size: 12px;
    padding: 3px 6px; border-radius: 3px; width: 140px;
  }
  .row-conflict .nid-input { border-color: #E74C3C; background: #1a0e0e; }
  .btn-ok, .btn-cancel {
    background: none; border: 1px solid transparent; cursor: pointer;
    font-size: 13px; padding: 0 6px; border-radius: 3px; line-height: 1;
  }
  .btn-ok { color: #7fd87f; }
  .btn-ok:hover { background: #1e3a1e; border-color: #3a5a3a; }
  .btn-cancel { color: #E74C3C; font-size: 16px; }
  .btn-cancel:hover { background: #2a1818; border-color: #5a3a3a; }
  .nid-hint { font-size: 10px; margin-top: 3px; color: #E74C3C; }
  .hint-err { color: #E74C3C; }

  .col-status .pill {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 10.5px; font-weight: 500;
  }
  .pill-cached { background: #2a2a2a; color: #888; }
  .pill-new { background: #1e3a1e; color: #7fd87f; }
  .pill-loading { background: #2a2a2a; color: #555; font-style: italic; }

  .col-sample { color: #1ABC9C; }
  .col-sample.is-collapsed {
    color: #D89E2E;
    font-style: italic;
    font-size: 11px;
  }
  .col-variant { color: #2ECC71; font-weight: 500; }
  .col-variant.override { color: #F39C12; }
  .col-action { color: #666; font-size: 11px; }
  .note-err { color: #E74C3C; }
  .note-rename { color: #b085e8; }
  .note-muted { color: #666; }
  .p-val { font-family: Consolas, monospace; }
  .p-val.p-number { color: #F39C12; }
  .p-val.p-string { color: #8FD980; }
  .p-val.p-boolean { color: #E74C3C; }
  .p-val.p-other { color: #888; }

  .footer {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 14px; background: #1a1a1a;
    border-top: 1px solid #3e3e42; font-size: 12px;
  }
  .tally { color: #9aa0a6; display: flex; gap: 14px; }
  .tally-new { color: #7fd87f; }
  .tally-cached { color: #888; }
  .tally-rename { color: #b085e8; }
  .tally-conflict { color: #E74C3C; }
  .run-btn {
    background: #4A90D9; color: #000; border: none; padding: 6px 14px;
    border-radius: 3px; font-weight: 500; cursor: pointer;
  }
  .run-btn:hover:not(:disabled) { background: #5aa0e9; }
  .run-btn:disabled { background: #2e2e32; color: #666; cursor: not-allowed; }
</style>
