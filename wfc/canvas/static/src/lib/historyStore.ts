/**
 * Reactive state management for the History view.
 * Uses svelte/store writable/derived for cross-component shared state
 * (matches the pattern in stores.ts).
 */
import { writable, derived, get } from 'svelte/store';
import type { WfcRun } from './historyApi.js';
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
export const descendantTreeRoot = writable<string | null>(null);

export const availableModules = writable<string[]>([]);
export const availableMethods = writable<string[]>([]);

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

export const favorites = writable<Set<string>>(loadFavorites());

export function toggleFavorite(runId: string): void {
  favorites.update(favs => {
    const next = new Set(favs);
    if (next.has(runId)) {
      next.delete(runId);
    } else {
      next.add(runId);
    }
    saveFavorites(next);
    return next;
  });
}

// ---------- effectiveSample ----------

/**
 * Cache for effectiveSample lookups. Cleared on each loadRuns().
 */
let effectiveSampleCache = new Map<string, string | null>();

/**
 * Walk the first-parent chain root-ward through allRuns to find the
 * root's sample. For fan-in nodes we deliberately follow ``parentRunIds[0]``
 * because the sample identity propagates from the spawning input_selector
 * and every ancestor chain leads back to the same root — picking any one
 * is fine. Cycle-safe: caps at 1000 hops. Returns null if cap is hit or
 * root has no sample.
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

  while (current && current.parentRunIds.length > 0 && hops < MAX_HOPS) {
    current = runMap.get(current.parentRunIds[0]);
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

export const availableSamples = derived(runs, ($runs) => {
  const rootSamples = new Set<string>();
  for (const r of $runs) {
    if (r.parentRunIds.length === 0 && r.dataSource) {
      rootSamples.add(r.dataSource);
    }
  }
  return Array.from(rootSamples).sort();
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
    // run_annotations. The legacy localStorage `favorites` set is kept
    // around as a mirror but is no longer the source of truth.
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

export const pathRows = derived(filteredRuns, ($filtered) => {
  // Build a lookup of filtered run ids and a parent map
  const filteredSet = new Set($filtered.map(r => r.id));
  const runMap = new Map<string, WfcRun>();
  for (const r of $filtered) runMap.set(r.id, r);

  // Find children of each run within the filtered set. Every parent in
  // ``parentRunIds`` registers this run as a child — so a run with fan-in
  // correctly appears as a child under all of its upstreams. Without this,
  // the old scalar-parent assumption made extra tree-terminals whenever
  // a diamond's "loser" parent had no other downstream.
  const childrenMap = new Map<string, string[]>();
  for (const r of $filtered) {
    for (const pid of r.parentRunIds) {
      if (!filteredSet.has(pid)) continue;
      const children = childrenMap.get(pid) || [];
      children.push(r.id);
      childrenMap.set(pid, children);
    }
  }

  // Terminals: runs that have no children in the filtered set
  const terminals = $filtered.filter(r => !childrenMap.has(r.id) || childrenMap.get(r.id)!.length === 0);

  // For each terminal, collect the FULL ancestor set (all runs that had to
  // complete before this terminal could start) and flatten it into execution
  // order. For a DAG with diamond/skip-level fan-ins the old parent[0] walk
  // would skip whichever branch wasn't on the first-parent chain — e.g., the
  // imaging pipeline's tile_export + illum_correct + segment branches were
  // invisible even though they were required ancestors of export_final. The
  // Inspector's per-slot parent chips remain the place to see "who fed this
  // exact slot"; this strip shows "what had to run before this terminal".
  const rows: PathRow[] = [];
  let pathId = 1;

  for (const terminal of terminals) {
    const visited = new Set<string>([terminal.id]);
    const queue: string[] = [terminal.id];
    while (queue.length > 0) {
      const rid = queue.shift()!;
      const r = runMap.get(rid);
      if (!r) continue;
      for (const pid of r.parentRunIds) {
        if (!filteredSet.has(pid) || visited.has(pid)) continue;
        visited.add(pid);
        queue.push(pid);
      }
    }

    const path = [...visited]
      .map(id => runMap.get(id)!)
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
    availableMethods.set(meths.map(m => m.name).sort());
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

export function showDescendants(runId: string): void {
  descendantTreeRoot.set(runId);
}

export function hideDescendants(): void {
  descendantTreeRoot.set(null);
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

export type HistoryViewMode = 'pipelines' | 'lineages';

/** Which top-level History view is showing. Default Pipelines (D-7). */
export const historyView = writable<HistoryViewMode>('pipelines');

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
  /** Aggregate status: running > failed > cancelled > success > unknown. */
  status: string;
  /** Number of completed child runs. */
  done: number;
  /** Total child runs (running + done + failed + cancelled + pending). */
  total: number;
  /** Distinct sample count across child runs (excluding the __all__ sentinel). */
  sampleCount: number;
  /** Earliest started_at across child runs. */
  started: number;
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
        .filter(s => !!s && s !== '__all__'),
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
      status: rollupStatus(group),
      done,
      total: group.length,
      sampleCount: samples.size,
      started,
      runs: group,
    });
  }
  rows.sort((a, b) => (b.started || 0) - (a.started || 0));
  return rows;
});

/**
 * Derived: returns the current canvas's pipelineId IF it has any
 * running/pending runs, else null. Per D-10 the running-block is scoped
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
 * No-op safe when already on Pipelines (the row still expands and the
 * run still highlights — SPEC says "no no-op").
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
    const buckets: Record<RunStatusFilter, number> = {
      success: 0, failed: 0, running: 0, cancelled: 0,
    };
    for (const r of result) {
      if (r.status === 'success' || r.status === 'failed' ||
          r.status === 'running' || r.status === 'cancelled') {
        buckets[r.status as RunStatusFilter] += 1;
      }
    }
    return buckets;
  }
);
