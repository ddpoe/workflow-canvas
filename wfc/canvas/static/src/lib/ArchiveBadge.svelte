<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import {
    archiveStatus,
    refreshArchiveStatus,
    startArchive,
    IDLE_POLL_MS,
    ACTIVE_POLL_MS,
  } from './archiveStatus.js';

  let popoverOpen = $state(false);
  let detailsOpen = $state(false);
  let expandedRuns = $state<Set<number>>(new Set());
  let wrapEl: HTMLElement | null = $state(null);

  // Blue archiving pill wins over amber; amber is suppressed whenever a
  // pipeline is in flight (mid-run NULL hashes are normal — see design).
  const progress = $derived(
    $archiveStatus?.state === 'archiving' ? $archiveStatus.progress : null,
  );
  const unarchivedRuns = $derived($archiveStatus?.unarchived_runs ?? 0);
  const showAmber = $derived(
    progress === null && unarchivedRuns > 0 && !$archiveStatus?.pipeline_running,
  );

  // Green confirmation pill: when the poller observes an archiving → zero
  // transition, linger `✓ N runs archived` briefly instead of vanishing.
  // A pass that starts AND finishes between two polls stays invisible —
  // catching it would need a backend "last archive finished" field.
  const LINGER_MS = 4000;
  let lingerRuns = $state<number | null>(null);
  let lingerTimer: ReturnType<typeof setTimeout> | null = null;
  let lastSeenProgress: number | null = null;

  $effect(() => {
    const s = $archiveStatus;
    if (!s) return;
    if (s.state === 'archiving' && s.progress) {
      lastSeenProgress = s.progress.runs_total;
      if (lingerTimer) clearTimeout(lingerTimer);
      lingerRuns = null;
    } else if (lastSeenProgress !== null) {
      const total = lastSeenProgress;
      lastSeenProgress = null;
      // Leftovers mean the job died — amber takes over, no confirmation.
      if (s.unarchived_runs === 0) {
        lingerRuns = total;
        lingerTimer = setTimeout(() => { lingerRuns = null; }, LINGER_MS);
      }
    }
  });

  function dismissLinger() {
    if (lingerTimer) clearTimeout(lingerTimer);
    lingerRuns = null;
  }

  onMount(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (disposed) return;
      await refreshArchiveStatus();
      if (disposed) return;
      const delay =
        get(archiveStatus)?.state === 'archiving' ? ACTIVE_POLL_MS : IDLE_POLL_MS;
      timer = setTimeout(loop, delay);
    };
    loop();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      if (lingerTimer) clearTimeout(lingerTimer);
    };
  });

  function pct(done: number, total: number): number {
    return total > 0 ? (done / total) * 100 : 0;
  }

  function toggleRun(runId: number) {
    const next = new Set(expandedRuns);
    if (next.has(runId)) next.delete(runId);
    else next.add(runId);
    expandedRuns = next;
  }

  function outStatusText(status: string): string {
    if (status === 'archived') return '✓ archived';
    if (status === 'hashing') return 'hashing…';
    return status; // pending, missing, error:…
  }

  function outStatusClass(status: string): string {
    if (status === 'archived') return 'ok';
    if (status === 'hashing') return 'busy';
    if (status === 'pending') return 'pend';
    return 'bad'; // missing / error:…
  }

  function onWindowClick(e: MouseEvent) {
    // Click-off closes the popover; clicks inside it (or on the badge,
    // which has its own toggle) don't. Closing never affects the job.
    if (!popoverOpen) return;
    if (wrapEl && e.target instanceof Node && wrapEl.contains(e.target)) return;
    popoverOpen = false;
  }
</script>

<svelte:window onclick={onWindowClick} />

{#if progress || showAmber || lingerRuns !== null}
  <div class="badge-wrap" bind:this={wrapEl}>
    <button
      class="badge"
      class:busy={!!progress}
      class:warn={!progress && showAmber}
      class:done={!progress && !showAmber}
      data-testid="archive-badge"
      onclick={() => {
        if (!progress && !showAmber) dismissLinger();
        else popoverOpen = !popoverOpen;
      }}
    >
      {#if progress}
        <span class="spin"></span>
        archiving runs {progress.runs_done}/{progress.runs_total}
      {:else if showAmber}
        ⚠ {unarchivedRuns} run{unarchivedRuns === 1 ? '' : 's'} unarchived
      {:else}
        ✓ {lingerRuns} run{lingerRuns === 1 ? '' : 's'} archived
      {/if}
    </button>
    {#if popoverOpen && (progress || showAmber)}
      <div class="popover" data-testid="archive-popover">
        {#if progress}
          <div class="pop-title">Archiving run outputs…</div>
          <div class="bar"><i style="width: {pct(progress.runs_done, progress.runs_total)}%"></i></div>
          <div class="sub">{progress.runs_done} of {progress.runs_total} runs archived</div>
          {#if progress.current_output}
            <div class="cur" data-testid="archive-current-output">hashing: {progress.current_output}</div>
          {/if}
          <button
            class="toggle"
            data-testid="archive-details-toggle"
            onclick={() => { detailsOpen = !detailsOpen; }}
          >{detailsOpen ? 'Hide run details ▴' : 'Show run details ▾'}</button>
          {#if detailsOpen}
            <div class="details">
              {#each progress.per_run as run (run.run_id)}
                <div class="run" data-testid="archive-run-row">
                  <button class="runline" onclick={() => toggleRun(run.run_id)}>
                    <span class="run-label">
                      <span class="caret">{expandedRuns.has(run.run_id) ? '▾' : '▸'}</span>
                      {run.label}
                    </span>
                    <span class="run-count" class:ok={run.done >= run.total}>
                      {#if run.done >= run.total}✓ {/if}{run.done}/{run.total} outputs
                    </span>
                  </button>
                  <div class="bar mini"><i style="width: {pct(run.done, run.total)}%"></i></div>
                  {#if expandedRuns.has(run.run_id)}
                    <div class="outs">
                      {#each run.outputs as out, i (i)}
                        <div class="out" data-testid="archive-out-row">
                          <span class="oname">{out.name}</span>
                          <span class="ostatus {outStatusClass(out.status)}">{outStatusText(out.status)}</span>
                        </div>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        {:else}
          <div class="pop-title">Unarchived run outputs</div>
          <p class="explain">
            Outputs from interrupted pipelines aren't hashed into the cache
            yet — their artifacts won't appear in History until archived.
          </p>
          <div class="sub">
            {$archiveStatus?.unarchived_outputs ?? 0}
            output{($archiveStatus?.unarchived_outputs ?? 0) === 1 ? '' : 's'} across
            {unarchivedRuns} run{unarchivedRuns === 1 ? '' : 's'}
          </div>
          <button class="archive-now" data-testid="archive-now" onclick={() => startArchive()}>
            Archive now
          </button>
        {/if}
      </div>
    {/if}
  </div>
{/if}

<style>
  .badge-wrap { position: relative; }
  .badge {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    border: 1px solid;
    cursor: pointer;
    white-space: nowrap;
  }
  .badge.busy {
    background: rgba(74, 144, 217, 0.15);
    border-color: rgba(74, 144, 217, 0.5);
    color: #4A90D9;
  }
  .badge.warn {
    background: rgba(230, 175, 60, 0.12);
    border-color: rgba(230, 175, 60, 0.5);
    color: #E6AF3C;
  }
  .badge.done {
    background: rgba(80, 200, 120, 0.12);
    border-color: rgba(80, 200, 120, 0.5);
    color: #50C878;
  }
  .spin {
    display: inline-block;
    width: 11px;
    height: 11px;
    border: 2px solid rgba(74, 144, 217, 0.3);
    border-top-color: #4A90D9;
    border-radius: 50%;
    animation: sp 1s linear infinite;
    flex-shrink: 0;
  }
  @keyframes sp { to { transform: rotate(360deg); } }
  .popover {
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    width: 285px;
    background: #252526;
    border: 1px solid #3e3e42;
    border-radius: 8px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
    padding: 10px 13px;
    z-index: 1000;
    text-align: left;
    cursor: default;
  }
  .pop-title { color: #e8e8e8; font-size: 13px; font-weight: 700; }
  .explain { margin: 6px 0 4px; font-size: 12px; color: #999; line-height: 1.45; }
  .bar {
    height: 7px;
    border-radius: 999px;
    background: #1e1e1e;
    overflow: hidden;
    margin: 7px 0 4px;
    border: 1px solid #3e3e42;
  }
  .bar > i { display: block; height: 100%; background: #4A90D9; }
  .bar.mini { height: 5px; margin: 3px 0 2px; }
  .sub { font-size: 12px; color: #999; }
  .cur {
    color: #777;
    font-size: 11px;
    font-family: ui-monospace, monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 2px;
  }
  .toggle {
    margin-top: 8px;
    font-size: 12px;
    color: #4A90D9;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
  }
  .details {
    margin-top: 5px;
    padding-top: 7px;
    border-top: 1px solid #3e3e42;
    /* ~6 run rows (label line + mini bar) before scrolling. */
    max-height: 170px;
    overflow-y: auto;
  }
  .runline {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    width: 100%;
    font-size: 11px;
    margin-top: 6px;
    color: #bbb;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    gap: 8px;
  }
  .run-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: left;
  }
  .run-count { flex-shrink: 0; }
  .run-count.ok { color: #50C878; }
  .caret { color: #666; margin-right: 4px; }
  .outs {
    margin: 4px 0 2px 14px;
    border-left: 1px solid #3e3e42;
    padding-left: 9px;
  }
  .out {
    display: flex;
    justify-content: space-between;
    font-size: 10.5px;
    font-family: ui-monospace, monospace;
    color: #999;
    margin-top: 3px;
    gap: 8px;
  }
  .oname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ostatus { flex-shrink: 0; }
  .ostatus.ok { color: #50C878; }
  .ostatus.busy { color: #4A90D9; }
  .ostatus.pend { color: #666; }
  .ostatus.bad { color: #E74C3C; }
  .archive-now {
    margin-top: 8px;
    padding: 5px 12px;
    background: #4A90D9;
    color: white;
    font-size: 12px;
    font-weight: 600;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .archive-now:hover { background: #3f7fc4; }
</style>
