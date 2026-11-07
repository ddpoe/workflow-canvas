<script lang="ts">
  /**
   * History's run panel for a draft step (a draft's latest draft run): the
   * header "History › draft" with Download zip and the bundle export
   * disabled (a lineage with a draft step cannot become a rerun bundle);
   * star, Rename and Archive disabled (a draft run cannot be starred,
   * renamed or archived); the run detail hero; Overview with the draft
   * string and its copy, Method version DRAFT, the draft folder, Kept
   * until; the footer Open in Registry ↗ and View pipeline ↗. There is no
   * Open or Reference in Canvas: a draft step is not a recorded run.
   */
  import { selectRun, jumpToPipelineRun, takePendingSection, runSectionRequest } from './historyStore.js';
  import { fetchDraftStep, draftStepArtifactUrl, draftStepZipUrl } from './historyApi.js';
  import type { DraftStepDetail } from './historyApi.js';
  import { formatBytes, formatDuration, formatRelativeTime, formatTimestamp } from './historyUtils.js';
  import { formatMetric, formatParamValue } from './runDetailFormat.js';
  import {
    draftBundleRefusal,
    draftExceptionColor,
    draftOwner,
    draftPillKind,
    draftPillText,
    keptUntilText,
    pipelineLabel,
  } from './historyDrafts.js';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import { openDraftInRegistry } from '../shared/draftLinks.js';

  interface Props { draftRef: string }
  let { draftRef }: Props = $props();

  type Tab = 'overview' | 'params' | 'metrics' | 'artifacts' | 'output';
  let detail = $state<DraftStepDetail | null>(null);
  let loadError = $state<string | null>(null);
  let tab = $state<Tab>('overview');
  let toast = $state<string | null>(null);

  $effect(() => {
    const ref = draftRef;
    detail = null;
    loadError = null;
    // Overview, unless the step was opened on another tab (✗ Failed on its
    // Descendants card opens it on Output).
    tab = takePendingSection(ref);
    fetchDraftStep(ref)
      .then(d => { if (ref === draftRef) detail = d; })
      .catch(err => { loadError = err instanceof Error ? err.message : String(err); });
  });

  $effect(() => {
    // The step is already open and another view asks it for a tab: the
    // effect above does not re-run for the same step, so apply it here.
    const req = $runSectionRequest;
    if (!req || req.runId !== draftRef) return;
    tab = req.section;
    takePendingSection(draftRef);
    runSectionRequest.set(null);
  });

  let step = $derived(detail?.step ?? null);
  let dashColor = $derived(step ? draftExceptionColor(step.status) : null);
  let owner = $derived(step ? draftOwner(step) : null);

  const NO_STAR = "A draft run can't be starred, renamed or archived.";

  /** What filled each input, in words ("reads r_4 filtered -> data"). */
  function inputLine(i: DraftStepDetail['inputs'][number]): string {
    if (i.source === 'run') return `reads r_${i.runId} ${i.sourceSlot ?? ''} -> ${i.slot}`;
    if (i.source === 'draft') return `reads ${i.draftString} ${i.sourceSlot ?? ''} -> ${i.slot}`;
    return `reads sample ${i.sample} -> ${i.slot}`;
  }

  async function copyDraftString(): Promise<void> {
    if (!step) return;
    try {
      await navigator.clipboard.writeText(step.draftString);
      toast = 'Draft string copied';
    } catch {
      toast = 'Copy failed';
    }
    setTimeout(() => { toast = null; }, 1400);
  }

  function downloadZip(): void {
    if (!step) return;
    const a = document.createElement('a');
    a.href = draftStepZipUrl(step);
    a.download = `${step.method}-${step.sample}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function viewPipeline(): void {
    if (step) jumpToPipelineRun(step.pipelineId, step.draftString);
  }
</script>

<div class="detail-panel draft-step-panel" data-testid="draft-step-panel">
  <div class="detail-header">
    <span class="crumb">History <span class="crumb-sep">›</span> <b>draft</b></span>
    <div class="header-actions">
      {#if step}
        <button class="text-btn" disabled data-testid="draft-export-run"
                title={draftBundleRefusal(step)}>Export run</button>
        <button class="text-btn" data-testid="draft-download-zip" onclick={downloadZip}>⤓ Download zip</button>
      {/if}
      <button class="icon-btn" onclick={() => selectRun(null)} title="Close">×</button>
    </div>
  </div>

  {#if loadError}
    <div class="detail-error">Error: {loadError}</div>
  {:else if !detail || !step}
    <div class="detail-loading">Loading…</div>
  {:else}
    <div class="action-row">
      <button class="act" disabled title={NO_STAR}>☆</button>
      <button class="act grow" disabled title={NO_STAR}>✎ Rename</button>
      <button class="act" disabled title={NO_STAR}>Archive</button>
    </div>

    <!-- The run detail hero (style guide): module bar, a 10% module tint on
         the body, History's border rule. -->
    <div class="hero" class:dash-2={!!dashColor} data-testid="draft-hero"
         style="--module: {moduleColor(step.module)}; --dash-color: {dashColor ?? 'transparent'}; --dash-body: var(--bg-panel);">
      <div class="hero-bar" style="background: {moduleColor(step.module)};"></div>
      <div class="hero-body">
        <div class="hero-row-1">
          <span class="hist-pill draft-pill {draftPillKind(step.status)}">{draftPillText(step.status)}</span>
          {#if step.timestamp}<span class="hero-time">{formatRelativeTime(step.timestamp)}</span>{/if}
          {#if step.duration != null}<span class="hero-duration">{formatDuration(step.duration)}</span>{/if}
        </div>
        <div class="hero-method">{step.method}/{step.sample}</div>
        <div class="hero-sub">
          in <span class="hero-module">{step.module}</span> · {detail.versionText} · on <span class="hero-source">{step.sample}</span>
        </div>
      </div>
    </div>

    <div class="tabs">
      {#each [
        { key: 'overview' as Tab, label: 'Overview', count: null as number | null },
        { key: 'params' as Tab, label: 'Parameters', count: Object.keys(detail.params ?? {}).length as number | null },
        { key: 'metrics' as Tab, label: 'Metrics', count: Object.keys(detail.metrics ?? {}).length as number | null },
        { key: 'artifacts' as Tab, label: 'Artifacts', count: (detail.artifacts ?? []).length as number | null },
        { key: 'output' as Tab, label: 'Output', count: null as number | null },
      ] as t}
        <button class="tab" class:active={tab === t.key} onclick={() => { tab = t.key; }}>
          {t.label}{#if t.count !== null}<small>{t.count}</small>{/if}
        </button>
      {/each}
    </div>

    <div class="body">
      {#if tab === 'overview'}
        <div class="facts">
          <span class="k">Draft run</span>
          <span class="v draft-string"><span class="mono" data-testid="draft-string">{step.draftString}</span>
            <button class="cpy" data-testid="draft-string-copy" onclick={copyDraftString}>copy</button></span>
          <span class="k">Method version</span>
          <span class="v"><span class="vch"><span class="vk">version</span><span class="vv" data-testid="draft-method-version">{detail.versionText}</span></span></span>
          <span class="k">Draft folder</span>
          <span class="v"><span class="path-box" title={detail.folder} data-testid="draft-folder">{detail.folder}</span></span>
          {#if step.timestamp}
            <span class="k">Started</span><span class="v">{formatTimestamp(step.timestamp)}</span>
          {/if}
          <span class="k">Pipeline</span>
          <span class="v"><button class="lnk" onclick={viewPipeline}>{pipelineLabel(step.pipelineName, step.pipelineId)} ↗</button></span>
          {#if (detail.inputs ?? []).length > 0}
            <span class="k">Sample read{detail.inputs.length === 1 ? '' : 's'}</span>
            <span class="v reads">{#each detail.inputs as i (i.slot)}<span class="mono">{inputLine(i)}</span>{/each}</span>
          {/if}
          {#if (detail.outputs ?? []).length > 0}
            <span class="k">Outputs</span>
            <span class="v">{detail.outputs.map(o => `${o.filename ?? o.slot}${o.sizeBytes != null ? ` · ${formatBytes(o.sizeBytes)}` : ''}`).join(', ')}</span>
          {/if}
          <span class="k">Kept until</span>
          <span class="v kept" data-testid="draft-kept-until">{detail.keptUntil || keptUntilText(step)}</span>
        </div>
        {#if step.status === 'failed' && step.error_message}
          <div class="err">{step.error_message}</div>
        {/if}
      {:else if tab === 'params'}
        {#each Object.entries(detail.params ?? {}) as [k, v]}
          <div class="flat"><span>{k}</span><span class="mono">{formatParamValue(v)}</span></div>
        {:else}<div class="empty">No parameters.</div>{/each}
      {:else if tab === 'metrics'}
        {#each Object.entries(detail.metrics ?? {}) as [k, v]}
          <div class="flat"><span>{k}</span><span class="mono">{formatMetric(v)}</span></div>
        {:else}<div class="empty">No metrics.</div>{/each}
      {:else if tab === 'artifacts'}
        {#each detail.artifacts ?? [] as a (a.name)}
          <div class="flat"><a class="lnk" href={draftStepArtifactUrl(step.draftString, a.name)} target="_blank" rel="noopener">{a.name}</a><span class="mono">{formatBytes(a.size)}</span></div>
        {:else}<div class="empty">No files.</div>{/each}
      {:else}
        {#if detail.log}<pre class="log">{detail.log}</pre>{:else}<div class="empty">No output captured.</div>{/if}
      {/if}
    </div>

    <div class="foot">
      <button class="btn pri" data-testid="draft-open-registry"
              title="Opens {owner?.method}’s Draft run tab in the Registry"
              onclick={() => owner && openDraftInRegistry(owner.module, owner.method)}>Open in Registry ↗</button>
      <button class="btn" data-testid="draft-view-pipeline" onclick={viewPipeline}>View pipeline ↗</button>
    </div>
  {/if}
  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<style>
  .detail-panel {
    --text-secondary: var(--text-primary);
    width: 360px;
    background: var(--bg-panel);
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
    font-size: 12px;
    color: var(--text-primary);
    position: relative;
  }
  .detail-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px; background: var(--bg-header); border-bottom: 1px solid var(--border);
  }
  .crumb { font-size: 11px; }
  .crumb-sep { color: var(--text-muted); }
  .header-actions { display: flex; gap: 4px; align-items: center; }
  .text-btn, .icon-btn, .act, .btn {
    background: var(--bg-header); border: 1px solid var(--border); border-radius: 4px;
    color: var(--text-primary); font-size: 11px; padding: 3px 9px; cursor: pointer; font-family: inherit;
  }
  .icon-btn { border: none; background: none; font-size: 13px; }
  .text-btn:disabled, .act:disabled { color: var(--text-muted); background: transparent; cursor: not-allowed; }
  .action-row { display: flex; gap: 8px; margin: 12px 12px 0; }
  .act.grow { flex: 1; }
  .hero {
    position: relative; margin: 12px; background: color-mix(in srgb, var(--module) 10%, var(--bg-panel));
    border: 2px solid var(--border); border-radius: 6px; flex-shrink: 0;
  }
  .hero-bar { height: 3px; border-radius: 4px 4px 0 0; }
  .hero-body { padding: 10px 14px 12px; }
  .hero-row-1 { display: flex; align-items: center; gap: 10px; font-size: 11.5px; }
  .hero-time { color: var(--text-secondary); }
  .hero-duration { margin-left: auto; font-family: 'Consolas', monospace; }
  .hero-method { font-size: 17px; font-weight: 600; margin: 6px 0 2px; font-family: 'Consolas', monospace; word-break: break-word; }
  .hero-sub { font-size: 12px; }
  .hero-module { color: var(--module-ink); font-weight: 600; }
  .hero-source { font-family: 'Consolas', monospace; }
  .draft-pill { border-color: var(--pill); }
  .tabs { display: flex; gap: 4px; padding: 0 12px 10px; }
  .tab { flex: 1; background: none; border: 1px solid var(--border); border-radius: 3px; color: var(--text-primary); font-size: 11px; padding: 4px 2px; cursor: pointer; }
  .tab.active { border-color: var(--accent); }
  .tab small { margin-left: 4px; color: var(--text-muted); }
  .body { flex: 1; overflow-y: auto; padding: 0 12px 12px; }
  .facts { display: grid; grid-template-columns: 110px 1fr; gap: 6px 10px; align-items: center; }
  .k { color: var(--text-muted); }
  .v { min-width: 0; }
  .draft-string { display: flex; gap: 6px; align-items: center; }
  .draft-string .mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mono { font-family: 'Consolas', monospace; font-size: 11px; }
  .cpy { font-size: 10px; border: 1px solid var(--border-node); border-radius: 3px; padding: 1px 5px; background: var(--bg-header); color: var(--text-primary); cursor: pointer; }
  .vch { display: inline-flex; border: 1px solid var(--border-node); border-radius: 3px; font-family: 'Consolas', monospace; font-size: 10.5px; line-height: 17px; }
  .vk { background: var(--border); padding: 0 6px; }
  .vv { padding: 0 7px; font-weight: 700; letter-spacing: 0.04em; background: var(--bg-canvas); }
  .path-box {
    display: block; background: var(--bg-canvas); border: 1px solid var(--border); border-radius: 4px;
    padding: 4px 8px; font-family: 'Consolas', monospace; font-size: 11px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left;
  }
  .reads { display: flex; flex-direction: column; }
  .kept { color: var(--text-secondary); }
  .lnk { background: none; border: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-decoration: none; }
  .lnk:hover { text-decoration: underline; }
  .flat { display: flex; justify-content: space-between; gap: 10px; padding: 4px 0; border-bottom: 1px solid var(--border); }
  .empty { color: var(--text-muted); padding: 12px 0; }
  .err { margin-top: 10px; color: var(--status-failed-ink); }
  .log { white-space: pre-wrap; font-family: 'Consolas', monospace; font-size: 11px; }
  .foot { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--border); }
  .btn.pri { background: var(--accent); border-color: var(--accent); color: #fff; }
  .detail-error { padding: 20px; color: var(--status-failed-ink); }
  .detail-loading { padding: 20px; color: var(--text-secondary); }
  .toast { position: absolute; bottom: 56px; left: 50%; transform: translateX(-50%); background: var(--bg-header); border: 1px solid var(--border); padding: 4px 10px; border-radius: 4px; }
</style>
