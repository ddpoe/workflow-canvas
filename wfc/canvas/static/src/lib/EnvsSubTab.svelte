<script lang="ts">
  import { diffLines, type DiffRow } from './envDiff';

  let { visible = true, filter = '' }: { visible?: boolean; filter?: string } = $props();

  type EnvRow = {
    spec: string;
    methods: string[];
    fingerprint_count: number;
    last_run_at: string | null;
    run_count: number;
  };
  type FpRow = {
    md5: string;
    first_seen: string | null;
    last_seen: string | null;
    run_count: number;
    source: 'run' | 'manual';
  };
  type RowState =
    | { kind: 'idle' }
    | { kind: 'in_flight' }
    | { kind: 'snapshotted'; is_new: boolean }
    | { kind: 'failed'; msg: string };

  let envs = $state<EnvRow[]>([]);
  let loadError = $state<string | null>(null);
  let loading = $state(false);

  // Per-row UI state.
  let rowState = $state<Record<string, RowState>>({});
  let rowTint = $state<Record<string, 'green' | 'red' | null>>({});
  let pillTimers: Record<string, number> = {};

  // Expansion.
  let expanded = $state<Set<string>>(new Set());
  let fingerprintsBySpec = $state<Record<string, FpRow[]>>({});
  let fpError = $state<Record<string, string>>({});
  let methodsTooltipOpen = $state<string | null>(null);
  let errorPanelOpen = $state<Record<string, boolean>>({});

  // Diff selection: spec -> ordered list of md5s selected (max 2).
  let diffSelection = $state<Record<string, string[]>>({});
  let diffRows = $state<Record<string, DiffRow[] | null>>({});
  let diffError = $state<Record<string, string>>({});

  // "Snapshot all" toolbar state.
  let snapshotAllProgress = $state<{ total: number; done: number } | null>(null);

  async function refresh() {
    loading = true;
    loadError = null;
    try {
      const resp = await fetch('/api/registry/envs');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
      const body = await resp.json();
      envs = body.envs ?? [];
    } catch (err) {
      loadError = String(err);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (visible) void refresh();
  });

  const q = $derived(filter.trim().toLowerCase());
  const filteredEnvs = $derived(
    q === '' ? envs : envs.filter(e => e.spec.toLowerCase().includes(q))
  );

  function fmtRelative(iso: string | null): string {
    if (!iso) return '—';
    const then = new Date(iso).getTime();
    const delta = Date.now() - then;
    if (delta < 60_000) return 'just now';
    const mins = Math.floor(delta / 60_000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 14) return `${days}d ago`;
    const weeks = Math.floor(days / 7);
    if (weeks < 8) return `${weeks}w ago`;
    const months = Math.floor(days / 30);
    return `${months}mo ago`;
  }

  function pluralize(n: number, word: string): string {
    return `${n} ${word}${n === 1 ? '' : 's'}`;
  }

  async function loadFingerprints(spec: string) {
    try {
      const resp = await fetch(`/api/registry/envs/${encodeURIComponent(spec)}/fingerprints`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
      const body = await resp.json();
      fingerprintsBySpec = { ...fingerprintsBySpec, [spec]: body.fingerprints ?? [] };
      fpError = { ...fpError, [spec]: '' };
    } catch (err) {
      fpError = { ...fpError, [spec]: String(err) };
    }
  }

  function toggleExpand(spec: string) {
    const next = new Set(expanded);
    if (next.has(spec)) {
      next.delete(spec);
    } else {
      next.add(spec);
      if (!fingerprintsBySpec[spec]) void loadFingerprints(spec);
    }
    expanded = next;
    // Interacting with the row clears tint + error panel.
    if (rowTint[spec]) rowTint = { ...rowTint, [spec]: null };
    errorPanelOpen = { ...errorPanelOpen, [spec]: false };
  }

  function schedulePillRevert(spec: string) {
    if (pillTimers[spec]) clearTimeout(pillTimers[spec]);
    pillTimers[spec] = window.setTimeout(() => {
      rowState = { ...rowState, [spec]: { kind: 'idle' } };
      delete pillTimers[spec];
    }, 4000);
  }

  async function snapshotOne(spec: string) {
    // If mid-flight already, ignore (double-click guard for THIS row).
    if (rowState[spec]?.kind === 'in_flight') return;
    rowState = { ...rowState, [spec]: { kind: 'in_flight' } };
    // Clicking the button counts as interaction: clear tint + error panel.
    rowTint = { ...rowTint, [spec]: null };
    errorPanelOpen = { ...errorPanelOpen, [spec]: false };

    try {
      const resp = await fetch(
        `/api/registry/envs/${encodeURIComponent(spec)}/snapshot`,
        { method: 'POST' },
      );
      const body = await resp.json();
      if (!resp.ok) {
        rowState = { ...rowState, [spec]: { kind: 'failed', msg: body.detail ?? String(body) } };
        rowTint = { ...rowTint, [spec]: 'red' };
        return;
      }
      rowState = { ...rowState, [spec]: { kind: 'snapshotted', is_new: body.is_new } };
      if (body.is_new) rowTint = { ...rowTint, [spec]: 'green' };
      // Refresh the main list so fingerprint_count + last_run stay honest.
      // (last_run_at doesn't change from a snapshot — no Run row created —
      // but fingerprint_count may tick up and we keep the UI in step.)
      void refresh();
      // Invalidate cached fingerprints for this spec so the expanded view
      // re-fetches on next open.
      if (fingerprintsBySpec[spec]) {
        delete fingerprintsBySpec[spec];
        fingerprintsBySpec = { ...fingerprintsBySpec };
        if (expanded.has(spec)) void loadFingerprints(spec);
      }
      schedulePillRevert(spec);
    } catch (err) {
      rowState = { ...rowState, [spec]: { kind: 'failed', msg: String(err) } };
      rowTint = { ...rowTint, [spec]: 'red' };
    }
  }

  async function snapshotAll() {
    const targets = filteredEnvs.map(e => e.spec);
    snapshotAllProgress = { total: targets.length, done: 0 };
    await Promise.all(targets.map(async spec => {
      await snapshotOne(spec);
      if (snapshotAllProgress) {
        snapshotAllProgress = { ...snapshotAllProgress, done: snapshotAllProgress.done + 1 };
      }
    }));
    snapshotAllProgress = null;
  }

  function toggleDiffSelect(spec: string, md5: string) {
    const cur = diffSelection[spec] ?? [];
    let next: string[];
    if (cur.includes(md5)) {
      next = cur.filter(m => m !== md5);
    } else if (cur.length >= 2) {
      // Drop the oldest, push the new — max 2 selections.
      next = [cur[1], md5];
    } else {
      next = [...cur, md5];
    }
    diffSelection = { ...diffSelection, [spec]: next };
    // Clear any previous diff for this spec when selection changes.
    diffRows = { ...diffRows, [spec]: null };
    diffError = { ...diffError, [spec]: '' };
  }

  async function runDiff(spec: string) {
    const sel = diffSelection[spec] ?? [];
    if (sel.length !== 2) return;
    try {
      const [a, b] = await Promise.all(sel.map(md5 =>
        fetch(`/api/registry/envs/blob/${md5}`).then(async r => {
          if (!r.ok) throw new Error(`blob ${md5}: HTTP ${r.status}: ${await r.text()}`);
          return r.text();
        })
      ));
      diffRows = { ...diffRows, [spec]: diffLines(a, b) };
    } catch (err) {
      diffError = { ...diffError, [spec]: String(err) };
    }
  }

  async function viewBlob(md5: string) {
    // Open in a new tab; server returns text/plain. Keeps the tab simple —
    // in-tab modal can come later if we want richer rendering.
    window.open(`/api/registry/envs/blob/${md5}`, '_blank');
  }

  function toggleMethodsTooltip(spec: string) {
    methodsTooltipOpen = methodsTooltipOpen === spec ? null : spec;
  }

  function onMethodClick(_methodPath: string) {
    // Future: emit an event to RegistryTab to jump to the Methods sub-tab
    // filtered to this method. Deferred — this is a small UX polish.
  }
</script>

<div class="envs" style:display={visible ? 'block' : 'none'}>
  <div class="toolbar">
    <button
      class="btn-primary"
      disabled={snapshotAllProgress !== null || filteredEnvs.length === 0}
      onclick={snapshotAll}
      title="Capture current env content for every listed env"
    >
      {#if snapshotAllProgress}
        <span class="spinner"></span>
        snapshotting {snapshotAllProgress.done}/{snapshotAllProgress.total}…
      {:else}
        ⟳ Snapshot all
      {/if}
    </button>
  </div>

  {#if loadError}
    <div class="error">Failed to load envs: {loadError}</div>
  {:else if loading && envs.length === 0}
    <div class="empty">Loading…</div>
  {:else if filteredEnvs.length === 0}
    <div class="empty">No envs registered.</div>
  {:else}
    <div class="body">
      <table class="reg-table">
        <thead>
          <tr>
            <th class="spec-header">Env Spec</th>
            <th>Methods</th>
            <th>Fingerprints</th>
            <th>Last Run</th>
            <th>Runs</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each filteredEnvs as env}
            {@const state = rowState[env.spec] ?? { kind: 'idle' }}
            {@const tint = rowTint[env.spec]}
            {@const isExpanded = expanded.has(env.spec)}
            <tr
              class="env-row"
              class:expanded={isExpanded}
              class:tint-green={tint === 'green'}
              class:tint-red={tint === 'red'}
              onclick={() => toggleExpand(env.spec)}
            >
              <td class="mono">
                <span class="chevron">{isExpanded ? '▼' : '▶'}</span>
                <span class="chip chip-env">{env.spec}</span>
              </td>
              <td onclick={(e) => { e.stopPropagation(); toggleMethodsTooltip(env.spec); }}>
                <span class="methods-count">{pluralize(env.methods.length, 'method')}</span>
                {#if methodsTooltipOpen === env.spec}
                  <div class="methods-tooltip">
                    <div class="label">Methods using {env.spec}</div>
                    {#each env.methods as m}
                      <div class="tooltip-method" onclick={() => onMethodClick(m)}>{m}</div>
                    {/each}
                  </div>
                {/if}
              </td>
              <td>
                <span class="chip chip-count">{pluralize(env.fingerprint_count, 'fingerprint')}</span>
                {#if state.kind === 'snapshotted' && state.is_new}
                  <span class="fp-new">+1</span>
                {/if}
              </td>
              <td class="mono dim">{fmtRelative(env.last_run_at)}</td>
              <td class="mono dim">{env.run_count}</td>
              <td onclick={(e) => e.stopPropagation()}>
                {#if state.kind === 'in_flight'}
                  <span class="pill pill-busy"><span class="spinner"></span> snapshotting…</span>
                {:else if state.kind === 'snapshotted' && state.is_new}
                  <span class="pill pill-ok">✓ snapshotted</span>
                {:else if state.kind === 'snapshotted'}
                  <span class="pill pill-dim">✓ unchanged</span>
                {:else if state.kind === 'failed'}
                  <span
                    class="pill pill-err"
                    title={state.msg}
                    onclick={() => { errorPanelOpen = { ...errorPanelOpen, [env.spec]: !errorPanelOpen[env.spec] }; }}
                  >⚠ failed</span>
                {:else}
                  <button class="pill pill-ghost" onclick={() => snapshotOne(env.spec)}>⟳ snapshot</button>
                {/if}
              </td>
            </tr>

            {#if state.kind === 'failed' && errorPanelOpen[env.spec]}
              <tr class="detail-row error-detail">
                <td colspan="6">
                  <div class="label">Error</div>
                  <pre class="code-block">{state.msg}</pre>
                </td>
              </tr>
            {/if}

            {#if isExpanded}
              <tr class="detail-row">
                <td colspan="6">
                  {#if fpError[env.spec]}
                    <div class="error">{fpError[env.spec]}</div>
                  {:else if !fingerprintsBySpec[env.spec]}
                    <div class="dim">Loading fingerprints…</div>
                  {:else if fingerprintsBySpec[env.spec].length === 0}
                    <div class="dim">No fingerprints recorded yet — click <code>⟳ snapshot</code> above to capture the current env state, or run a method that uses this env.</div>
                  {:else}
                    {@const fps = fingerprintsBySpec[env.spec]}
                    {@const sel = diffSelection[env.spec] ?? []}
                    <div class="label">Fingerprint History</div>
                    <table class="fp-table">
                      <thead>
                        <tr>
                          <th></th>
                          <th>md5</th>
                          <th>First Seen</th>
                          <th>Last Seen</th>
                          <th>Runs</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {#each fps as fp}
                          <tr>
                            <td>
                              <input
                                type="checkbox"
                                checked={sel.includes(fp.md5)}
                                onchange={() => toggleDiffSelect(env.spec, fp.md5)}
                              />
                            </td>
                            <td class="mono">{fp.md5.slice(0, 10)}…</td>
                            <td class="mono dim">{fmtRelative(fp.first_seen)}</td>
                            <td class="mono dim">{fmtRelative(fp.last_seen)}</td>
                            <td class="mono dim">{fp.run_count}</td>
                            <td><button class="link" onclick={() => viewBlob(fp.md5)}>view blob →</button></td>
                          </tr>
                        {/each}
                      </tbody>
                    </table>

                    <div class="diff-controls">
                      <button
                        class="btn-primary"
                        disabled={sel.length !== 2}
                        onclick={() => runDiff(env.spec)}
                      >Diff selected ({sel.length})</button>
                      <span class="dim">Pick two fingerprints and click Diff.</span>
                    </div>

                    {#if diffError[env.spec]}
                      <div class="error">{diffError[env.spec]}</div>
                    {:else if diffRows[env.spec]}
                      {@const rows = diffRows[env.spec]!}
                      <div class="label">Diff · {sel[0].slice(0,10)} → {sel[1].slice(0,10)}</div>
                      <div class="code-block">
                        {#each rows as row}
                          <div class="diff-row diff-{row.kind}">{row.kind === 'add' ? '+ ' : row.kind === 'del' ? '- ' : '  '}{row.text}</div>
                        {/each}
                      </div>
                    {/if}
                  {/if}
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .envs { display: block; }
  .toolbar {
    display: flex; align-items: center; gap: 12px;
    padding: 0 0 10px 0;
    flex-shrink: 0;
    justify-content: flex-end;
  }
  .body { padding: 0; }
  .empty, .error { padding: 28px 14px; color: #888; font-size: 12.5px; }
  .error { color: #E74C3C; }

  .reg-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .reg-table thead th {
    text-align: left;
    color: #888;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    padding: 6px 10px;
    border-bottom: 1px solid #3e3e42;
  }
  .spec-header { padding-left: 33px !important; }

  .reg-table tbody td {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
    vertical-align: middle;
    position: relative;
  }
  .env-row { cursor: pointer; }
  .env-row:hover { background: #2d2d30; }
  .env-row.expanded { background: #2d2d30; }
  .env-row.tint-green td {
    background: rgba(80, 200, 120, 0.08);
    box-shadow: inset 2px 0 0 rgba(80, 200, 120, 0.5);
  }
  .env-row.tint-red td {
    background: rgba(231, 76, 60, 0.06);
    box-shadow: inset 2px 0 0 rgba(231, 76, 60, 0.5);
  }
  .chevron { color: #888; font-size: 9px; margin-right: 8px; display: inline-block; width: 8px; }

  .mono { font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px; }
  .dim { color: #888; }

  .chip {
    display: inline-block;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 2px;
    font-family: "JetBrains Mono", Consolas, monospace;
    border: 1px solid;
  }
  .chip-env    { color: #9b59b6; background: rgba(155, 89, 182, 0.14); border-color: rgba(155, 89, 182, 0.35); }
  .chip-count  { color: #ccc;    background: #1e1e1e;                  border-color: #3e3e42; }

  .fp-new { color: #50C878; font-size: 10px; margin-left: 4px; }

  .methods-count { color: #4A90D9; border-bottom: 1px dotted #4A90D9; cursor: help; padding-bottom: 1px; font-size: 11.5px; font-family: "JetBrains Mono", Consolas, monospace; }

  .methods-tooltip {
    position: absolute;
    top: 30px;
    left: 10px;
    background: #252526;
    border: 1px solid #3e3e42;
    border-radius: 4px;
    padding: 8px 12px;
    font-size: 11px;
    line-height: 1.7;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    z-index: 10;
    min-width: 200px;
  }
  .methods-tooltip .label {
    color: #888; font-size: 9px; text-transform: uppercase;
    letter-spacing: 0.6px; margin-bottom: 4px; font-weight: 600;
  }
  .tooltip-method {
    color: #4A90D9; font-family: "JetBrains Mono", Consolas, monospace;
    cursor: pointer; font-size: 11px;
  }
  .tooltip-method:hover { color: #5aa0e9; }

  .pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 8px; border-radius: 3px; font-size: 10.5px;
    border: 1px solid;
    font-family: "JetBrains Mono", Consolas, monospace;
  }
  .pill-ghost {
    border-color: #3e3e42; background: transparent; color: #ccc;
    cursor: pointer;
  }
  .pill-ghost:hover { background: #2d2d30; }
  .pill-busy {
    border-color: rgba(74, 144, 217, 0.4);
    background: rgba(74, 144, 217, 0.08);
    color: #4A90D9;
    cursor: wait;
  }
  .pill-ok {
    border-color: rgba(80, 200, 120, 0.4);
    background: rgba(80, 200, 120, 0.08);
    color: #50C878;
  }
  .pill-dim {
    border-color: #3e3e42; background: #1e1e1e; color: #888;
  }
  .pill-err {
    border-color: rgba(231, 76, 60, 0.4);
    background: rgba(231, 76, 60, 0.08);
    color: #E74C3C;
    cursor: pointer;
  }

  .btn-primary {
    background: #4A90D9;
    border: none;
    color: white;
    font-size: 12.5px;
    padding: 5px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .btn-primary:hover:not(:disabled) { background: #5aa0e9; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

  .spinner {
    display: inline-block;
    width: 10px; height: 10px;
    border: 1.5px solid rgba(74, 144, 217, 0.25);
    border-top-color: #4A90D9;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .detail-row td {
    background: #1e1e1e;
    padding: 14px 18px 18px 28px;
    border-bottom: 1px solid #3e3e42;
  }
  .error-detail td {
    background: rgba(231, 76, 60, 0.04);
  }

  .label {
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    color: #888;
    font-weight: 600;
    margin-bottom: 8px;
  }

  .fp-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11.5px;
    margin-bottom: 14px;
  }
  .fp-table thead th {
    text-align: left;
    color: #888;
    font-size: 10px;
    padding: 4px 8px;
    font-weight: 600;
  }
  .fp-table tbody td {
    padding: 6px 8px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
  }

  .link {
    background: none; border: none;
    color: #4A90D9;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 11px;
    cursor: pointer;
    padding: 0;
  }
  .link:hover { color: #5aa0e9; }

  .diff-controls {
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 14px;
  }
  .diff-controls .dim { font-size: 11px; }

  .code-block {
    background: #1a1a1a;
    border: 1px solid #3e3e42;
    border-radius: 4px;
    padding: 10px 14px;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 11px;
    line-height: 1.6;
    max-height: 420px;
    overflow: auto;
    margin: 0;
    white-space: pre-wrap;
    color: #ccc;
  }
  .diff-row { white-space: pre; }
  .diff-ctx { color: #888; }
  .diff-add { color: #50C878; background: rgba(80, 200, 120, 0.08); }
  .diff-del { color: #E74C3C; background: rgba(231, 76, 60, 0.08); }
</style>
