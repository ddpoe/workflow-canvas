<script lang="ts">
  /**
   * The run detail's "Cache-key inputs" card: what went into the run's
   * cache key, then what it recorded that is not in the key.
   *
   * One collapsible card, closed by default. Its content is built by
   * `cacheKeyInputs.ts`; this component renders it, holds the open/closed
   * state (reset when another run opens, as the panel remounts it), the
   * links to what the card names, the copy icons and the one hover tooltip.
   *
   * Hover: a hash tag says what it is computed from; a name chip shows its
   * full name and what clicking it does; a copy icon says what it copies.
   * The copy icon beside every hash tag copies the full hash.
   */
  import { cacheKeyInputs } from './cacheKeyInputs.js';
  import type { HashTag, ParamRow } from './cacheKeyInputs.js';
  import type { WfcRun } from './historyApi.js';
  import { selectRun, openRunOn, jumpToPipelineRun } from './historyStore.js';
  import { focusMethod, focusSamples } from '../registry/registryFocus.js';
  import LabelTag from '../shared/LabelTag.svelte';

  interface Props {
    run: WfcRun;
  }

  let { run }: Props = $props();

  let card = $derived(cacheKeyInputs(run));

  let open = $state(false);
  let paramsOpen = $state(false);
  let slotsOpen = $state(false);
  let openValues = $state<Set<string>>(new Set());
  let helpOpen = $state(false);

  let cardEl = $state<HTMLDivElement | undefined>();
  let helpBtn = $state<HTMLButtonElement | undefined>();

  function toggleValue(name: string): void {
    const next = new Set(openValues);
    if (next.has(name)) next.delete(name); else next.add(name);
    openValues = next;
  }

  // Copy icons: a hash's full value, a sample's name, a long value. The icon
  // pressed turns to a check, and its hover says "Copied", for a moment; no
  // timer outlives the card.
  let copied = $state<string | null>(null);
  let copiedTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => () => { if (copiedTimer) clearTimeout(copiedTimer); });

  async function copy(id: string, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      copied = id;
      if (tip) tip = { ...tip, text: 'Copied', preview: null };
      if (copiedTimer) clearTimeout(copiedTimer);
      copiedTimer = setTimeout(() => { copied = null; copiedTimer = null; }, 1500);
    } catch {
      // Clipboard refused (insecure context or permission): nothing is copied.
    }
  }

  // The hover tooltip. One fixed-position element for the card, placed from
  // the hovered element's box, so no scroll or overflow around the card can
  // clip it; it opens upward when there is no room below, and follows its
  // element when anything scrolls.
  const TIP_WIDTH = 240;
  let tip = $state<{
    text: string; preview: string | null; left: number; top: number; up: boolean;
  } | null>(null);
  let tipAnchor: HTMLElement | null = null;

  function placeTip(text: string, preview: string | null): void {
    if (!tipAnchor || !tipAnchor.isConnected) { hideTip(); return; }
    const rect = tipAnchor.getBoundingClientRect();
    const up = rect.bottom + (preview ? 200 : 80) > window.innerHeight;
    tip = {
      text,
      preview,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - TIP_WIDTH - 8)),
      top: up ? rect.top - 5 : rect.bottom + 5,
      up,
    };
  }

  function showTip(event: Event, text: string, preview: string | null): void {
    tipAnchor = event.currentTarget as HTMLElement;
    placeTip(text, preview);
  }

  function hideTip(): void {
    tip = null;
    tipAnchor = null;
  }

  /** The handlers that show `text` (and a preview) while an element is hovered or focused. */
  function hover(text: string, preview: string | null = null) {
    return {
      onmouseenter: (e: Event) => showTip(e, text, preview),
      onmouseleave: hideTip,
      onfocus: (e: Event) => showTip(e, text, preview),
      onblur: hideTip,
    };
  }

  const tipShown = $derived(tip !== null);
  $effect(() => {
    if (!tipShown) return;
    const follow = () => { if (tip) placeTip(tip.text, tip.preview); };
    window.addEventListener('scroll', follow, true);
    return () => window.removeEventListener('scroll', follow, true);
  });

  // The header ?'s popover sits under the card's header, its right edge on
  // the card's, as wide as the formula needs: it may cover the run list to
  // the left rather than the card.
  let helpPos = $state<{ right: number; top: number } | null>(null);

  function placeHelp(): void {
    if (!cardEl || !helpBtn) return;
    const cardRect = cardEl.getBoundingClientRect();
    const btnRect = helpBtn.getBoundingClientRect();
    helpPos = { right: window.innerWidth - cardRect.right, top: btnRect.bottom + 6 };
  }

  function toggleHelp(event: MouseEvent): void {
    event.stopPropagation();
    hideTip();
    if (helpOpen) { helpOpen = false; return; }
    placeHelp();
    helpOpen = true;
  }

  // Open, the popover closes on a click elsewhere or Escape, and follows the
  // card when the panel scrolls or the window resizes.
  $effect(() => {
    if (!helpOpen) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== 'Escape') return;
      helpOpen = false;
    };
    window.addEventListener('click', close);
    window.addEventListener('keydown', close);
    window.addEventListener('scroll', placeHelp, true);
    window.addEventListener('resize', placeHelp);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('keydown', close);
      window.removeEventListener('scroll', placeHelp, true);
      window.removeEventListener('resize', placeHelp);
    };
  });

  function typeClass(r: ParamRow): string {
    return r.type ? `t-${r.type}` : 't-none';
  }

  /**
   * The hover of an output slot the run did not record: either the input
   * named none (the claim used its upstream run's one output) or the run
   * predates recording it; the card cannot tell which.
   */
  const NO_SOURCE_SLOT =
    'No output slot is recorded for this input: it named none, or the run predates recording it.';

  /** A tag's copy icon's hover. */
  function copyLabel(t: HashTag): string {
    return t.kind === 'commit' ? 'Copy full commit SHA' : 'Copy full hash';
  }
</script>

{#snippet copyIcon(id: string, value: string, label: string, testid: string, kind: string)}
  <button
    type="button"
    class="copy-ico"
    class:done={copied === id}
    data-testid={testid}
    data-kind={kind}
    data-copy={value}
    aria-label={label}
    {...hover(copied === id ? 'Copied' : label)}
    onclick={() => copy(id, value)}
  >
    {#if copied === id}
      <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
        <path d="M2.5 6.3l2.3 2.3 4.7-5" fill="none" stroke="currentColor" stroke-width="1.4"
              stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    {:else}
      <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
        <rect x="3.5" y="3.5" width="7" height="7" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.1" />
        <path d="M2.5 8.5h-.3a.9.9 0 0 1-.9-.9V2.2a.9.9 0 0 1 .9-.9h5.4a.9.9 0 0 1 .9.9v.3"
              fill="none" stroke="currentColor" stroke-width="1.1" />
      </svg>
    {/if}
  </button>
{/snippet}

{#snippet tagFace(t: HashTag)}<LabelTag
    label={t.label}
    value={t.short}
    data-testid="hash-tag"
    data-kind={t.kind}
    data-full={t.full ?? ''}
  />{/snippet}

{#snippet hashTag(t: HashTag, onclick: (() => void) | null = null, clickSays: string = '', linkId: string = '')}
  <span class="tagwrap">
    {#if onclick}
      <button
        type="button"
        class="hsh h-{t.kind} link"
        data-testid={linkId}
        {...hover(`${t.meaning} ${clickSays}`)}
        onclick={onclick}
      >{@render tagFace(t)}</button>
    {:else}
      <!-- A tab stop on purpose: focus shows the tag's explanation, as hover
           does, for a sighted keyboard user; the copy icon beside it only
           says what it copies. -->
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <span
        class="hsh h-{t.kind}"
        role="note"
        tabindex="0"
        aria-label={t.meaning}
        {...hover(t.meaning)}
      >{@render tagFace(t)}</span>
    {/if}
    {#if t.full}{@render copyIcon(`hash:${t.kind}:${t.full}`, t.full, copyLabel(t), 'hash-copy', t.kind)}{/if}
  </span>
{/snippet}

<div class="ck-card" data-testid="cache-key-inputs" data-open={open} bind:this={cardEl}>
  <div class="head" class:open>
    <button
      type="button"
      class="head-toggle"
      data-testid="cache-key-toggle"
      aria-expanded={open}
      onclick={() => { open = !open; }}
    >
      <span class="chev" class:down={open}>▸</span>
      <span class="title">Cache-key inputs</span>
    </button>
    <button
      type="button"
      class="help"
      class:on={helpOpen}
      data-testid="cache-key-help"
      aria-label="What the cache key is"
      bind:this={helpBtn}
      onclick={toggleHelp}
    >
      <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
        <circle cx="8" cy="8" r="7.2" fill="none" stroke="currentColor" stroke-width="1" />
        <path d="M6.1 6.2a1.95 1.95 0 1 1 2.7 1.8c-.5.22-.8.62-.8 1.15v.45" fill="none"
              stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
        <circle cx="8" cy="11.7" r=".85" fill="currentColor" />
      </svg>
    </button>
    <span class="meta">
      <span class="chip ch-method" {...hover(card.method.name)}>{card.method.name}</span>
      {#if card.method.version}<span class="chip ch-ver fixed">{card.method.version}</span>{/if}
    </span>
  </div>

  {#if helpOpen && helpPos}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="legend"
      data-testid="cache-key-help-popover"
      style="right: {helpPos.right}px; top: {helpPos.top}px;"
      onclick={(e) => e.stopPropagation()}
    >
      <div class="legend-title">What the cache key is</div>
      <div class="legend-text">
        One hash over everything in the blue group. Two runs with the same key give the same
        result, so wfc reuses the earlier run instead of running again.
      </div>
      <div class="formula">
        cache key = hash(
        <span class="chip ch-method">method</span><span class="chip ch-ver">version</span><span
          class="hsh h-code"><span class="k">code</span></span><span
          class="hsh h-env"><span class="k">env</span></span><span
          class="hsh h-inputs"><span class="k">inputs</span></span><span
          class="hsh h-params"><span class="k">params</span></span>
        )
      </div>
      <div class="legend-foot">
        <span><span class="dim">Change any one</span> and the key changes, so the step runs again.</span>
        <span><span class="dim">Hover a hash</span> to see what it is computed from.</span>
      </div>
    </div>
  {/if}

  {#if open}
    {#if card.earlierRule}
      <div class="note" data-testid="cache-key-earlier-rule">{card.earlierRule}</div>
    {/if}

    <div class="group stripe-key" data-testid="cache-key-group-in-key">
      <div class="sec key">In the cache key</div>

      <div class="row" data-testid="cache-key-row" data-row="method">
        <span class="lbl"><span class="sw sw-method"></span>Method</span>
        <span class="val line">
          <button
            type="button"
            class="chip ch-method link"
            data-testid="cache-key-method-link"
            {...hover(`${card.method.name} — open this version in the Registry`)}
            onclick={() => focusMethod(card.method.module, card.method.method,
                                       card.method.versionIdentity, card.method.version)}
          >{card.method.name}</button>{#if card.method.version}<span class="chip ch-ver fixed">{card.method.version}</span>{/if}<span class="sep"></span>{@render hashTag(card.method.tag)}
        </span>
      </div>

      <div class="row" data-testid="cache-key-row" data-row="env">
        <span class="lbl"><span class="sw sw-env"></span>Env</span>
        <span class="val line">
          {#if card.env.name}<span class="chip ch-env" {...hover(card.env.name)}>{card.env.name}</span>{:else}<span class="muted">not recorded</span>{/if}<span class="sep"></span>{@render hashTag(card.env.tag)}
        </span>
      </div>

      <div class="row" data-testid="cache-key-row" data-row="slots">
        <span class="lbl"><span class="sw sw-inputs"></span>Slots</span>
        <span class="val">
          <span class="line">
          {#if card.slots.rows.length > 0}
            <button
              type="button"
              class="sub-toggle"
              data-testid="cache-key-slots-toggle"
              aria-expanded={slotsOpen}
              onclick={() => { slotsOpen = !slotsOpen; }}
            ><span class="chev" class:down={slotsOpen}>▸</span>{card.slots.label}</button>
          {:else}
            <span class="muted mono">{card.slots.label}</span>
          {/if}<span class="sep"></span>{@render hashTag(card.slots.tag)}
          </span>
        </span>
          {#if slotsOpen}
            <div class="box" data-testid="cache-key-slots-table">
              <div class="tbl slots-tbl">
                <div class="th c-in" style="grid-row: span 2">Input slot</div>
                <div class="th vline span2">Source</div>
                <div class="th vline" style="grid-row: span 2">Hash</div>
                <div class="th sub c-run vline">Run</div>
                <div class="th sub c-out vline">Output slot</div>
                {#each card.slots.rows as s, i (i)}
                  <div data-testid="slot-row" data-kind={s.kind}><span class="chip ch-in" {...hover(s.slot)}>{s.slot}</span></div>
                  {#if s.kind === 'parent'}
                    <div class="vline">
                      <button
                        type="button"
                        class="chip ch-run link"
                        data-testid="slot-run-link"
                        {...hover(`Open run #${s.runId}`)}
                        onclick={() => selectRun(s.runId)}
                      >#{s.runId}</button>
                    </div>
                    <div class="vline">
                      {#if s.sourceSlot}
                        <button
                          type="button"
                          class="chip ch-out link"
                          data-testid="slot-output-link"
                          {...hover(`${s.sourceSlot} — open run #${s.runId} on its Artifacts`)}
                          onclick={() => openRunOn(s.runId, 'artifacts')}
                        >{s.sourceSlot}</button>
                      {:else}
                        <span
                          class="muted"
                          role="note"
                          aria-label={NO_SOURCE_SLOT}
                          data-testid="slot-output-unrecorded"
                          {...hover(NO_SOURCE_SLOT)}
                        >not recorded</span>
                      {/if}
                    </div>
                  {:else}
                    <div class="vline span2 start">
                      <button
                        type="button"
                        class="chip ch-sample link"
                        data-testid="slot-sample-link"
                        {...hover(`${s.sample} — show the Registry's samples`)}
                        onclick={() => focusSamples()}
                      >sample {s.sample}</button>
                      {@render copyIcon(`sample:${i}`, s.sample, 'Copy sample name', 'slot-sample-copy', '')}
                    </div>
                  {/if}
                  <div class="vline">{@render hashTag(s.tag)}</div>
                {/each}
              </div>
            </div>
          {/if}
      </div>

      <div class="row" data-testid="cache-key-row" data-row="params">
        <span class="lbl"><span class="sw sw-params"></span>Params</span>
        <span class="val">
          <span class="line">
          {#if card.params.rows.length > 0}
            <button
              type="button"
              class="sub-toggle"
              data-testid="cache-key-params-toggle"
              aria-expanded={paramsOpen}
              onclick={() => { paramsOpen = !paramsOpen; }}
            ><span class="chev" class:down={paramsOpen}>▸</span>{card.params.label}</button>
          {:else}
            <span class="muted mono">{card.params.label}</span>
          {/if}<span class="sep"></span>{@render hashTag(card.params.tag)}
          </span>
        </span>
          {#if paramsOpen}
            <div class="box" data-testid="cache-key-params-table">
              <div class="tbl params-tbl mono">
                <div class="th c-name">Name</div><div class="th c-value vline">Value</div><div class="th vline">Type</div>
                {#each card.params.rows as r (r.name)}
                  <div class="p-key" data-testid="param-row" data-name={r.name}>{r.name}</div>
                  <div class="vline {typeClass(r)} value-cell">
                    {#if r.long}
                      <button
                        type="button"
                        class="long-toggle"
                        data-testid="param-long-toggle"
                        aria-expanded={openValues.has(r.name)}
                        {...hover(openValues.has(r.name) ? 'Click to close the value' : 'Click to open the full value', openValues.has(r.name) ? null : r.preview)}
                        onclick={() => { hideTip(); toggleValue(r.name); }}
                      >
                        <span class="chev" class:down={openValues.has(r.name)}>▸</span>
                        <span class="tv ell" data-testid="param-value">{r.display}</span>
                        <span class="size" data-testid="param-size">{r.size}</span>
                      </button>
                    {:else}
                      <span class="tv wrap" data-testid="param-value">{r.display}</span>
                    {/if}
                  </div>
                  <div class="vline">
                    {#if r.type}
                      <span class="tchip t-{r.type}" data-testid="param-type">{r.type}</span>
                    {:else}
                      <span class="muted" data-testid="param-type">—</span>
                    {/if}
                  </div>
                  {#if r.long && openValues.has(r.name)}
                    <div class="json-cell">
                      <div class="json-box {typeClass(r)}">
                        <span class="json-copy">{@render copyIcon(`param:${r.name}`, r.pretty ?? '', 'Copy value', 'param-json-copy', '')}</span>
                        <pre class="tv" data-testid="param-json">{r.pretty}</pre>
                      </div>
                    </div>
                  {/if}
                {/each}
              </div>
            </div>
          {/if}
      </div>
    </div>

    <div class="group stripe-rec" data-testid="cache-key-group-recorded">
      <div class="sec rec">Recorded, not in the key</div>
      <div class="row" data-testid="cache-key-row" data-row="commit">
        <span class="lbl">Commit</span>
        <span class="val line">{@render hashTag(card.recorded.commit)}</span>
      </div>
      <div class="row" data-testid="cache-key-row" data-row="wfc-version">
        <span class="lbl">wfc version</span>
        <span class="val mono plain">{card.recorded.wfcVersion}</span>
      </div>
      <div class="row" data-testid="cache-key-row" data-row="pipeline">
        <span class="lbl">Pipeline</span>
        <span class="val line">
          {#if card.recorded.pipeline.pipelineId}
            {@const pipelineId = card.recorded.pipeline.pipelineId}
            {@render hashTag(card.recorded.pipeline.tag,
                             () => { hideTip(); jumpToPipelineRun(pipelineId, run.id); },
                             'Click to open it in the Pipelines view.', 'cache-key-pipeline-link')}
          {:else}
            {@render hashTag(card.recorded.pipeline.tag)}
          {/if}
        </span>
      </div>
    </div>
  {/if}
</div>

{#if tip}
  <div
    class="hash-tip"
    class:up={tip.up}
    data-testid="hash-tip"
    style="left: {tip.left}px; top: {tip.top}px; width: {TIP_WIDTH}px;"
  >
<div>{tip.text}</div>{#if tip.preview}<pre class="tip-preview">{tip.preview}</pre>{/if}</div>
{/if}

<style>
  /* One hue per concept, used by its chips and its hash tag. */
  .ck-card, .hash-tip, .legend {
    --method: #c2a77e; --ver: #7ea893; --env: #a68fae; --params: #c08f6e;
    --inputs: #7a9fc8; --run: #7a9fc8; --out: #6fa5a4; --sample: #adad78; --pipe: #8a909e;
    --commit: #9a9da5;
  }
  .ck-card {
    position: relative;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    font-size: 11px;
    color: var(--text-primary, #ccc);
  }
  .mono { font-family: Consolas, 'Courier New', monospace; }
  .muted { color: #777; }
  .chev { display: inline-block; width: 10px; color: #888; transition: transform 0.12s; }
  .chev.down { transform: rotate(90deg); }

  .head { display: flex; align-items: center; gap: 6px; padding: 8px 10px; }
  .head.open { border-bottom: 1px solid var(--border, #3e3e42); }
  .head-toggle {
    display: flex; align-items: center; gap: 4px;
    background: none; border: none; padding: 0; cursor: pointer;
    color: inherit; font: inherit; font-weight: 600;
  }
  /* The ? is drawn, so it sits in the middle of its circle in any font. */
  .help {
    width: 15px; height: 15px; padding: 0; border: none; background: none;
    color: #9a9aa0; cursor: pointer; display: inline-flex;
  }
  .help svg { display: block; }
  .help:hover, .help.on { color: #4A90D9; }
  .meta { margin-left: auto; display: flex; justify-content: flex-end; gap: 3px; min-width: 0; }
  .head-toggle, .help { flex-shrink: 0; white-space: nowrap; }

  .note { padding: 6px 10px 0; color: #e9a847; }

  .stripe-key { border-left: 3px solid #4d6a8c; }
  .stripe-rec { border-left: 3px solid #6b6b72; }
  .sec {
    font-size: 9px; letter-spacing: 0.6px; text-transform: uppercase;
    padding: 7px 10px 3px; font-weight: 600;
  }
  .sec.key { color: #6f93bd; }
  .sec.rec { color: #888; border-top: 1px solid var(--border, #3e3e42); }
  .row {
    display: grid; grid-template-columns: 58px minmax(0, 1fr); column-gap: 8px;
    padding: 5px 10px; border-top: 1px solid #38383c; align-items: start;
  }
  .lbl { color: #8a8a90; white-space: nowrap; line-height: 17px; }
  .sw { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 5px; vertical-align: 1px; }
  .sw-method { background: var(--method); }
  .sw-env { background: var(--env); }
  .sw-params { background: var(--params); }
  .sw-inputs { background: var(--inputs); }
  .val { min-width: 0; line-height: 17px; }
  /* A row's first line: the name chips shrink (with an ellipsis, the full
     name on hover) so the divider and the hash tag stay on the line. */
  .line { display: flex; align-items: center; min-width: 0; }
  .line > .chip, .meta > .chip { flex: 0 1 auto; min-width: 0; }
  .line > .chip.fixed, .meta > .chip.fixed, .line > .tagwrap, .line > .sep, .line > .sub-toggle { flex-shrink: 0; }
  .val.plain { color: #d4d4d4; }

  .chip {
    display: inline-block; padding: 0 5px; border-radius: 3px; margin-right: 3px;
    font-family: Consolas, monospace; font-size: 10px; line-height: 15px;
    border: 1px solid transparent; vertical-align: middle;
    max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chip.link { cursor: pointer; }
  .chip.link:hover { text-decoration: underline; }
  .ch-method { background: rgba(194, 167, 126, 0.12); color: var(--method); }
  .ch-ver { background: rgba(126, 168, 147, 0.12); color: var(--ver); }
  .ch-env { background: rgba(166, 143, 174, 0.12); color: var(--env); }
  .ch-in { background: rgba(122, 159, 200, 0.12); color: #a9c3e0; border-color: #4d6a8c; font-weight: 600; }
  .ch-out { background: rgba(111, 165, 164, 0.10); color: var(--out); border: 1px dashed #4a7473; }
  .ch-run { background: rgba(122, 159, 200, 0.10); color: var(--run); }
  .ch-sample { background: rgba(173, 173, 120, 0.10); color: var(--sample); }

  .sep {
    display: inline-block; width: 1px; height: 16px; background: #5f5f66;
    margin: 0 7px 0 4px; vertical-align: middle;
  }

  /* A hash tag (the shared LabelTag, its label half in the concept's colour,
     --c) and its copy icon. Sized to its content wherever it sits. */
  .tagwrap { display: inline-flex; align-items: center; gap: 2px; width: max-content; vertical-align: middle; }
  .hsh {
    display: inline-flex; align-items: stretch; vertical-align: middle;
    border-radius: 3px; font-family: Consolas, monospace; font-size: 10px; line-height: 15px;
    border: none; cursor: help;
    white-space: nowrap; width: max-content; padding: 0; background: none;
  }
  .hsh.link { cursor: pointer; }
  .hsh.link:hover :global(.ltag) { border-color: var(--c); }
  .formula .hsh { border: 1px solid color-mix(in srgb, var(--c) 35%, transparent); }
  .hsh .k {
    padding: 0 4px; background: color-mix(in srgb, var(--c) 18%, transparent);
    color: var(--c); font-size: 9px; letter-spacing: 0.2px; border-radius: 2px 0 0 2px;
  }
  .hsh .v { padding: 0 5px; background: rgba(255, 255, 255, 0.04); color: #c8ccd8; border-radius: 0 2px 2px 0; }
  .h-code { --c: var(--method); }
  .h-env { --c: var(--env); }
  .h-params { --c: var(--params); }
  .h-inputs { --c: var(--inputs); }
  .h-run-key { --c: var(--run); }
  .h-content { --c: var(--sample); }
  .h-commit { --c: var(--commit); }
  .h-pipeline { --c: var(--pipe); }

  /* The two-squares copy icon; a check for a moment after a copy. */
  .copy-ico {
    display: inline-flex; align-items: center; justify-content: center;
    width: 15px; height: 15px; padding: 0; border: none; border-radius: 3px;
    background: none; color: #7d7d85; cursor: pointer; flex-shrink: 0;
  }
  .copy-ico:hover, .copy-ico:focus-visible { color: #4A90D9; background: rgba(74, 144, 217, 0.12); }
  .copy-ico.done { color: #50C878; }
  .copy-ico svg { display: block; }

  .sub-toggle {
    background: none; border: none; padding: 0; cursor: pointer; color: #d4d4d4;
    font-family: Consolas, monospace; font-size: 11px; vertical-align: middle;
  }

  /* A row's table spans the row, under its label too. */
  .row > .box { grid-column: 1 / -1; }
  .box { margin-top: 5px; border: 1px solid #45454b; border-radius: 4px; overflow: hidden; background: #252528; }
  .tbl { display: grid; }
  .tbl > div {
    padding: 3px 6px; border-top: 1px solid #38383c; min-width: 0;
    display: flex; align-items: center; gap: 3px;
  }
  .tbl > .th {
    border-top: none; background: #333337; color: #9a9aa0;
    font-size: 9px; letter-spacing: 0.5px; text-transform: uppercase; font-weight: 600;
  }
  .tbl > .th.sub { border-top: 1px solid #3e3e42; background: #2f2f33; font-size: 8.5px; color: #888; }
  .vline { border-left: 1px solid #38383c; }
  .tbl > div > .chip { flex: 0 1 auto; min-width: 0; margin-right: 0; }
  .tbl > div > .copy-ico, .tbl > div > .tagwrap { flex-shrink: 0; }
  .span2 { grid-column: span 2; justify-content: center; }
  .span2.start { justify-content: flex-start; }
  .params-tbl { grid-template-columns: auto minmax(0, 1fr) auto; font-size: 10.5px; }
  .params-tbl > .th.c-name, .p-key { color: #9cdcfe; }
  .params-tbl > .th.c-value { color: #ce9178; }
  /* The hash keeps its width; the run and output-slot columns share what is
     left, their chips truncating. */
  .slots-tbl { grid-template-columns: auto max-content minmax(0, 1fr) max-content; }
  .th.c-in { color: #9cc8f2; }
  .th.c-run { color: var(--run); }
  .th.c-out { color: var(--out); }

  .value-cell { overflow: hidden; }
  .wrap { overflow-wrap: anywhere; }
  .ell { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
  .long-toggle {
    display: flex; align-items: center; gap: 3px; width: 100%; min-width: 0;
    background: none; border: none; padding: 0; cursor: pointer; font: inherit; text-align: left;
  }
  .size { color: #888; white-space: nowrap; font-size: 9.5px; }
  .json-cell { grid-column: 1 / -1; display: block !important; }
  .json-box {
    position: relative; max-height: 240px; overflow: auto;
    background: #1e1e1e; border: 1px solid #3a3a3e; border-radius: 3px;
  }
  .json-box pre { margin: 0; padding: 6px 8px; font-size: 10px; line-height: 1.4; white-space: pre; }
  .json-copy { position: sticky; float: right; top: 4px; margin: 4px 4px 0 0; display: inline-flex; }

  /* Declared param types: the type chip and the value share a colour. */
  .t-str { --t: #c9917a; } .t-int { --t: #a9c49a; } .t-float { --t: #86bfae; }
  .t-bool { --t: #7ea6d6; } .t-list { --t: #cdb27a; } .t-dict { --t: #b98fb6; }
  .t-any { --t: #9a9da5; } .t-none { --t: #ccc; }
  .tv { color: var(--t); }
  .tchip {
    display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 9px; line-height: 14px;
    letter-spacing: 0.3px; color: var(--t);
    background: color-mix(in srgb, var(--t) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--t) 30%, transparent);
  }

  .hash-tip {
    position: fixed; z-index: 1000; white-space: normal;
    font-family: 'Segoe UI', sans-serif; font-size: 10.5px; line-height: 1.35; color: #ccc;
    background: #252526; border: 1px solid #4a4a50; border-radius: 4px; padding: 6px 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5); pointer-events: none; overflow-wrap: anywhere;
  }
  .hash-tip.up { transform: translateY(-100%); }
  .tip-preview {
    margin: 5px 0 0; padding: 4px 6px; max-height: 160px; overflow: hidden;
    background: #1e1e1e; border: 1px solid #3a3a3e; border-radius: 3px;
    font-family: Consolas, monospace; font-size: 9.5px; line-height: 1.35; color: #b0b0b8;
    white-space: pre;
  }

  .legend {
    position: fixed; z-index: 1000; width: max-content; max-width: calc(100vw - 16px);
    background: #252526; border: 1px solid #4a4a50; border-radius: 5px;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.5); padding: 8px 10px; font-size: 11px; color: #bbb;
  }
  .legend-title {
    margin-bottom: 5px; font-size: 10px; color: #aaa; letter-spacing: 0.5px;
    text-transform: uppercase; font-weight: 600;
  }
  .legend-text { max-width: 520px; line-height: 1.4; margin-bottom: 6px; }
  .formula {
    font-family: Consolas, monospace; white-space: nowrap; line-height: 1.9;
    background: #1e1e1e; border: 1px solid #3a3a3e; border-radius: 3px; padding: 3px 8px;
  }
  .formula .hsh { margin-right: 3px; cursor: default; }
  .legend-foot { display: flex; gap: 14px; margin-top: 6px; flex-wrap: wrap; }
  .dim { color: #999; }
</style>
