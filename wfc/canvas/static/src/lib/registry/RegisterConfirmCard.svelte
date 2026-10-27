<script lang="ts">
  /**
   * What a method registration will write, built like the run detail's
   * Cache-key inputs card, from the plan's changes and holds as
   * Registration states them.
   *
   * A new version: the head reads `method | …  version | vN → version | vN+1`;
   * a "Changed" group has one row per part (Code, Slots, Params, Env, and
   * Run only when `script`, `executor` or `gpus` changed), each with its
   * count, opening to a table. Slots and Params tables group their columns
   * under Before and After, one line per entry: an added entry is blank
   * under Before, a removed one under After; changed cells are outlined and
   * unchanged ones dimmed; More opens to a Before | After JSON box with the
   * changed lines marked. An unchanged part reads "unchanged" and does not
   * open.
   *
   * A new method: the head reads `MODULE | …  method | …  version | v1`; one
   * "Registering" group lists its Slots, Params, Env and Run, each open by
   * default, with no Before / After.
   *
   * Both end with the grey "vN holds" group: Script, Helpers, Ignored.
   * Every value wraps inside its own cell.
   */
  import { untrack } from 'svelte';
  import LabelTag from '../shared/LabelTag.svelte';
  import { getModuleColor } from '../history/historyUtils.js';
  import { displaySlotType, slotColor } from '../shared/slotColor.js';
  import {
    cellChanged, cellValue, changeCounts, codeCounts, moreDiff,
    moreJson, moreKeys, moreKeysEither, paramCount, pyValue, slotCounts, typeClass,
  } from './registryFormat.js';
  import type { Declaration, TableKind } from './registryFormat.js';
  import type { PlanEntry, RegistrationPlan } from './api.js';

  let { plan, module, method }: { plan: RegistrationPlan; module: string; method: string } = $props();

  const isNew = $derived(plan.outcome === 'new');
  const changes = $derived(plan.changes);
  const holds = $derived(plan.holds);

  // Which part rows and More cells are open. A new method's rows start open
  // (the modal mounts one card per check).
  let open = $state<Set<string>>(untrack(() => new Set(plan.outcome === 'new' ? ['slots', 'params'] : [])));

  function toggle(id: string): void {
    const next = new Set(open);
    if (next.has(id)) next.delete(id); else next.add(id);
    open = next;
  }

  const slotKind = (e: PlanEntry): TableKind => (e.kind === 'output' ? 'output' : 'input');
  const SLOT_FIELDS = ['type', 'required', 'multiple'] as const;
  const PARAM_FIELDS = ['type', 'default', 'required'] as const;

  const runRows = $derived(Object.entries(changes.run ?? {}));
  const envChanged = $derived(changes.env.before !== changes.env.after || changes.env.rebuilt);
  const codeHas = $derived(!!changes.code && (changes.code.changed.length
    + changes.code.added.length + changes.code.removed.length) > 0);
</script>

{#snippet field(decl: Declaration | null, name: string, slot: boolean)}
  {#if decl === null}<span class="muted">—</span>
  {:else if name === 'type'}
    {#if slot}
      {#if typeof decl.type === 'string' && decl.type}
        <span class="stype" style="--t: {slotColor(decl.type)}">{displaySlotType(decl.type)}</span>
      {:else}<span class="tv t-none">None</span>{/if}
    {:else if typeof decl.type === 'string'}
      <span class="tchip {typeClass(decl.type)}">{decl.type}</span>
    {:else}<span class="tv t-none">None</span>{/if}
  {:else}
    {@const cell = cellValue(decl[name])}
    <span class="tv {cell.cls}" class:ell={cell.long} title={cell.long ? cell.text : undefined}>{cell.text}</span>
  {/if}
{/snippet}

{#snippet slotChip(e: { name: string; kind?: string })}
  <span class="chip {e.kind === 'output' ? 'ch-out' : 'ch-in'}" title={e.name}>{e.name}</span>
{/snippet}

{#snippet moreCell(id: string, keys: string[], lit: boolean)}
  {#if keys.length > 0}
    <button type="button" class="sub-toggle more" class:lit data-testid="more-toggle"
            aria-expanded={open.has(id)} onclick={() => toggle(id)}>
      <span class="chev" class:down={open.has(id)}>▸</span><span class="keys">{#each keys as k}<span class="key">{k}</span>{/each}</span>
    </button>
  {:else}<span class="muted">—</span>{/if}
{/snippet}

{#snippet beforeAfter(part: 'slots' | 'params', entries: PlanEntry[])}
  {@const slot = part === 'slots'}
  {@const fields = slot ? SLOT_FIELDS : PARAM_FIELDS}
  <div class="box" data-testid="confirm-table" data-part={part}>
    <div class="tbl ba" class:ba-param={!slot}>
      <div class="th rspan2" class:p-key={!slot}>{slot ? 'Slot' : 'Name'}</div>
      <div class="th grp b span3 gap">Before</div>
      <div class="th grp a span3 gap">After</div>
      <div class="th rspan2 vline">More</div>
      {#each ['b', 'a'] as side}
        {#each fields as f, i}
          <div class="th sub" class:gap={i === 0} class:vline={i > 0}
               class:c-default={f === 'default'}>{f === 'required' ? 'Req.' : f === 'multiple' ? 'Multi' : f === 'default' ? 'Default' : 'Type'}</div>
        {/each}
      {/each}
      {#each entries as e (`${e.kind ?? ''}:${e.name}`)}
        {@const kind = slot ? slotKind(e) : 'param'}
        {@const id = `${part}:${e.kind ?? ''}:${e.name}`}
        {@const keys = moreKeysEither(e.before, e.after, kind)}
        {@const moreLit = e.change === 'changed' && moreJson(e.before, kind) !== moreJson(e.after, kind)}
        <div class="entry" data-testid="confirm-entry" data-name={e.name} data-change={e.change}>
          {#if slot}{@render slotChip(e)}{:else}<span class="p-key">{e.name}</span>{/if}
        </div>
        {#each [['b', e.before], ['a', e.after]] as [side, decl]}
          {#each fields as f, i}
            {@const changed = cellChanged(e.before, e.after, f)}
            <div class="{side === 'b' ? 'bcol' : 'acol'}" class:gap={i === 0} class:vline={i > 0}
                 class:dimc={e.change === 'changed' && !changed}
                 data-testid="ba-cell" data-side={side === 'b' ? 'before' : 'after'} data-field={f}
                 data-changed={changed}>
              <span class:lit={changed} class:old={side === 'b' && (changed || e.change === 'removed')}>{@render field(decl as Declaration | null, f, slot)}</span>
            </div>
          {/each}
        {/each}
        <div class="vline">{@render moreCell(id, keys, moreLit)}</div>
        {#if open.has(id)}
          {@const diff = moreDiff(e.before, e.after, kind)}
          <div class="json-cell" data-testid="more-diff">
            <div class="jdiff">
              <div><div class="jh"><span class="side b">before</span></div>
                {#if diff.before}<pre data-testid="more-before">{#each diff.before as l}<span class:del={l.marked}>{l.text}</span>
{/each}</pre>{:else}<pre class="muted">not declared</pre>{/if}</div>
              <div><div class="jh"><span class="side a">after</span></div>
                {#if diff.after}<pre data-testid="more-after">{#each diff.after as l}<span class:ins={l.marked}>{l.text}</span>
{/each}</pre>{:else}<pre class="muted">not declared</pre>{/if}</div>
            </div>
          </div>
        {/if}
      {/each}
    </div>
  </div>
{/snippet}

{#snippet newTable(part: 'slots' | 'params', entries: PlanEntry[])}
  {@const slot = part === 'slots'}
  {@const fields = slot ? SLOT_FIELDS : PARAM_FIELDS}
  <div class="box" data-testid="confirm-table" data-part={part}>
    <div class="tbl nm" class:nm-param={!slot}>
      <div class="th" class:p-key={!slot}>{slot ? 'Slot' : 'Name'}</div>
      {#each fields as f}
        <div class="th vline" class:c-default={f === 'default'}>{f === 'required' ? 'Req.' : f === 'multiple' ? 'Multi' : f === 'default' ? 'Default' : 'Type'}</div>
      {/each}
      <div class="th vline">More</div>
      {#each entries as e (`${e.kind ?? ''}:${e.name}`)}
        {@const kind = slot ? slotKind(e) : 'param'}
        {@const id = `${part}:${e.kind ?? ''}:${e.name}`}
        <div class="entry" data-testid="confirm-entry" data-name={e.name} data-change={e.change}>
          {#if slot}{@render slotChip(e)}{:else}<span class="p-key">{e.name}</span>{/if}
        </div>
        {#each fields as f}
          <div class="vline" data-testid="nm-cell" data-field={f}>{@render field(e.after, f, slot)}</div>
        {/each}
        <div class="vline">{@render moreCell(id, e.after ? moreKeys(e.after, kind) : [], false)}</div>
        {#if open.has(id)}
          <div class="json-cell" data-testid="more-diff">
            <pre data-testid="more-after">{moreJson(e.after, kind)}</pre>
          </div>
        {/if}
      {/each}
    </div>
  </div>
{/snippet}

{#snippet partRow(part: string, label: string, dot: string, count: string, has: boolean)}
  <div class="row" data-testid="confirm-row" data-part={part}>
    <span class="lbl"><span class="sw sw-{dot}"></span>{label}</span>
    <span class="val">
      {#if has}
        <button type="button" class="sub-toggle" data-testid="confirm-count"
                aria-expanded={open.has(part)} onclick={() => toggle(part)}>
          <span class="chev" class:down={open.has(part)}>▸</span>{count}
        </button>
      {:else}
        <span class="muted" data-testid="confirm-count">{count}</span>
      {/if}
    </span>
    {#if has && open.has(part)}
      {#if part === 'code' && changes.code}
        <div class="box" data-testid="confirm-table" data-part="code">
          <div class="tbl code">
            <div class="th">File</div><div class="th vline">Change</div>
            {#each (['changed', 'added', 'removed'] as const) as kind}
              {#each changes.code[kind] as file (file)}
                <div class="file">{file}</div><div class="vline"><span class="chg {kind}">{kind}</span></div>
              {/each}
            {/each}
          </div>
        </div>
      {:else if part === 'slots'}
        {#if isNew}{@render newTable('slots', changes.slots)}{:else}{@render beforeAfter('slots', changes.slots)}{/if}
      {:else if part === 'params'}
        {#if isNew}{@render newTable('params', changes.params)}{:else}{@render beforeAfter('params', changes.params)}{/if}
      {/if}
    {/if}
  </div>
{/snippet}

<div class="ck" data-testid="confirm-card" data-outcome={plan.outcome}>
  <div class="head">
    {#if isNew}
      <LabelTag kind="module" label="MODULE" value={module} color={getModuleColor(module)} />
      <LabelTag kind="method" label="method" value={method} />
      <LabelTag kind="version" label="version" value={`v${plan.label}`} />
      <span class="dim">new method</span>
    {:else}
      <LabelTag kind="method" label="method" value={method} />
      <LabelTag kind="version" label="version" value={`v${plan.newestLabel}`} />
      <span class="dim">→</span>
      <LabelTag kind="version" label="version" value={`v${plan.label}`} data-testid="confirm-next" />
      <span class="dim">changes</span>
    {/if}
  </div>

  <div class="group key">
    <div class="sec key">{isNew ? 'Registering' : 'Changed'}</div>
    {#if isNew}
      {@render partRow('slots', 'Slots', 'inputs', slotCounts(changes.slots), changes.slots.length > 0)}
      {@render partRow('params', 'Params', 'params', paramCount(changes.params.length), changes.params.length > 0)}
      <div class="row" data-testid="confirm-row" data-part="env">
        <span class="lbl"><span class="sw sw-env"></span>Env</span>
        <span class="val"><LabelTag kind="env" label="env" value={changes.env.after} /></span>
      </div>
      <div class="row" data-testid="confirm-row" data-part="run">
        <span class="lbl"><span class="sw sw-method"></span>Run</span>
        <span class="val plain" data-testid="confirm-run">{holds.script} · {String(changes.run.executor?.[1] ?? 'python')} · gpus <span class="tv {cellValue(changes.run.gpus?.[1] ?? false).cls}">{pyValue(changes.run.gpus?.[1] ?? false)}</span></span>
      </div>
    {:else}
      {#if changes.code === null}
        <div class="row" data-testid="confirm-row" data-part="code">
          <span class="lbl"><span class="sw sw-method"></span>Code</span>
          <span class="val muted">v{plan.newestLabel}'s files are no longer in git, so what changed in its code and contract cannot be named</span>
        </div>
      {:else}
        {@render partRow('code', 'Code', 'method', codeHas ? codeCounts(changes.code) : 'unchanged', codeHas)}
        {@render partRow('slots', 'Slots', 'inputs', changes.slots.length ? changeCounts(changes.slots) : 'unchanged', changes.slots.length > 0)}
        {@render partRow('params', 'Params', 'params', changes.params.length ? changeCounts(changes.params) : 'unchanged', changes.params.length > 0)}
      {/if}
      <div class="row" data-testid="confirm-row" data-part="env">
        <span class="lbl"><span class="sw sw-env"></span>Env</span>
        <span class="val" data-testid="confirm-count">
          {#if changes.env.before !== changes.env.after}
            <LabelTag kind="env" label="env" value={changes.env.before ?? '—'} /> <span class="dim">→</span> <LabelTag kind="env" label="env" value={changes.env.after} />
          {:else}
            <LabelTag kind="env" label="env" value={changes.env.after} />
            <span class="muted">{envChanged ? 'rebuilt (its fingerprint changed)' : 'unchanged'}</span>
          {/if}
        </span>
      </div>
      {#if runRows.length > 0}
        <div class="row" data-testid="confirm-row" data-part="run">
          <span class="lbl"><span class="sw sw-method"></span>Run</span>
          <span class="val plain" data-testid="confirm-run">
            {#each runRows as [key, [b, a]], i (key)}{#if i > 0}<span class="dim"> · </span>{/if}{key} <span class="tv old {cellValue(b).cls}">{pyValue(b)}</span> <span class="dim">→</span> <span class="tv {cellValue(a).cls}">{pyValue(a)}</span>{/each}
          </span>
        </div>
      {/if}
    {/if}
  </div>

  <div class="group rec">
    <div class="sec rec">v{plan.label} holds</div>
    <div class="row"><span class="lbl">Script</span><span class="val files" data-testid="holds-script">{holds.script}</span></div>
    <div class="row"><span class="lbl">Helpers</span>
      <span class="val files" class:muted={holds.helpers.length === 0} data-testid="holds-helpers">{holds.helpers.length ? holds.helpers.join(', ') : 'none'}</span></div>
    <div class="row"><span class="lbl">Ignored</span>
      <span class="val muted" data-testid="holds-ignored">{holds.ignored.length ? holds.ignored.join(', ') : 'none'}</span></div>
  </div>
</div>

<style>
  .ck {
    --method: #c2a77e; --env: #a68fae; --params: #c08f6e; --inputs: #7a9fc8; --out: #6fa5a4;
    background: var(--bg-header); border: 1px solid var(--border); font-size: 11px; color: var(--text-primary);
    min-width: 0;
  }
  .head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 7px 10px;
    border-bottom: 1px solid var(--border); font-weight: 600; color: #e6e6e6;
  }
  .head .dim { font-weight: 400; margin-left: 4px; }
  .dim { color: var(--text-secondary); }
  .muted { color: #777; }
  .group.key { border-left: 3px solid #4d6a8c; }
  .group.rec { border-left: 3px solid #6b6b72; }
  .sec { font-size: 9px; letter-spacing: 0.6px; text-transform: uppercase; padding: 7px 10px 3px; font-weight: 600; }
  .sec.key { color: #6f93bd; }
  .sec.rec { color: var(--text-secondary); border-top: 1px solid var(--border); }
  .row {
    display: grid; grid-template-columns: 58px minmax(0, 1fr); column-gap: 8px;
    padding: 5px 10px; border-top: 1px solid #38383c; align-items: start;
  }
  .row > .box { grid-column: 1 / -1; }
  .lbl { color: #8a8a90; white-space: nowrap; line-height: 17px; }
  .sw { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 5px; vertical-align: 1px; }
  .sw-method { background: var(--method); } .sw-env { background: var(--env); }
  .sw-params { background: var(--params); } .sw-inputs { background: var(--inputs); }
  .val { line-height: 17px; min-width: 0; font-family: Consolas, monospace; overflow-wrap: anywhere; }
  .val.plain { color: #d4d4d4; }
  .files { color: #d4d4d4; }
  .chev { display: inline-block; width: 10px; color: var(--text-secondary); transition: transform 0.12s; flex-shrink: 0; }
  .chev.down { transform: rotate(90deg); }
  .sub-toggle {
    display: inline-flex; align-items: flex-start; max-width: 100%; min-width: 0;
    background: none; border: none; padding: 0; cursor: pointer; color: #d4d4d4;
    font-family: Consolas, monospace; font-size: 10.5px; text-align: left;
  }
  .keys { min-width: 0; overflow-wrap: break-word; }
  .box { margin-top: 5px; border: 1px solid #45454b; border-radius: 4px; overflow: hidden; background: #252528; }
  .tbl { display: grid; font-size: 10.5px; font-family: Consolas, monospace; }
  /* Every value wraps inside its own cell, at a space where it has one. */
  .tbl > div {
    padding: 3px 6px; border-top: 1px solid #38383c; min-width: 0;
    display: flex; align-items: center; gap: 4px; overflow-wrap: break-word;
  }
  .tbl > div > span { min-width: 0; max-width: 100%; }
  .tbl > .th {
    border-top: none; background: #333337; color: #9a9aa0; font-size: 9px; letter-spacing: 0.5px;
    text-transform: uppercase; font-weight: 600; font-family: -apple-system, "Segoe UI", sans-serif;
  }
  .tbl > .th.sub { border-top: 1px solid var(--border); background: #2f2f33; font-size: 8.5px; color: var(--text-secondary); }
  .tbl > .th.grp { justify-content: center; font-size: 9.5px; }
  .tbl > .th.grp.b { color: #e88a7d; background: #35302f; }
  .tbl > .th.grp.a { color: #7ec699; background: #2e3530; }
  .tbl > .th.c-default { color: #ce9178; }
  .tbl > .th.p-key { color: #9cdcfe; }
  .vline { border-left: 1px solid #38383c; }
  .gap { border-left: 2px solid #4a4a50 !important; }
  .span3 { grid-column: span 3; }
  .rspan2 { grid-row: span 2; }
  /* Name | Before (3) | After (3) | More; a param's Default gets the room. */
  .tbl.ba { grid-template-columns: minmax(0, 1.2fr) repeat(6, minmax(0, 0.8fr)) minmax(0, 1.2fr); }
  .tbl.ba.ba-param {
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.6fr) minmax(0, 1.5fr) minmax(0, 0.6fr)
                           minmax(0, 0.6fr) minmax(0, 1.5fr) minmax(0, 0.6fr) minmax(0, 1.2fr);
  }
  .tbl.nm { grid-template-columns: minmax(0, 1.3fr) repeat(3, minmax(0, 0.8fr)) minmax(0, 1.2fr); }
  .tbl.nm.nm-param { grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.7fr) minmax(0, 1.6fr) minmax(0, 0.7fr) minmax(0, 1.2fr); }
  .key { display: block; line-height: 15px; }
  .tbl.code { grid-template-columns: minmax(0, 1fr) auto; }
  .tbl > div.bcol { background: rgba(232, 138, 125, 0.03); }
  .tbl > div.acol { background: rgba(126, 198, 153, 0.03); }
  .dimc { opacity: 0.4; }
  .lit { box-shadow: inset 0 0 0 1px rgba(233, 168, 71, 0.55); border-radius: 2px; }
  .old { text-decoration: line-through; text-decoration-color: rgba(232, 138, 125, 0.7); }
  .ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; max-width: 100%; }
  .p-key { color: #9cdcfe; overflow-wrap: anywhere; }
  .file { color: #d4d4d4; }
  .chip {
    display: inline-block; max-width: 100%; padding: 0 5px; border-radius: 3px; font-family: Consolas, monospace;
    font-size: 10px; line-height: 15px; border: 1px solid transparent; overflow-wrap: anywhere;
  }
  .ch-in { background: rgba(122, 159, 200, 0.12); color: #a9c3e0; border-color: #4d6a8c; font-weight: 600; }
  .ch-out { background: rgba(111, 165, 164, 0.10); color: var(--out); border: 1px dashed #4a7473; }
  .chg { font-size: 9px; padding: 0 5px; line-height: 14px; border-radius: 3px; text-transform: uppercase; letter-spacing: 0.3px; border: 1px solid; white-space: nowrap; }
  .chg.changed { color: var(--color-running); border-color: rgba(233, 168, 71, 0.45); background: rgba(233, 168, 71, 0.1); }
  .chg.added { color: #7ec699; border-color: rgba(126, 198, 153, 0.45); background: rgba(126, 198, 153, 0.1); }
  .chg.removed { color: #e88a7d; border-color: rgba(232, 138, 125, 0.45); background: rgba(232, 138, 125, 0.1); }
  .stype {
    display: inline-block; max-width: 100%; overflow-wrap: anywhere; font-size: 9.5px; padding: 0 5px;
    line-height: 14px; border-radius: 3px; color: var(--t);
    border: 1px solid color-mix(in srgb, var(--t) 45%, transparent);
    background: color-mix(in srgb, var(--t) 14%, transparent);
  }
  .t-str { --t: #c9917a; } .t-int { --t: #a9c49a; } .t-float { --t: #86bfae; }
  .t-bool { --t: #7ea6d6; } .t-list { --t: #cdb27a; } .t-dict { --t: #b98fb6; }
  .t-any { --t: #9a9da5; } .t-none { --t: #ccc; }
  .tv { color: var(--t); overflow-wrap: anywhere; }
  .tchip {
    display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 9px; line-height: 14px;
    color: var(--t); border: 1px solid color-mix(in srgb, var(--t) 45%, transparent);
  }
  .json-cell { grid-column: 1 / -1; padding: 4px 6px 6px !important; display: block !important; background: #222225; }
  .jdiff { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 6px; }
  .jh { margin-bottom: 2px; }
  .side {
    font-family: -apple-system, "Segoe UI", sans-serif; font-size: 8.5px; text-transform: uppercase;
    letter-spacing: 0.4px; padding: 0 4px; border-radius: 2px; line-height: 13px;
  }
  .side.b { color: #e88a7d; background: rgba(232, 138, 125, 0.12); }
  .side.a { color: #7ec699; background: rgba(126, 198, 153, 0.12); }
  pre {
    margin: 0; background: var(--bg-input); border: 1px solid #45454b; border-radius: 3px; padding: 5px 7px;
    font-size: 10.5px; color: #d4d4d4; white-space: pre-wrap; overflow-wrap: anywhere;
  }
  .del { background: rgba(232, 138, 125, 0.16); }
  .ins { background: rgba(126, 198, 153, 0.16); }
</style>
