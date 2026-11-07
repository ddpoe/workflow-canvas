<script lang="ts">
  /**
   * An open draft in the Registry's Drafts group.
   *
   * On the left: the draft card (DRAFT VERSION OF <method> or DRAFT METHOD,
   * the version registering it adds, the folder in a path box clipped in
   * the middle with the full path on hover and a copy, "Files updated since
   * vN", and Register as vN), the collapsible Registration check (each
   * line ✓ or ✗; nothing is written; Register is disabled while a line
   * fails), then the Parameters | Draft run tabs. On the right: the draft
   * folder's files.
   *
   * No dashes and no draft colour: red only for a failed check.
   */
  import { untrack } from 'svelte';
  import LabelTag from '../shared/LabelTag.svelte';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import CopyButton from './CopyButton.svelte';
  import ContractTables from './ContractTables.svelte';
  import DraftRunTab from './DraftRunTab.svelte';
  import { highlight } from '../shared/shikiHighlight.js';
  import { clipMiddle, codeParts } from './draftFormat.js';
  import type { Contract, DraftDetail, DraftEntry, DraftRunDetail, DraftRunNode, FileEntry } from './api.js';
  import type { DraftTab } from './draftFormat.js';

  let {
    draft,
    detail = null,
    detailError = null,
    tab,
    run = null,
    runLoading = false,
    runError = null,
    onTab,
    onRegister,
    onOpenHistory,
    onViewPipeline,
    onOpenStep,
    onDeleteOutputs,
  }: {
    draft: DraftEntry;
    detail?: DraftDetail | null;
    detailError?: string | null;
    tab: DraftTab;
    run?: DraftRunDetail | null;
    runLoading?: boolean;
    runError?: string | null;
    onTab: (tab: DraftTab) => void;
    onRegister: () => void;
    onOpenHistory: (run: DraftRunDetail) => void;
    onViewPipeline: (run: DraftRunDetail) => void;
    onOpenStep: (run: DraftRunDetail, node: DraftRunNode) => void;
    onDeleteOutputs: () => void;
  } = $props();

  const check = $derived(draft.check ?? { ok: true, lines: [] });
  const okCount = $derived(check.lines.filter(l => l.ok).length);
  const failCount = $derived(check.lines.filter(l => !l.ok).length);
  const canRegister = $derived(check.ok && draft.valid !== false);
  // A failed check starts open, a passing one closed.
  let checkOpen = $state(untrack(() => !(draft.check?.ok ?? true)));

  const contractSrc = $derived(detail?.contract ?? draft.contract);
  const contract = $derived<Contract>({
    input_slots: (contractSrc?.inputs ?? {}) as Contract['input_slots'],
    output_slots: (contractSrc?.outputs ?? {}) as Contract['output_slots'],
    params_schema: contractSrc?.params_schema ?? {},
    executor: contractSrc?.executor ?? 'python',
  });

  // ---- Files ----

  let activeFile = $state<string | null>(null);
  let html = $state<Record<string, string>>({});

  $effect(() => {
    const d = detail;
    untrack(() => {
      html = {};
      activeFile = d?.files?.[0]?.name ?? null;
      if (d?.files?.[0]) void render(d.files[0]);
    });
  });

  async function render(file: FileEntry): Promise<void> {
    if (html[file.name]) return;
    let out: string;
    try {
      out = await highlight(file.content, file.language);
    } catch {
      const escaped = file.content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      out = `<pre class="shiki"><code>${escaped}</code></pre>`;
    }
    html = { ...html, [file.name]: out };
  }

  function selectFile(file: FileEntry): void {
    activeFile = file.name;
    void render(file);
  }
</script>

<div class="dpanel" data-testid="draft-panel">
  <div class="left">
    <div class="card" data-testid="draft-card">
      <div class="k">
        {#if draft.kind === 'version'}
          <span>DRAFT VERSION OF {draft.method}</span>
        {:else}
          <span>DRAFT METHOD</span>
        {/if}
        <small>registers as v{draft.registersAs}</small>
      </div>
      <div class="tx">
        {#if draft.kind === 'version'}
          Runs the method folder as it is now.
          {#if draft.filesUpdated}
            <span data-testid="draft-files-updated"><b>Files updated</b> since v{draft.newestLabel}.</span>
          {:else}
            No files changed since v{draft.newestLabel}.
          {/if}
        {:else}
          Not registered yet. Joins module
          <LabelTag kind="module" label="MODULE" value={draft.module} color={moduleColor(draft.module)} />
          when registered.
        {/if}
      </div>
      {#if draft.stale}
        <div class="tx stale" data-testid="draft-stale">
          <code>method.yaml</code> changed since it was last read. The next Run or Validate reads it again; Update checks it now without running.
        </div>
      {/if}
      <div class="path">
        <span class="box" data-testid="draft-path" title={draft.folder}>{clipMiddle(draft.folder)}</span>
        <CopyButton value={draft.folder} label="Copy path" testid="draft-path-copy" />
      </div>
      <div class="acts">
        <button type="button" class="pri" data-testid="draft-register" disabled={!canRegister}
                title={canRegister ? `Check the folder, then register it as v${draft.registersAs}` : 'Fix the failed checks first'}
                onclick={onRegister}>Register as v{draft.registersAs}</button>
      </div>
    </div>

    <div class="chk" class:bad={!check.ok} data-testid="draft-check">
      <button type="button" class="chh" aria-expanded={checkOpen} data-testid="draft-check-toggle"
              onclick={() => (checkOpen = !checkOpen)}>
        <span>{checkOpen ? '▾' : '▸'} REGISTRATION CHECK</span>
        <small data-testid="draft-check-summary"><span class="ok">✓ {okCount} ok</span>{#if failCount > 0}{' · '}<span class="bad">✗ {failCount} failed</span>{/if}{' · '}nothing written</small>
      </button>
      {#if checkOpen}
        <div class="cb">
          {#each check.lines as line, i (i)}
            <div data-testid="draft-check-line" data-ok={line.ok}>
              <span class={line.ok ? 'ok' : 'bad'}>{line.ok ? '✓' : '✗'}</span>
              {#each codeParts(line.text) as part, j (j)}{#if part.code}<code>{part.text}</code>{:else}{part.text}{/if}{/each}
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <div class="itabs" role="tablist">
      <button type="button" role="tab" aria-selected={tab === 'parameters'} class:on={tab === 'parameters'}
              data-testid="draft-tab-parameters" onclick={() => onTab('parameters')}>Parameters</button>
      <button type="button" role="tab" aria-selected={tab === 'run'} class:on={tab === 'run'}
              data-testid="draft-tab-run" onclick={() => onTab('run')}>Draft run</button>
    </div>
    {#if tab === 'parameters'}
      <div data-testid="draft-parameters"><ContractTables {contract} /></div>
    {:else}
      <DraftRunTab module={draft.module} method={draft.method} {run} loading={runLoading} error={runError}
                   {onOpenHistory} {onViewPipeline} {onOpenStep} {onDeleteOutputs} />
    {/if}
  </div>

  <div class="right">
    {#if detailError}
      <div class="err">{detailError}</div>
    {:else if !detail}
      <div class="dim">Loading the folder…</div>
    {:else}
      <div class="ftabs">
        {#each detail.files ?? [] as file (file.name)}
          <button type="button" class:on={activeFile === file.name} onclick={() => selectFile(file)}>{file.name}</button>
        {/each}
        {#if draft.kind === 'version' && draft.filesUpdated}
          <span class="chg">● files updated since v{draft.newestLabel}</span>
        {/if}
      </div>
      {#if activeFile && html[activeFile]}
        <div class="code">{@html html[activeFile]}</div>
      {/if}
    {/if}
  </div>
</div>

<style>
  .dpanel {
    display: grid; grid-template-columns: minmax(0, 560px) minmax(0, 1fr); gap: 18px;
    font-size: 12.5px;
  }
  .left, .right { min-width: 0; }
  .card {
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px;
    padding: 11px 13px; margin-bottom: 12px;
  }
  .card .k {
    font-size: 10.5px; letter-spacing: 0.08em; color: #bbb; margin-bottom: 6px; font-weight: 700;
    display: flex; justify-content: space-between;
  }
  .card .k small { color: var(--text-secondary); font-weight: 400; letter-spacing: 0; }
  .tx { color: #bbb; line-height: 1.6; }
  .tx b { color: #eee; font-weight: 600; }
  .tx.stale { margin-top: 4px; color: var(--text-primary); }
  .path { display: flex; align-items: center; gap: 6px; margin: 6px 0 8px; }
  .box {
    flex: 1; min-width: 0; background: var(--bg-input); border: 1px solid var(--border); border-radius: 4px;
    padding: 4px 8px; font-family: Consolas, monospace; font-size: 11px; color: var(--text-primary);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .acts { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
  .pri {
    background: var(--accent); border: none; color: white; font-size: 11.5px; padding: 4px 10px;
    border-radius: 3px; cursor: pointer;
  }
  .pri:disabled { opacity: 0.45; cursor: default; }
  .chk { border: 1px solid var(--border); border-radius: 6px; background: var(--bg-panel); margin-bottom: 12px; }
  .chk.bad { border-color: color-mix(in srgb, var(--status-failed) 55%, transparent); }
  .chh {
    width: 100%; padding: 9px 13px; display: flex; justify-content: space-between; align-items: center;
    background: none; border: none; cursor: pointer; text-align: left;
    font-size: 10.5px; letter-spacing: 0.08em; color: #bbb; font-weight: 700;
  }
  .chk.bad .chh { color: var(--status-failed-ink); }
  .chh small { letter-spacing: 0; font-weight: 400; color: #999; font-size: 11.5px; }
  .cb { padding: 0 13px 11px; line-height: 1.75; color: var(--text-primary); }
  code { font-family: Consolas, monospace; font-size: 11.5px; color: #e6e6e6; }
  .itabs { display: flex; gap: 16px; margin-bottom: 10px; border-bottom: 1px solid var(--border); }
  .itabs button {
    background: none; border: none; padding: 4px 2px 7px; color: #999; font-size: 12px; cursor: pointer;
  }
  .itabs button.on { color: #eee; box-shadow: inset 0 -2px 0 var(--accent); }
  .ftabs { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
  .ftabs button {
    background: none; border: 1px solid transparent; color: #999; font-family: Consolas, monospace;
    font-size: 11.5px; padding: 3px 8px; border-radius: 3px; cursor: pointer;
  }
  .ftabs button.on { color: #eee; border-color: var(--border); background: var(--bg-panel); }
  .chg { margin-left: auto; color: var(--text-secondary); font-size: 11.5px; }
  .code { max-height: 460px; overflow: auto; border: 1px solid var(--border); border-radius: 4px; }
  .code :global(pre) { margin: 0; padding: 10px 12px; font-size: 11.5px; }
  .err { color: var(--status-failed-ink); }
  .dim { color: var(--text-secondary); }
  .ok { color: var(--status-ok); }
  .bad { color: var(--status-failed-ink); }
</style>
