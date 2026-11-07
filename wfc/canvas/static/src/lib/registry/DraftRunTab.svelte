<script lang="ts">
  /**
   * An open draft's Draft run tab: its latest draft run. When it ran, in
   * which pipeline and on how many samples; its steps by status; the total
   * size and how long it is kept; one line per node with its status and
   * size, a copy of its draft string, and a click that opens the run in
   * History. Then Open in History ↗, View pipeline ↗, Download zip and
   * Delete outputs.
   *
   * A draft with no draft run says why, as the server words it.
   */
  import CopyButton from './CopyButton.svelte';
  import { STEP_WORD, fmtBytes, tallyParts, timeAgo } from './draftFormat.js';
  import { draftRunZipUrl } from './api.js';
  import type { DraftRunDetail, DraftRunNode } from './api.js';

  let {
    module,
    method,
    run,
    loading = false,
    error = null,
    onOpenHistory,
    onViewPipeline,
    onOpenStep,
    onDeleteOutputs,
  }: {
    module: string;
    method: string;
    run: DraftRunDetail | null;
    loading?: boolean;
    /** Why there is no draft run (the server's 404 detail), or a failure. */
    error?: string | null;
    onOpenHistory: (run: DraftRunDetail) => void;
    onViewPipeline: (run: DraftRunDetail) => void;
    onOpenStep: (run: DraftRunDetail, node: DraftRunNode) => void;
    onDeleteOutputs: () => void;
  } = $props();

  const parts = $derived(run ? tallyParts(run.tally ?? {}) : []);
  const samples = $derived(run?.samples?.length ?? 0);

  /** A node's draft strings: one per sample, one per line. */
  function draftStrings(node: DraftRunNode): string {
    return (node.steps ?? []).map(s => s.draftString).join('\n');
  }
</script>

{#if loading && !run}
  <div class="drt dim" data-testid="draft-run-tab">Loading the draft run…</div>
{:else if !run}
  <div class="drt dim" data-testid="draft-run-tab">
    <span data-testid="draft-run-none">{error ?? 'No draft run yet.'}</span>
    Run a pipeline that uses this draft in the Builder; its latest results are kept here.
  </div>
{:else}
  <div class="drt" data-testid="draft-run-tab">
    <div class="r"><span>Last draft run</span>
      <span data-testid="draft-run-when">{timeAgo(run.startedAt)} · pipeline <b>{run.pipelineName ?? run.pipelineId}</b> · {samples} {samples === 1 ? 'sample' : 'samples'}</span></div>
    <div class="r"><span>Status</span>
      <span data-testid="draft-run-status">
        {#each parts as p, i (p.word)}
          {#if i > 0}<span class="dim">{' · '}</span>{/if}<span class={p.cls}>{p.mark} {p.n} {p.word}</span>
        {/each}
        {#if parts.length > 0}<span class="dim">{' · '}</span>{/if}<span class="dim">latest kept, not recorded</span>
      </span></div>
    <div class="r"><span>Outputs</span>
      <span data-testid="draft-run-size">{fmtBytes(run.totalBytes)} · kept until {run.keptUntil}</span></div>
    <div class="nodes">
      {#each run.nodes ?? [] as node (node.nodeId)}
        {@const st = STEP_WORD[node.status] ?? STEP_WORD.pending}
        <div class="nrow" role="button" tabindex="0" data-testid="draft-run-node" data-node={node.nodeId}
             title="Open this step's run in History"
             onclick={() => onOpenStep(run, node)}
             onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenStep(run, node); } }}>
          <span class="nname">
            <span class="mono">{node.method}</span>
            {#if node.isDraft}<span class="pill">DRAFT</span>{/if}
          </span>
          <span class={st.cls}>{st.mark} {st.word}</span>
          <span class="mono dim">{fmtBytes(node.sizeBytes)}</span>
          <span class="copy">
            {#if (node.steps ?? []).length > 0}
              <CopyButton value={draftStrings(node)} testid="draft-string-copy"
                          label={(node.steps ?? []).length === 1 ? 'Copy draft string' : 'Copy draft strings'} />
            {/if}
          </span>
          <span class="dim">›</span>
        </div>
      {/each}
      <div class="hint">Click a step for its files, logs and parameters in the run detail.</div>
    </div>
    <div class="acts">
      <button type="button" class="lnk" data-testid="draft-open-history"
              onclick={() => onOpenHistory(run)}>Open in History ↗</button>
      <button type="button" class="lnk" data-testid="draft-view-pipeline"
              onclick={() => onViewPipeline(run)}>View pipeline ↗</button>
      <a class="btn" data-testid="draft-run-zip" href={draftRunZipUrl(module, method)}
         download={`${method}_draft_run.zip`}>⤓ Download zip</a>
      <button type="button" class="btn xr" data-testid="draft-delete-outputs"
              onclick={onDeleteOutputs}>Delete outputs</button>
    </div>
  </div>
{/if}

<style>
  .drt {
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px;
    padding: 12px 14px; font-size: 12.5px;
  }
  .drt.dim { color: var(--text-secondary); line-height: 1.6; }
  .r { display: grid; grid-template-columns: 120px 1fr; padding: 3px 0; }
  .r > span:first-child { color: var(--text-secondary); }
  .r > span:last-child { color: #ddd; }
  .r b { color: #eee; font-weight: 600; }
  .nodes { margin-top: 8px; border-top: 1px solid var(--border); padding-top: 8px; }
  .nrow {
    display: grid; grid-template-columns: minmax(0, 200px) 120px 1fr 24px 14px; align-items: center;
    padding: 5px 6px; border-radius: 4px; cursor: pointer;
  }
  .nrow:hover { background: var(--bg-header); }
  .nrow:focus-visible { outline: 1px solid var(--accent); outline-offset: -1px; }
  .nname { display: flex; align-items: center; gap: 6px; min-width: 0; color: #eee; }
  .nname .mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pill {
    font-family: Consolas, monospace; font-size: 9.5px; line-height: 14px; padding: 0 6px;
    font-weight: 700; letter-spacing: 0.04em; color: var(--marker-light); background: var(--bg-canvas);
    border: 1px solid var(--marker-dark); border-radius: 3px;
  }
  .copy { display: flex; justify-content: center; }
  .hint { font-size: 11px; color: var(--text-secondary); margin-top: 4px; }
  .acts { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
  .lnk {
    background: none; border: none; padding: 0; color: var(--accent); cursor: pointer;
    font: inherit; font-size: 12.5px; white-space: nowrap;
  }
  .lnk:hover { text-decoration: underline; }
  .btn {
    background: transparent; border: 1px solid var(--border); color: var(--text-primary);
    font-size: 11.5px; padding: 3px 9px; border-radius: 3px; cursor: pointer; text-decoration: none;
  }
  .btn:hover { background: var(--bg-header); }
  .btn.xr {
    border-color: color-mix(in srgb, var(--status-failed) 55%, transparent); color: var(--status-failed-ink);
  }
  .mono { font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px; }
  .dim { color: var(--text-secondary); }
  .ok { color: var(--status-ok); }
  .bad { color: var(--status-failed-ink); }
  .can { color: var(--status-cancelled-ink); }
  .act { color: var(--status-active); }
</style>
