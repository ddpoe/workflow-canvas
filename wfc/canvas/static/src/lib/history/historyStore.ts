/**
 * Reactive state management for the History view.
 * Uses svelte/store writable/derived for cross-component shared state
 * (matches the pattern in stores.ts).
 */
import { writable, derived, get } from 'svelte/store';
import type { MethodInfo, WfcRun } from './historyApi.js';
import { COLLAPSED_SAMPLE } from '../shared/types.js';
import {
  fetchRuns,
  fetchModules,
  fetchMethods,
  favoriteRun as apiFavoriteRun,
  renameRun as apiRenameRun,
  deleteRun as apiDeleteRun,
  setArchived as apiSetArchived,
} from './historyApi.js';

// ---------- Types ----------

export type TimeRange = 'all' | '24h' | '7d' | '30d';

export type RunStatusFilter = 'success' | 'failed' | 'running' | 'cancelled';

export interface FilterState {
  timeRange: TimeRange;
  module: string;          // '' = all modules
  methods: string[];       // [] = all methods
  sample: string[];        // [] = all samples
  searchText: string;
  favoritesOnly: boolean;
  selectMode: boolean;
  // Archive visibility. Default: 'hide' (archived runs excluded from the
  // list). 'only' shows just archived rows; 'all' shows everything.
  archiveView: 'hide' | 'only' | 'all';
  // Status chips. [] = all statuses (no filter). When any are selected,
  // only runs whose status matches one of the selected chips are kept.
  statuses: RunStatusFilter[];
}

export interface PathRow {
  pathId: number;
  nodes: WfcRun[];
}

// ---------- Stores ----------

export const runs = writable<WfcRun[]>([]);
export const loading = writable<boolean>(false);
export const error = writable<string | null>(null);

export const filters = writable<FilterState>({
  timeRange: 'all',
  module: '',
  methods: [],
  sample: [],
  searchText: '',
  favoritesOnly: false,
  selectMode: false,
  archiveView: 'hide',
  statuses: [],
});

export const selectedRunId = writable<string | null>(null);
export const selectedRunIds = writable<Set<string>>(new Set());

export const availableModules = writable<string[]>([]);

/** Full method records (name + owning module) from /api/wfc/methods. */
export const methodInfos = writable<MethodInfo[]>([]);

/**
 * Methods offered by the FilterBar dropdown. Cascades: when a module
 * filter is active, only that module's methods are listed.
 */
export const availableMethods = derived(
  [methodInfos, filters],
  ([$infos, $filters]) => {
    const pool = $filters.module
      ? $infos.filter(m => m.module === $filters.module)
      : $infos;
    return pool.map(m => m.name).sort();
  }
);

// ---------- Favorites (localStorage) ----------

const FAVORITES_KEY = 'wfc-history-favorites';

function loadFavorites(): Set<string> {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    if (raw) return new Set(JSON.parse(raw));
  } catch { /* ignore */ }
  return new Set();
}

function saveFavorites(favs: Set<string>): void {
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favs]));
  } catch { /* ignore */ }
}

const favorites = writable<Set<string>>(loadFavorites());

// ---------- Resolved upstreams + the hidden-run rule ----------

/**
 * A run's resolved upstreams, as the server's lineage relation sends them:
 * input edges in slot order, then the reuse edge of a cache-hit row. The
 * server always sends the field, so a record without it has no upstreams.
 */
function upstreamsOf(run: WfcRun): string[] {
  return run.upstreamRunIds ?? [];
}

/**
 * A run's input edges alone — what fed it, without the reuse edge a
 * cache-hit row also carries. The Lineages view takes row membership from
 * these, so a re-run's row is the re-run and not the re-run plus every
 * original it reused; the reuse edge stays on the record, where it is the
 * CACHED label.
 */
function inputEdgesOf(run: WfcRun): string[] {
  return run.parentRunIds ?? [];
}

/**
 * The one hidden-run rule the History views share. Returns a memoised
 * lookup of a run's nearest visible ancestors: each upstream path is walked
 * through hidden runs until a visible run ends it. A visible run whose
 * lookup is empty heads its own tree or row, so hiding never invents
 * structure. The pre-seeded memo keeps a malformed cyclic history from
 * recursing forever.
 *
 * Which edges count is the caller's choice. The Descendants forest walks
 * the resolved upstreams, so an executed run fed by a cache hit re-anchors
 * at its cache point; the Lineages rows walk the input edges only, so a
 * re-run's row holds the re-run.
 */
function nearestVisibleAncestors(
  allMap: Map<string, WfcRun>,
  visibleSet: Set<string>,
  edgesOf: (run: WfcRun) => string[] = upstreamsOf,
): (id: string) => Set<string> {
  const memo = new Map<string, Set<string>>();
  function nearest(id: string): Set<string> {
    const known = memo.get(id);
    if (known) return known;
    const result = new Set<string>();
    memo.set(id, result);
    const run = allMap.get(id);
    if (!run) return result;
    for (const pid of edgesOf(run)) {
      if (visibleSet.has(pid)) {
        result.add(pid);
      } else {
        for (const a of nearest(pid)) result.add(a);
      }
    }
    return result;
  }
  return nearest;
}

/**
 * A visible run's parents in the Descendants tree: its nearest visible
 * ancestors, reduced. A member that is itself an ancestor of another member
 * is dropped, because the walk through hidden runs reaches grandparents as
 * well as parents: a comparison fed by two cache-hit quantifications reaches
 * each quantification through its reuse edge and each segmentation through
 * its input edge, and only the quantifications are its parents. Ancestry is
 * the visible ancestry, the closure of the nearest-visible lookup. Should a
 * malformed cyclic history reduce a non-empty set to nothing, the unreduced
 * set is kept, so a reduction never turns a child into a root.
 */
function visibleParents(
  nearest: (id: string) => Set<string>,
): (id: string) => Set<string> {
  const ancestry = new Map<string, Set<string>>();
  function ancestorsOf(id: string): Set<string> {
    const known = ancestry.get(id);
    if (known) return known;
    const seen = new Set<string>();
    const queue = [...nearest(id)];
    while (queue.length > 0) {
      const a = queue.shift()!;
      if (seen.has(a)) continue;
      seen.add(a);
      queue.push(...nearest(a));
    }
    ancestry.set(id, seen);
    return seen;
  }
  return (id: string): Set<string> => {
    const direct = nearest(id);
    if (direct.size < 2) return direct;
    const kept = new Set<string>();
    for (const p of direct) {
      let covered = false;
      for (const q of direct) {
        if (q !== p && ancestorsOf(q).has(p)) { covered = true; break; }
      }
      if (!covered) kept.add(p);
    }
    return kept.size > 0 ? kept : direct;
  };
}

// ---------- effectiveSample ----------

/**
 * Cache for effectiveSample lookups. Cleared on each loadRuns().
 */
let effectiveSampleCache = new Map<string, string | null>();

/**
 * Walk the first-upstream chain root-ward through allRuns to find the
 * root's sample. The first upstream is the first input edge, so a fan-in
 * node follows its first slot: the sample identity propagates from the
 * spawning input_selector and every ancestor chain leads back to the same
 * root. A cache-hit row with no input edge follows its reuse edge to the
 * run it reused, which has the same sample (a cache hit matches on sample);
 * when that run is not loaded, the walk ends at the cache-hit row itself.
 * Cycle-safe: caps at 1000 hops. Returns null if the cap is hit, an input
 * edge names a run that is not loaded, or the root has no sample.
 */
export function effectiveSample(run: WfcRun, allRuns: WfcRun[]): string | null {
  if (effectiveSampleCache.has(run.id)) {
    return effectiveSampleCache.get(run.id)!;
  }

  const runMap = new Map<string, WfcRun>();
  for (const r of allRuns) runMap.set(r.id, r);

  let current: WfcRun | undefined = run;
  let hops = 0;
  const MAX_HOPS = 1000;

  while (current && hops < MAX_HOPS) {
    const first: string | undefined = upstreamsOf(current)[0];
    if (first === undefined) break;
    const next: WfcRun | undefined = runMap.get(first);
    if (!next && first === current.cacheSourceRunId) break;
    current = next;
    hops++;
  }

  if (hops >= MAX_HOPS || !current) {
    effectiveSampleCache.set(run.id, null);
    return null;
  }

  const sample = current.dataSource || null;
  effectiveSampleCache.set(run.id, sample);
  return sample;
}

// ---------- Derived: availableSamples ----------

/**
 * Samples offered by the FilterBar dropdown. Cascades: when module or
 * methods filters are active, only samples whose lineage contains a
 * matching run are listed (lineage-aware via effectiveSample). The
 * sample filter itself never narrows this list.
 */
export const availableSamples = derived([runs, filters], ([$runs, $filters]) => {
  let pool = $runs;
  if ($filters.module) {
    pool = pool.filter(r => r.module === $filters.module);
  }
  if ($filters.methods.length > 0) {
    const methodSet = new Set($filters.methods);
    pool = pool.filter(r => methodSet.has(r.method));
  }
  const samples = new Set<string>();
  for (const r of pool) {
    const es = effectiveSample(r, $runs);
    if (es) samples.add(es);
  }
  return Array.from(samples).sort();
});

// ---------- Derived: filtered runs ----------

function timeRangeMs(range: TimeRange): number {
  const now = Date.now();
  switch (range) {
    case '24h': return now - 24 * 60 * 60 * 1000;
    case '7d': return now - 7 * 24 * 60 * 60 * 1000;
    case '30d': return now - 30 * 24 * 60 * 60 * 1000;
    default: return 0;
  }
}

export const filteredRuns = derived(
  [runs, filters, favorites],
  ([$runs, $filters, $favorites]) => {
    let result = $runs;

    // Time range filter
    if ($filters.timeRange !== 'all') {
      const cutoff = timeRangeMs($filters.timeRange);
      result = result.filter(r => r.timestamp >= cutoff);
    }

    // Module filter
    if ($filters.module) {
      result = result.filter(r => r.module === $filters.module);
    }

    // Methods filter
    if ($filters.methods.length > 0) {
      const methodSet = new Set($filters.methods);
      result = result.filter(r => methodSet.has(r.method));
    }

    // Sample filter (uses effectiveSample for lineage-aware filtering)
    if ($filters.sample.length > 0) {
      const sampleSet = new Set($filters.sample);
      result = result.filter(r => {
        const es = effectiveSample(r, $runs);
        return es !== null && sampleSet.has(es);
      });
    }

    // Search text filter (case-insensitive, matches run name, method, module, sample)
    if ($filters.searchText) {
      const term = $filters.searchText.toLowerCase();
      result = result.filter(r =>
        r.runName.toLowerCase().includes(term) ||
        r.method.toLowerCase().includes(term) ||
        r.module.toLowerCase().includes(term) ||
        r.dataSource.toLowerCase().includes(term) ||
        r.id.toLowerCase().includes(term)
      );
    }

    // Favorites only — use the DB-backed `favorite` field populated from
    // run_annotations. The localStorage `favorites` set is only a mirror,
    // not the source of truth.
    if ($filters.favoritesOnly) {
      result = result.filter(r => r.favorite);
    }

    // Archive visibility: hide archived by default, toggle via archiveView.
    if ($filters.archiveView === 'hide') {
      result = result.filter(r => !r.archivedAt);
    } else if ($filters.archiveView === 'only') {
      result = result.filter(r => !!r.archivedAt);
    }

    // Status chip filter: [] means "all statuses", otherwise keep only
    // runs whose status matches one of the selected chips.
    if ($filters.statuses.length > 0) {
      const statusSet = new Set<string>($filters.statuses);
      result = result.filter(r => statusSet.has(r.status));
    }

    // Sort by timestamp descending (newest first)
    return result.sort((a, b) => b.timestamp - a.timestamp);
  }
);

// ---------- Derived: pathRows ----------

/**
 * The Lineages view's rows, over the one relation and the shared hidden-run
 * rule. Every filtered run is visible here, cache-hit rows included. A
 * visible run attaches to its nearest visible ancestors through the runs a
 * filter hides, so hiding a middle run joins its path instead of splitting
 * it. A terminal is a visible run with no visible child; its row is the
 * terminal plus every visible ancestor reached through that attachment, in
 * execution order.
 *
 * Membership comes from the input edges alone, not the resolved upstreams:
 * a re-run's row holds the re-run's own runs, and the originals it reused
 * keep their own row instead of being repeated inside it. The reuse edge
 * stays on each record, where ``cacheSourceRunId`` is the CACHED label a
 * row node draws — so a re-run in which every run was a cache hit still
 * gets a row of its own, cached from end to end, rather than dissolving
 * into the row it mirrors.
 *
 * The Inspector's per-slot parent chips remain the place to see "who fed
 * this exact slot"; a row shows "what had to run before this terminal".
 */
export const pathRows = derived([filteredRuns, runs], ([$filtered, $runs]) => {
  const allMap = new Map<string, WfcRun>();
  for (const r of $runs) allMap.set(r.id, r);
  const nearest = nearestVisibleAncestors(
    allMap, new Set($filtered.map(r => r.id)), inputEdgesOf,
  );

  // Terminals: visible runs that no visible run attaches to.
  const attachedTo = new Set<string>();
  for (const r of $filtered) {
    for (const a of nearest(r.id)) attachedTo.add(a);
  }
  const terminals = $filtered.filter(r => !attachedTo.has(r.id));

  const rows: PathRow[] = [];
  let pathId = 1;

  for (const terminal of terminals) {
    const members = new Set<string>([terminal.id]);
    const queue: string[] = [terminal.id];
    while (queue.length > 0) {
      for (const a of nearest(queue.shift()!)) {
        if (members.has(a)) continue;
        members.add(a);
        queue.push(a);
      }
    }

    const path = [...members]
      .map(id => allMap.get(id)!)
      .filter(Boolean)
      .sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));

    rows.push({ pathId: pathId++, nodes: path });
  }

  // Sort rows by timestamp of root node (newest first)
  rows.sort((a, b) => {
    const aTime = a.nodes[0]?.timestamp ?? 0;
    const bTime = b.nodes[0]?.timestamp ?? 0;
    return bTime - aTime;
  });

  return rows;
});

// ---------- Actions ----------

export async function loadRuns(): Promise<void> {
  loading.set(true);
  error.set(null);
  // Clear the effectiveSample cache on each load
  effectiveSampleCache = new Map();
  try {
    const [allRuns, mods, meths] = await Promise.all([
      fetchRuns(),
      fetchModules(),
      fetchMethods(),
    ]);
    runs.set(allRuns);
    availableModules.set(mods.sort());
    methodInfos.set(meths);
  } catch (err) {
    error.set(err instanceof Error ? err.message : String(err));
  } finally {
    loading.set(false);
  }
}

export function selectRun(runId: string | null): void {
  selectedRunId.set(runId);
}

export function toggleRunSelection(runId: string): void {
  selectedRunIds.update(ids => {
    const next = new Set(ids);
    if (next.has(runId)) {
      next.delete(runId);
    } else {
      next.add(runId);
    }
    return next;
  });
}

export function clearSelection(): void {
  selectedRunIds.set(new Set());
}

// ---------- Optimistic mutations (backend stubs) ----------

function patchRun(runId: string, patch: Partial<WfcRun>): void {
  runs.update(rs => rs.map(r => (r.id === runId ? { ...r, ...patch } : r)));
}

/**
 * Favorite/unfavorite a run. Updates the local store + localStorage mirror
 * immediately; reverts on API rejection.
 */
export async function setFavoriteOptimistic(runId: string, favorite: boolean): Promise<void> {
  const prev = get(runs).find(r => r.id === runId)?.favorite ?? false;
  patchRun(runId, { favorite });
  favorites.update(f => {
    const next = new Set(f);
    if (favorite) next.add(runId); else next.delete(runId);
    saveFavorites(next);
    return next;
  });
  try {
    await apiFavoriteRun(runId, favorite);
  } catch (err) {
    patchRun(runId, { favorite: prev });
    favorites.update(f => {
      const next = new Set(f);
      if (prev) next.add(runId); else next.delete(runId);
      saveFavorites(next);
      return next;
    });
    throw err;
  }
}

/**
 * Rename a run. The label lives in `Run.nid` server-side (see backend
 * PATCH endpoint). Writes optimistically to the local store's `nid`
 * (and mirrors into `name` for convenience); reverts on rejection.
 */
export async function renameRunOptimistic(runId: string, name: string): Promise<void> {
  const cur = get(runs).find(r => r.id === runId);
  const prevNid = cur?.nid ?? '';
  const prevName = cur?.name ?? null;
  patchRun(runId, { nid: name, name });
  try {
    await apiRenameRun(runId, name);
  } catch (err) {
    patchRun(runId, { nid: prevNid, name: prevName });
    throw err;
  }
}

/**
 * Archive or unarchive a run. Writes the new archivedAt locally, then
 * calls the API; reverts on rejection.
 */
export async function setArchivedOptimistic(runId: string, archived: boolean): Promise<void> {
  const prev = get(runs).find(r => r.id === runId)?.archivedAt ?? null;
  patchRun(runId, { archivedAt: archived ? Date.now() : null });
  try {
    await apiSetArchived(runId, archived);
  } catch (err) {
    patchRun(runId, { archivedAt: prev });
    throw err;
  }
}

/**
 * Delete a run. Removes from the local store immediately and clears the
 * selection; restores the run on API rejection.
 */
export async function deleteRunOptimistic(runId: string): Promise<void> {
  const current = get(runs);
  const idx = current.findIndex(r => r.id === runId);
  if (idx === -1) return;
  const removed = current[idx];
  runs.set(current.filter(r => r.id !== runId));
  if (get(selectedRunId) === runId) selectedRunId.set(null);
  try {
    await apiDeleteRun(runId);
  } catch (err) {
    runs.update(rs => {
      const copy = [...rs];
      copy.splice(idx, 0, removed);
      return copy;
    });
    throw err;
  }
}

/**
 * Set the module filter. Cascade-prunes dependent selections: selected
 * methods that don't belong to the module and selected samples the
 * narrowed dropdown no longer offers are dropped, so no invisible
 * filter can remain active.
 */
export function setModuleFilter(module: string): void {
  filters.update(f => {
    let methods = f.methods;
    if (module) {
      const valid = new Set(
        get(methodInfos).filter(m => m.module === module).map(m => m.name),
      );
      methods = f.methods.filter(m => valid.has(m));
    }
    return { ...f, module, methods };
  });
  pruneSampleSelection();
}

/** Toggle a method filter selection, then cascade-prune samples. */
export function toggleMethodFilter(method: string): void {
  filters.update(f => {
    const methods = f.methods.includes(method)
      ? f.methods.filter(m => m !== method)
      : [...f.methods, method];
    return { ...f, methods };
  });
  pruneSampleSelection();
}

/** Drop selected samples that the narrowed dropdown no longer offers. */
function pruneSampleSelection(): void {
  const avail = new Set(get(availableSamples));
  filters.update(f =>
    f.sample.every(s => avail.has(s))
      ? f
      : { ...f, sample: f.sample.filter(s => avail.has(s)) },
  );
}

export function resetFilters(): void {
  filters.set({
    timeRange: 'all',
    module: '',
    methods: [],
    sample: [],
    searchText: '',
    favoritesOnly: false,
    selectMode: false,
    archiveView: 'hide',
    statuses: [],
  });
}

// ---------- Load-in-Canvas: Pipelines view ----------

export type HistoryViewMode = 'pipelines' | 'lineages' | 'descendants';

/** Which top-level History view is showing. Default Descendants. */
export const historyView = writable<HistoryViewMode>('descendants');

/** Set of pipeline_id values whose child run rows are currently expanded. */
export const expandedPipelineIds = writable<Set<string>>(new Set());

/**
 * Highlighted run id within the Pipelines view. Used by cross-nav from
 * RunDetailPanel meta-row to flash the relevant child row when switching
 * away from Lineages.
 */
export const highlightedRunId = writable<string | null>(null);

export interface PipelineRowSummary {
  pipelineId: string;
  /**
   * Display name: the pipeline name given at submission, falling back to
   * the short pipeline id when no run in the group carries one. Never
   * derived from a child run's method, which would give every card in a
   * group of same-shaped pipelines the same first-method label.
   */
  name: string;
  /** Aggregate status: running > failed > cancelled > success > unknown. */
  status: string;
  /** Number of completed child runs. */
  done: number;
  /** Total child runs (running + done + failed + cancelled + pending). */
  total: number;
  /** Distinct sample count across child runs (excluding the collapsed-sample sentinel). */
  sampleCount: number;
  /** Earliest started_at across child runs. */
  started: number;
  /** Child runs that were cache hits (cacheSourceRunId set). */
  cachedCount: number;
  runs: WfcRun[];
}

function rollupStatus(runs: WfcRun[]): string {
  // Priority: running > pending > failed > cancelled > success > unknown.
  const statuses = new Set(runs.map(r => r.status));
  if (statuses.has('running')) return 'running';
  if (statuses.has('pending')) return 'running';
  if (statuses.has('failed')) return 'failed';
  if (statuses.has('cancelled')) return 'cancelled';
  if (statuses.has('success')) return 'success';
  return 'unknown';
}

/**
 * Group runs by pipelineId into one row per pipeline. Used by PipelinesView
 * as the top-level list. Sort: most recent first by ``started``.
 */
export const pipelineRuns = derived(runs, ($runs): PipelineRowSummary[] => {
  const groups = new Map<string, WfcRun[]>();
  for (const r of $runs) {
    if (!r.pipelineId) continue;
    const arr = groups.get(r.pipelineId) ?? [];
    arr.push(r);
    groups.set(r.pipelineId, arr);
  }
  const rows: PipelineRowSummary[] = [];
  for (const [pid, group] of groups.entries()) {
    const samples = new Set(
      group
        .map(r => r.dataSource)
        .filter(s => !!s && s !== COLLAPSED_SAMPLE),
    );
    const done = group.filter(
      r => r.status === 'success' || r.status === 'failed' || r.status === 'cancelled',
    ).length;
    const started = group.reduce(
      (min, r) => (r.timestamp && (!min || r.timestamp < min) ? r.timestamp : min),
      0,
    );
    rows.push({
      pipelineId: pid,
      name: group.map(r => r.pipelineName).find(n => !!n) ?? pid.slice(0, 8),
      status: rollupStatus(group),
      done,
      total: group.length,
      sampleCount: samples.size,
      started,
      cachedCount: group.filter(r => !!r.cacheSourceRunId).length,
      runs: group,
    });
  }
  rows.sort((a, b) => (b.started || 0) - (a.started || 0));
  return rows;
});

/**
 * Derived: returns the current canvas's pipelineId IF it has any
 * running/pending runs, else null. The running-block is scoped
 * to the current canvas's pipelineId — not a global "anything running"
 * flag.
 *
 * Returns a function so callers can pass the *current* canvas pipelineId
 * (which lives in the Builder's stores, not historyStore).
 */
export function runningPipelineId(currentPipelineId: string | null): string | null {
  if (!currentPipelineId) return null;
  const $runs = get(runs);
  const blocked = $runs.some(
    r => r.pipelineId === currentPipelineId &&
      (r.status === 'running' || r.status === 'pending'),
  );
  return blocked ? currentPipelineId : null;
}

/**
 * Cross-navigate from RunDetailPanel meta-row → Pipelines view. Switches
 * the view, expands the target pipeline row, and highlights the run.
 * Already on Pipelines, it is still not a no-op: the row still expands
 * and the run still highlights.
 */
export function jumpToPipelineRun(pipelineId: string, runId: string): void {
  historyView.set('pipelines');
  expandedPipelineIds.update(s => {
    const next = new Set(s);
    next.add(pipelineId);
    return next;
  });
  highlightedRunId.set(runId);
}

export function togglePipelineExpanded(pipelineId: string): void {
  expandedPipelineIds.update(s => {
    const next = new Set(s);
    if (next.has(pipelineId)) next.delete(pipelineId); else next.add(pipelineId);
    return next;
  });
}

// ---------- Derived: status bucket counts ----------

/**
 * Count of runs per status across all non-status filters (time, module,
 * methods, sample, archive, searchText, favoritesOnly). The status-chip
 * filter itself is intentionally NOT applied here -- otherwise toggling a
 * chip would self-zero its own bucket. Used for the history header summary
 * so the counts always match the visible row count for the chosen filters.
 */
export const statusBuckets = derived(
  [runs, filters],
  ([$runs, $filters]) => {
    let result = $runs;
    if ($filters.timeRange !== 'all') {
      const cutoff = timeRangeMs($filters.timeRange);
      result = result.filter(r => r.timestamp >= cutoff);
    }
    if ($filters.module) {
      result = result.filter(r => r.module === $filters.module);
    }
    if ($filters.methods.length > 0) {
      const methodSet = new Set($filters.methods);
      result = result.filter(r => methodSet.has(r.method));
    }
    if ($filters.sample.length > 0) {
      const sampleSet = new Set($filters.sample);
      result = result.filter(r => {
        const es = effectiveSample(r, $runs);
        return es !== null && sampleSet.has(es);
      });
    }
    if ($filters.searchText) {
      const term = $filters.searchText.toLowerCase();
      result = result.filter(r =>
        r.runName.toLowerCase().includes(term) ||
        r.method.toLowerCase().includes(term) ||
        r.module.toLowerCase().includes(term) ||
        r.dataSource.toLowerCase().includes(term) ||
        r.id.toLowerCase().includes(term)
      );
    }
    if ($filters.favoritesOnly) {
      result = result.filter(r => r.favorite);
    }
    if ($filters.archiveView === 'hide') {
      result = result.filter(r => !r.archivedAt);
    } else if ($filters.archiveView === 'only') {
      result = result.filter(r => !!r.archivedAt);
    }
    const buckets: Record<RunStatusFilter | 'cached', number> = {
      success: 0, failed: 0, running: 0, cancelled: 0, cached: 0,
    };
    for (const r of result) {
      if (r.status === 'success' || r.status === 'failed' ||
          r.status === 'running' || r.status === 'cancelled') {
        buckets[r.status as RunStatusFilter] += 1;
      }
      // Informational overlap: a cache-hit run counts in BOTH its status
      // bucket (success) and here — "11 success · 3 cached" means 3 of
      // the 11 successes were cache hits.
      if (r.cacheSourceRunId) {
        buckets.cached += 1;
      }
    }
    return buckets;
  }
);

// ---------- Descendants view: forest derivation + collapse state ----------

export interface DescTreeNode {
  /**
   * The entry's position in the forest: the run ids from its section root
   * down to it, joined by '>'. A run with several parents has one entry
   * under each of them, so the key, not the run id, names an entry; the
   * view keys its rows and its fold state on it.
   */
  key: string;
  run: WfcRun;
  /** The run's visible parent count; above one the view draws a pill. */
  parentCount: number;
  children: DescTreeNode[];
}

export interface DescendantSection {
  /** Section label — effectiveSample of the roots (null when unknown). */
  sample: string | null;
  /** Top-level trees, in the forest's own order (see forestOrder). */
  roots: DescTreeNode[];
}

/**
 * The Descendants forest's sibling order: method, then node id, then run
 * id, each compared numerically where digits run. It reads no timestamp,
 * so the forest does not depend on which run started first.
 */
function forestOrder(a: WfcRun, b: WfcRun): number {
  const cmp = (x: string, y: string) => x.localeCompare(y, 'en', { numeric: true });
  return cmp(a.method ?? '', b.method ?? '')
    || cmp(a.nid ?? '', b.nid ?? '')
    || cmp(a.id, b.id);
}

/**
 * Per-root forest of what actually executed, derived from filteredRuns.
 *
 * Cache-hit runs (cacheSourceRunId != null) are excluded from display
 * entirely; so are runs removed by any filter. Exclusion never invents
 * structure: a hidden run's non-hidden children re-attach to their nearest
 * visible ancestors (the shared hidden-run rule, walking the resolved
 * upstreams through the full run list), or become section top-level trees
 * when the whole upstream chain is hidden. That set is then reduced: a
 * member that is an ancestor of another member is not a parent.
 *
 * A run with several parents gets an equal, full entry under each of them,
 * its whole subtree included, and no copy is primary. Each entry is keyed
 * by its position, so copies fold independently. Nothing here reads a
 * timestamp: placement and order are the same whichever run started
 * first. Siblings follow forestOrder; sections follow their sample label,
 * the unlabelled section last.
 */
export const descendantForest = derived(
  [filteredRuns, runs],
  ([$filtered, $runs]): DescendantSection[] => {
    const allMap = new Map<string, WfcRun>();
    for (const r of $runs) allMap.set(r.id, r);

    const visible = $filtered.filter(r => !r.cacheSourceRunId);
    const visibleSet = new Set(visible.map(r => r.id));

    // The shared hidden-run rule. A hidden cache-hit run's resolved
    // upstreams end with the run it reused, so a cache-fed executed run
    // re-anchors under the run its inputs actually came from (its "cache
    // point"), while still promoting through a cache hit that sits directly
    // above a visible executed ancestor.
    const parentsOf = visibleParents(nearestVisibleAncestors(allMap, visibleSet));

    const parentCount = new Map<string, number>();
    const childrenMap = new Map<string, WfcRun[]>();
    const rootRuns: WfcRun[] = [];
    for (const r of visible) {
      const parents = parentsOf(r.id);
      parentCount.set(r.id, parents.size);
      if (parents.size === 0) {
        rootRuns.push(r);
      } else {
        for (const pid of parents) {
          const arr = childrenMap.get(pid) ?? [];
          arr.push(r);
          childrenMap.set(pid, arr);
        }
      }
    }

    // Each entry carries its whole subtree. The path guard stops a
    // malformed cyclic history from recursing forever.
    const onPath = new Set<string>();
    function build(run: WfcRun, key: string): DescTreeNode {
      onPath.add(run.id);
      const kids = (childrenMap.get(run.id) ?? [])
        .filter(k => !onPath.has(k.id))
        .sort(forestOrder);
      const children = kids.map(k => build(k, `${key}>${k.id}`));
      onPath.delete(run.id);
      return { key, run, parentCount: parentCount.get(run.id) ?? 0, children };
    }

    rootRuns.sort(forestOrder);
    const sections = new Map<string, DescendantSection>();
    for (const root of rootRuns) {
      const sample = effectiveSample(root, $runs);
      const key = sample ?? '';
      let sec = sections.get(key);
      if (!sec) {
        sec = { sample, roots: [] };
        sections.set(key, sec);
      }
      sec.roots.push(build(root, root.id));
    }

    const result = [...sections.values()];
    result.sort((a, b) => {
      if (a.sample === null || b.sample === null) {
        return (a.sample === null ? 1 : 0) - (b.sample === null ? 1 : 0);
      }
      return a.sample.localeCompare(b.sample, 'en', { numeric: true });
    });
    return result;
  }
);

/** The run whose subtree the Descendants view is showing; null = all. */
export const descendantScope = writable<string | null>(null);

/**
 * Scope the Descendants view to one run's subtree — what "→ Descendants"
 * in the run detail panel does. The view is switched as well as the scope
 * set, because that panel is open beside whichever view is showing.
 */
export function scopeDescendantsTo(runId: string): void {
  descendantScope.set(runId);
  historyView.set('descendants');
}

/** Drop the scope, so the view shows every section again. */
export function clearDescendantScope(): void {
  descendantScope.set(null);
}

/**
 * The Descendants view's sections, pruned to the scoped run's subtree.
 *
 * Unscoped this is ``descendantForest`` itself; scoped it is the one
 * section holding the scoped run, with that run as its only root. Pruning
 * the forest rather than deriving a second tree is what makes the scoped
 * view agree with the unscoped one: it inherits both exclusions, so it
 * shows no cache-hit copies and nothing a filter hides. A run this forest
 * does not hold — a cache-hit run, or one a filter removed — therefore
 * scopes to nothing, and the view draws its own empty state. Every entry of
 * a run with several parents carries the same full subtree, so scoping to
 * one of its parents shows it whole, and scoping to the run itself takes
 * its first entry.
 */
export const scopedDescendantForest = derived(
  [descendantForest, descendantScope],
  ([$forest, $scope]): DescendantSection[] => {
    if (!$scope) return $forest;
    const find = (nodes: DescTreeNode[]): DescTreeNode | null => {
      for (const n of nodes) {
        if (n.run.id === $scope) return n;
        const hit = find(n.children);
        if (hit) return hit;
      }
      return null;
    };
    for (const sec of $forest) {
      const hit = find(sec.roots);
      if (hit) return [{ sample: sec.sample, roots: [hit] }];
    }
    return [];
  }
);

/**
 * Entry keys (DescTreeNode.key) whose subtree is collapsed in the
 * Descendants view. Keyed by position, not run id, so folding one entry of
 * a run with several parents leaves its other entries open.
 */
export const descendantsCollapsed = writable<Set<string>>(new Set());

export function toggleDescendantCollapsed(key: string): void {
  descendantsCollapsed.update(s => {
    const next = new Set(s);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
}

/** Collapse every expandable entry in every section. */
export function collapseAllDescendants(): void {
  const keys = new Set<string>();
  const walk = (n: DescTreeNode): void => {
    if (n.children.length > 0) {
      keys.add(n.key);
      n.children.forEach(walk);
    }
  };
  for (const sec of get(descendantForest)) sec.roots.forEach(walk);
  descendantsCollapsed.set(keys);
}

/** One drawn row of the Descendants view. */
export interface DescRow {
  node: DescTreeNode;
  /** Nesting depth; roots are 0. */
  depth: number;
  /** Whether the entry is the last of its siblings (the └ connector). */
  isLast: boolean;
  /** Whether this entry's subtree is folded away. */
  collapsed: boolean;
}

/**
 * The rows the Descendants view draws for a list of trees, depth first,
 * leaving out everything below a collapsed entry. Fold state is looked up
 * by entry key.
 *
 * Args:
 *   roots: The top-level trees of one section.
 *   collapsed: Entry keys whose subtree is folded.
 *
 * Returns:
 *   The visible rows in drawing order.
 */
export function descendantRows(roots: DescTreeNode[], collapsed: Set<string>): DescRow[] {
  const out: DescRow[] = [];
  const walk = (nodes: DescTreeNode[], depth: number): void => {
    nodes.forEach((node, i) => {
      const isCollapsed = collapsed.has(node.key);
      out.push({ node, depth, isLast: i === nodes.length - 1, collapsed: isCollapsed });
      if (!isCollapsed) walk(node.children, depth + 1);
    });
  };
  walk(roots, 0);
  return out;
}

/**
 * Run ids the Descendants view highlights: the selected run, or in select
 * mode the checked runs. Highlight goes by run id, so selecting any entry of
 * a run with several parents highlights every entry of it.
 */
export const descendantHighlight = derived(
  [selectedRunId, selectedRunIds, filters],
  ([$selected, $selectedIds, $filters]): Set<string> => {
    if ($filters.selectMode) return $selectedIds;
    return new Set($selected ? [$selected] : []);
  },
);

export function expandAllDescendants(): void {
  descendantsCollapsed.set(new Set());
}
