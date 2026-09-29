<script lang="ts">
  /**
   * RunsPreview — two views of the projected run matrix.
   *
   *   - "All runs" (default) — summary row per method node, with per-method
   *      tallies (runs / cached · local / cached · remote / outputs missing /
   *      new / blocked).  Click a method row to drill in.
   *   - "<method_id>"        — detail table for that method, cache-aware, with
   *                             where each row's outputs are, one column per
   *                             param, pencil-to-rename with explicit Confirm,
   *                             collision detection per (sample, method), and
   *                             a grouping control. Rows with outputs expand
   *                             to one line per output slot with its location
   *                             and action. Grouped by variant name by
   *                             default, so each group reads as one
   *                             whole-pipeline scenario.
   *
   * The rows are the engine's schedule (lib/graph/projection.ts, a preview of
   * the `wfc.graph` package). Rows that reuse another row's output — identical
   * params on the whole upstream chain under that variant name — are marked
   * and hidden behind a Show toggle; every count includes them, so the header
   * matches what History lists after the run. "Reused" is a fact about this
   * schedule; "cached" (below) is a fact about the database — the two are
   * independent.
   *
   * Statuses come from the lock (lockFlow.ts). Until the user locks, and
   * again after any edit, the preview shows run counts only. Lock All posts
   * the pipeline document to POST /api/wfc/cache-status once; the route
   * returns one row per engine target: its status, the blocked reason, the
   * source run, and where each output is. A pipeline-level `blocked_reason`
   * blocks every row with that one reason. When the route cannot be
   * reached, every row is blocked saying so: the preview never shows a
   * status the engine did not report. Run is disabled while any locked row
   * is blocked, with the reasons as its tooltip.
   */
  import { untrack } from 'svelte';
  import { exportPipeline } from './pipeline.js';
  import { nodes as nodesStore, edges as edgesStore, pipelineName as pipelineNameStore } from './stores.js';
  import { lockState, blockedReasons, openLockSummary, requestRun, type LockState } from './lockFlow.js';
  import { COLLAPSED_SAMPLE } from '../shared/types.js';
  import { projectRuns } from '../graph/projection.js';
  import {
    OUTPUT_ACTION, STATUS_ACTION, STATUS_LABEL,
    blockedVerdict, hasSourceRun, isCached, tallyStatuses, verdictsFor, whereSummary, willRun,
    type RowVerdict,
  } from './runsPreviewStatus.js';

  let { onClose, onOpenRun }: {
    onClose?: () => void;
    /** Open a source run in History (the NID link on cached/outputs-missing rows). */
    onOpenRun?: (runId: string) => void;
  } = $props();

  type GroupBy = 'sample' | 'variant' | 'status' | 'none';

  interface Row {
    key: string;
    nodeId: string;
    nodeLabel: string;
    method: string;
    sample: string;
    variant: string;
    params: Record<string, unknown>;
    /** The route's answer for this row; null while it is loading. */
    verdict: RowVerdict | null;
    /** The per-output breakdown is shown. */
    expanded: boolean;
    /** Committed custom name; empty = use auto-NID. */
    pendingRename: string;
    /** Edit session state. */
    editing: boolean;
    draft: string;
    /**
     * For collapsed fan-in rows (sample === COLLAPSED_SAMPLE), the actual sample
     * list bundled into this single projected run. Empty for normal
     * per-sample rows — the UI shows `sample` directly in that case.
     */
    bundledSamples?: string[];
    /**
     * True when an earlier row of this schedule produces the identical output
     * (the package's value-identity reuse rule). Hidden until the Show toggle
     * reveals it; counted in every total.
     */
    reused: boolean;
  }

  let selection = $state<string>('all');
  let groupBy = $state<GroupBy>('variant');
  let showReused = $state(false);
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
   * The projected run matrix. Collapse, the bundle and the per-node rows
   * come from the pure projection in lib/graph/projection.ts — a preview of
   * the engine's schedule, kept aligned with the `wfc.graph` package by the
   * shared shape corpus — and this joins each projected run to its canvas
   * node's NID parts and display label.
   */
  let projections = $derived((() => {
    const labelByNodeId: Record<string, string> = {};
    for (const n of methodNodes) labelByNodeId[n.id] = n.label;

    const out: Omit<Row, 'verdict' | 'expanded' | 'pendingRename' | 'editing' | 'draft'>[] = [];
    for (const p of projectRuns(pipeline)) {
      out.push({
        key: p.key,
        nodeId: p.nodeId,
        nodeLabel: labelByNodeId[p.nodeId] ?? p.method,
        method: p.method,
        sample: p.sample,
        variant: p.variant,
        params: p.params,
        reused: p.reused,
        ...(p.bundledSamples ? { bundledSamples: p.bundledSamples } : {}),
      });
    }
    return out;
  })());

  /** A row's verdict under the current lock; null while unlocked or locking. */
  function verdictOf(s: LockState, key: string): RowVerdict | null {
    if (s.status !== 'locked') return null;
    return s.verdicts[key] ?? blockedVerdict('the engine reported no status for this row');
  }

  /** What an unanswered status cell shows: `…` while locking, `—` while unlocked. */
  let placeholder = $derived($lockState.status === 'locking' ? '…' : '—');
  let locked = $derived($lockState.status === 'locked');

  $effect(() => {
    const s = $lockState;
    untrack(() => {
      rows = rows.map(r => ({ ...r, verdict: verdictOf(s, r.key) }));
    });
  });

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
        verdict: untrack(() => verdictOf($lockState, p.key)),
        expanded: prev?.expanded ?? false,
        pendingRename: prev?.pendingRename ?? '',
        editing: prev?.editing ?? false,
        draft: prev?.draft ?? '',
        bundledSamples: p.bundledSamples,
      };
    });
  });

  $effect(() => {
    if (selection !== 'all' && !methodNodes.some(n => n.id === selection)) {
      selection = 'all';
    }
  });

  function autoNidFor(r: Row): string {
    return r.verdict?.sourceNid || '—';
  }

  function toggleExpanded(key: string) {
    rows = rows.map(r => r.key === key ? { ...r, expanded: !r.expanded } : r);
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
    const location = r.sample === COLLAPSED_SAMPLE ? 'the collapsed bundle' : r.sample;
    return `collides with ${displayedNidFor(r)} in ${location}`;
  }

  /** The rows of the selected view, reused rows included — the header count. */
  let scopedRows = $derived(selection === 'all' ? rows : rows.filter(r => r.nodeId === selection));
  let reusedCount = $derived(scopedRows.filter(r => r.reused).length);
  /** The rows listed: reused rows stay hidden until the Show toggle reveals them. */
  let filteredRows = $derived(showReused ? scopedRows : scopedRows.filter(r => !r.reused));

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
   * "N samples: a, b, c" instead of the raw collapsed-sample sentinel; per-sample
   * rows fall through to the literal sample name.
   */
  function sampleLabel(r: Row): string {
    if (r.sample === COLLAPSED_SAMPLE && r.bundledSamples && r.bundledSamples.length > 0) {
      return `${r.bundledSamples.length} samples: ${r.bundledSamples.join(', ')}`;
    }
    return r.sample;
  }

  function groupKey(r: Row): string {
    switch (groupBy) {
      case 'sample': return sampleLabel(r);
      case 'variant': return r.variant;
      case 'status': return r.verdict ? STATUS_LABEL[r.verdict.status] : 'loading';
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

  /** Tally rows by status; a row still loading counts as `loading`. */
  function tallyRows(rs: Row[]) {
    return tallyStatuses(rs.map(r => r.verdict?.status ?? null));
  }

  function groupSummary(runs: Row[]): string {
    const t = tallyRows(runs);
    const u = runs.filter(r => r.reused).length;
    const parts = [`${runs.length} run${runs.length === 1 ? '' : 's'}`];
    if (t.cachedLocal > 0) parts.push(`${t.cachedLocal} cached · local`);
    if (t.cachedRemote > 0) parts.push(`${t.cachedRemote} cached · remote`);
    if (t.outputsMissing > 0) parts.push(`${t.outputsMissing} outputs missing`);
    if (t.new > 0) parts.push(`${t.new} new`);
    if (t.blocked > 0) parts.push(`${t.blocked} blocked`);
    if (u > 0) parts.push(`${u} reused`);
    return parts.join(' · ');
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
    return { ...mn, total: forNode.length, tally: tallyRows(forNode) };
  }))());

  let counts = $derived((() => {
    let renames = 0, conflicts = 0, reused = 0, jobs = 0;
    for (const r of rows) {
      if (r.pendingRename !== '' && r.pendingRename !== autoNidFor(r)) renames++;
      if (collisionKeys.has(r.key)) conflicts++;
      if (r.reused) reused++;
      if (r.verdict && willRun(r.verdict.status)) jobs++;
    }
    return { tally: tallyRows(rows), jobs, renames, conflicts, reused };
  })());

  /**
   * The action cell for a row. A collision, then a committed rename on a
   * cached or new row, take precedence over the status's own action.
   */
  function rowAction(r: Row, committed: boolean, collision: string | null): { text: string; cls: string } | null {
    if (collision) return { text: 'blocks Run', cls: 'note-err' };
    if (!r.verdict) return null;
    const s = r.verdict.status;
    if (committed && isCached(s)) return { text: 'rename only', cls: 'note-rename' };
    if (committed && (s === 'new_step_changed' || s === 'new_upstream_reruns')) {
      return { text: 'custom name · will run', cls: 'note-rename' };
    }
    return { text: STATUS_ACTION[s], cls: s === 'blocked' ? 'note-err' : 'note-muted' };
  }

  function statusPillClass(s: RowVerdict['status']): string {
    return `pill-${s.replace(/_/g, '-')}`;
  }

  function runLabel(): string {
    const jobs = counts.jobs;
    const renames = counts.renames;
    if (renames === 0) return `Run ${jobs} job${jobs === 1 ? '' : 's'}`;
    return `Run ${jobs} job${jobs === 1 ? '' : 's'} · +${renames} rename${renames === 1 ? '' : 's'}`;
  }
  let runDisabled = $derived(
    counts.conflicts > 0
    || $blockedReasons.length > 0
    || (locked && counts.jobs === 0 && counts.renames === 0),
  );
  let runTitle = $derived(
    $blockedReasons.length > 0 ? ['Blocked:', ...$blockedReasons].join('\n')
      : counts.conflicts > 0 ? 'resolve conflicts first' : '',
  );

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
    <span class="run-count">{scopedRows.length} row{scopedRows.length !== 1 ? 's' : ''}</span>
    {#if reusedCount > 0}
      <button class="toggle-reused" onclick={() => { showReused = !showReused; }}
        title="Rows whose output is identical to an earlier row of this schedule (same params on the whole upstream chain under that variant name)">
        {showReused ? 'Hide' : 'Show'} {reusedCount} reused
      </button>
    {/if}
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
            <th class="th-num">cached · local</th>
            <th class="th-num">cached · remote</th>
            <th class="th-num">outputs missing</th>
            <th class="th-num">new</th>
            <th class="th-num">blocked</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each summaryRows as s, i}
            {@const loading = s.tally.loading > 0}
            <tr class:alt={i % 2 === 1} class="summary-row" data-method={s.id}
                onclick={() => { selection = s.id; }}>
              <td class="col-method">{s.label}</td>
              <td class="col-num col-total">{s.total}</td>
              <td class="col-num col-cached-local">
                {#if loading}<span class="loading">{placeholder}</span>{:else}{s.tally.cachedLocal}{/if}
              </td>
              <td class="col-num col-cached-remote">
                {#if loading}<span class="loading">{placeholder}</span>{:else}{s.tally.cachedRemote}{/if}
              </td>
              <td class="col-num col-outputs-missing">
                {#if loading}<span class="loading">{placeholder}</span>{:else}{s.tally.outputsMissing}{/if}
              </td>
              <td class="col-num col-new">
                {#if loading}<span class="loading">{placeholder}</span>{:else}{s.tally.new}{/if}
              </td>
              <td class="col-num col-blocked">
                {#if loading}<span class="loading">{placeholder}</span>{:else}{s.tally.blocked}{/if}
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
            <th class="th-where">Where</th>
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
                <td colspan={5 + paramCols.length + 1}>
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
                {@const v = r.verdict}
                {@const expandable = !!v && hasSourceRun(v.status) && v.outputs.length > 0}
                {@const action = rowAction(r, committed, collision)}
                <tr class="run-row" data-key={r.key}
                    class:row-renamed={committed && !collision} class:row-conflict={!!collision} class:row-reused={r.reused}>
                  <td class="col-nid">
                    {#if expandable}
                      <button class="btn-chevron" aria-expanded={r.expanded}
                        onclick={() => toggleExpanded(r.key)}
                        title={r.expanded ? 'Hide outputs' : 'Show outputs'}>{r.expanded ? '▾' : '▸'}</button>
                    {:else}
                      <span class="chevron-spacer"></span>
                    {/if}
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
                      {#if !committed && v && hasSourceRun(v.status) && v.sourceRunId !== null && onOpenRun}
                        <button class="nid-text nid-link" title="Open the source run in History"
                          onclick={() => onOpenRun?.(String(v.sourceRunId))}>{displayed}</button>
                      {:else}
                        <span class="nid-text"
                            class:is-renamed={committed}
                            class:is-loading={!v}>
                          {!v ? '…' : displayed}
                        </span>
                      {/if}
                      <button class="btn-pencil" onclick={() => startEdit(r.key)} title="Rename">✎</button>
                      {#if committed}
                        <button class="btn-clear" onclick={() => clearRename(r.key)} title="Clear custom name">×</button>
                      {/if}
                    {/if}
                  </td>
                  <td class="col-status">
                    {#if !v}
                      <span class="pill pill-loading">{placeholder === '…' ? '● loading' : '—'}</span>
                    {:else}
                      <span class="pill {statusPillClass(v.status)}" title={v.reason || undefined}>● {STATUS_LABEL[v.status]}</span>
                      {#if v.status === 'blocked' && v.reason}
                        <div class="status-reason">{v.reason}</div>
                      {/if}
                    {/if}
                  </td>
                  <td class="col-where">{v && v.outputs.length > 0 ? whereSummary(v.outputs) : ''}</td>
                  <td class="col-sample"
                      title={r.bundledSamples && r.bundledSamples.length > 0
                        ? r.bundledSamples.join(', ') : r.sample}
                      class:is-collapsed={r.sample === COLLAPSED_SAMPLE && !!r.bundledSamples?.length}>
                    {sampleLabel(r)}
                  </td>
                  <td class="col-variant" class:override={isOverrideVariant(r.variant)}>
                    {r.variant}
                    {#if r.reused}<span class="pill pill-reused" title="Same output as an earlier row of this schedule">↺ reused</span>{/if}
                  </td>
                  {#each paramCols as p}
                    <td class="p-val p-{valueKind(r.params[p])}">{fmtVal(r.params[p])}</td>
                  {/each}
                  <td class="col-action">
                    {#if action}<span class={action.cls}>{action.text}</span>{/if}
                  </td>
                </tr>
                {#if expandable && r.expanded && v}
                  {#each v.outputs as o}
                    <tr class="output-line" data-row={r.key} data-slot={o.slot}>
                      <td class="out-slot" colspan="2">{o.slot}</td>
                      <td class="out-location out-{o.location}">{o.location}</td>
                      <td colspan={2 + paramCols.length}></td>
                      <td class="out-action">{OUTPUT_ACTION[o.location]}</td>
                    </tr>
                  {/each}
                {/if}
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
        {#if !locked}
          <span class="tally-unlocked">{rows.length} run{rows.length === 1 ? '' : 's'} · {placeholder === '…' ? 'checking the cache…' : 'statuses appear after Lock All'}</span>
        {:else}
        <span class="tally-cached-local">● {counts.tally.cachedLocal} cached · local</span>
        <span class="tally-cached-remote">● {counts.tally.cachedRemote} cached · remote</span>
        <span class="tally-outputs-missing">● {counts.tally.outputsMissing} outputs missing</span>
        <span class="tally-new">● {counts.tally.new} new</span>
        <span class="tally-blocked">● {counts.tally.blocked} blocked</span>
        {/if}
        {#if counts.reused > 0}<span class="tally-reused">↺ {counts.reused} reused</span>{/if}
        {#if counts.renames > 0}<span class="tally-rename">✎ {counts.renames} rename{counts.renames === 1 ? '' : 's'}</span>{/if}
        {#if counts.conflicts > 0}<span class="tally-conflict">✗ {counts.conflicts} conflict{counts.conflicts === 1 ? '' : 's'}</span>{/if}
      </span>
      <span class="footer-actions">
        <button class="lock-btn" data-testid="preview-lock-all" onclick={() => openLockSummary('lock')}
          title="Lock every parameter row and check what is cached">🔒 Lock All</button>
        <button class="run-btn" data-testid="preview-run" disabled={runDisabled} title={runTitle}
          onclick={requestRun}>
          {locked ? runLabel() : 'Run'}
        </button>
      </span>
    </div>
  {/if}
</div>

<style>
  .runs-preview-panel {
    background: #252526;
    border-top: 1px solid var(--border);
    color: #ccc;
    max-height: 420px;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    width: 100%;
  }
  .runs-preview-header {
    display: flex; align-items: center; gap: 14px;
    padding: 8px 14px; background: #2d2d30; border-bottom: 1px solid var(--border);
  }
  .title { color: #ccc; font-size: 13px; font-weight: 600; }
  .view-picker { display: flex; align-items: center; gap: 6px; }
  .view-label { color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  .view-picker select {
    background: #1e1e1e; border: 1px solid var(--border); color: #ccc;
    font-size: 12px; padding: 3px 6px; border-radius: 3px; cursor: pointer;
  }
  .view-picker select:hover { border-color: #555; }
  .view-picker select:focus { outline: none; border-color: var(--accent); }
  .run-count { color: var(--accent); font-size: 12px; font-weight: 600; }
  .toggle-reused {
    background: none; border: 1px solid var(--border); color: #9aa0a6;
    font-size: 11px; padding: 2px 8px; border-radius: 3px; cursor: pointer;
  }
  .toggle-reused:hover { border-color: #555; color: #ccc; }
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
  .summary-row:hover .col-drill { color: var(--accent); }
  .col-method { color: var(--accent); font-weight: 600; }
  .col-num { text-align: right; font-variant-numeric: tabular-nums; width: 80px; }
  .col-total { color: #F39C12; font-weight: 600; }
  .col-cached-local { color: #888; }
  .col-cached-remote { color: #7fb4e8; }
  .col-outputs-missing { color: #e8b04f; }
  .col-new { color: #7fd87f; }
  .col-blocked { color: #E74C3C; }
  .col-num .loading { color: #555; font-style: italic; }
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
  .pill-cached-local { background: #2a2a2a; color: #888; }
  .pill-cached-remote { background: #1e2a3a; color: #7fb4e8; }
  .pill-outputs-missing { background: #3a2e1a; color: #e8b04f; }
  .pill-new-step-changed, .pill-new-upstream-reruns { background: #1e3a1e; color: #7fd87f; }
  .pill-blocked { background: #3a1e1e; color: #E74C3C; }
  .status-reason {
    color: #c07070; font-size: 10.5px; margin-top: 3px;
    white-space: normal; max-width: 320px;
  }
  .col-where { color: #9aa0a6; font-size: 11px; }
  .btn-chevron {
    background: none; border: none; cursor: pointer; color: #888;
    font-size: 11px; padding: 0 4px 0 0; width: 14px;
  }
  .btn-chevron:hover { color: var(--accent); }
  .chevron-spacer { display: inline-block; width: 14px; }
  .nid-link {
    background: none; border: none; padding: 0; cursor: pointer;
    font-family: Consolas, monospace; font-size: 12px; text-decoration: underline dotted;
  }
  .nid-link:hover { color: var(--accent); }
  .output-line td { background: #1f1f21; color: #888; font-size: 11px; padding: 3px 12px; }
  .output-line .out-slot { padding-left: 32px; font-family: Consolas, monospace; }
  .out-local { color: #888; }
  .out-remote { color: #7fb4e8; }
  .out-missing { color: #e8b04f; }
  .pill-loading { background: #2a2a2a; color: #555; font-style: italic; }
  .pill-reused { background: #2a2a2a; color: #9aa0a6; margin-left: 6px; }
  .row-reused td { color: #777; }
  .row-reused .col-variant { color: #6aa06a; }

  .col-sample { color: var(--color-system); }
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
    border-top: 1px solid var(--border); font-size: 12px;
  }
  .tally { color: #9aa0a6; display: flex; gap: 14px; }
  .tally-new { color: #7fd87f; }
  .tally-cached-local { color: #888; }
  .tally-cached-remote { color: #7fb4e8; }
  .tally-outputs-missing { color: #e8b04f; }
  .tally-blocked { color: #E74C3C; }
  .tally-rename { color: #b085e8; }
  .tally-reused { color: #9aa0a6; }
  .tally-conflict { color: #E74C3C; }
  .footer-actions { display: flex; gap: 8px; }
  .tally-unlocked { color: #9aa0a6; font-style: italic; }
  .lock-btn {
    background: #2d2d30; color: #ccc; border: 1px solid var(--border); padding: 6px 12px;
    border-radius: 3px; cursor: pointer;
  }
  .lock-btn:hover { border-color: var(--accent); }
  .run-btn {
    background: var(--accent); color: #000; border: none; padding: 6px 14px;
    border-radius: 3px; font-weight: 500; cursor: pointer;
  }
  .run-btn:hover:not(:disabled) { background: #5aa0e9; }
  .run-btn:disabled { background: #2e2e32; color: #666; cursor: not-allowed; }
</style>
