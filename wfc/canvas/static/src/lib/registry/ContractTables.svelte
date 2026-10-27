<script lang="ts">
  /**
   * A method version's contract: its Inputs, Outputs and Params, each a
   * bordered box with a header bar (`INPUTS | TYPE`, `PARAMS | TYPE |
   * DEFAULT`) and one row per entry. A row reads the entry's name and type,
   * and a param's default; everything else it declares is behind the row.
   * A closed row's name is one line, cut with "…" and whole on hover; an
   * open row shows the whole name.
   *
   * Opening a row draws a line under its name and lists, under it on the
   * row's own grid with a rule on the left and a `FIELD | VALUE` header
   * row, its `description` (or "No description in method.yaml") and then
   * each other field it declares: `required`, `multiple`, `constraints`,
   * `columns`, any custom key. The list holds what that entry has and
   * nothing more, so a field added to the contract later shows there with
   * no change here. Values read in Python form (`registryFormat.ts`).
   *
   * Every value, the row's default and each field's alike, sits on one line
   * from the Type column to the right edge, bound to the right. One wider
   * than that is cut with "…", and clicking it opens a box under it with
   * the whole value: prose as written, a mapping or a list as JSON (✕, or
   * the value again, closes it). What is cut follows the column's width,
   * which the panel's divider drags: widen it and a value that fits is
   * whole and no longer opens. Slot types take their slot colour; param
   * types and values the Cache-key card's type colours.
   */
  import { displaySlotType, slotColor } from '../shared/slotColor.js';
  import { asDeclaration, cellValue, detailFields, typeClass } from './registryFormat.js';
  import type { Declaration, TableKind } from './registryFormat.js';
  import type { Contract } from './api.js';

  let { contract }: { contract: Contract } = $props();

  type Row = { name: string; decl: Declaration };
  type Section = { kind: TableKind; caption: string; rows: Row[] };

  function rowsOf(section: Record<string, unknown> | undefined): Row[] {
    return Object.entries(section ?? {}).map(([name, spec]) => ({ name, decl: asDeclaration(spec) }));
  }

  const sections = $derived<Section[]>([
    { kind: 'input', caption: 'Inputs', rows: rowsOf(contract.input_slots) },
    { kind: 'output', caption: 'Outputs', rows: rowsOf(contract.output_slots) },
    { kind: 'param', caption: 'Params', rows: rowsOf(contract.params_schema) },
  ]);

  /**
   * What is open: a row's details (`kind:name`), and the whole value of a
   * cut one (`v:kind:name` for a default, `f:kind:name:field` for a field).
   */
  let open = $state<Set<string>>(new Set());

  function toggle(id: string): void {
    const next = new Set(open);
    if (next.has(id)) next.delete(id); else next.add(id);
    open = next;
  }

  /**
   * A name split after each `_` and `-`, the places an open row's name may
   * break (`minimum_normalised_` / `expression`), so it wraps between words.
   */
  function nameParts(name: string): string[] {
    return name.match(/[^_-]*[_-]|[^_-]+$/g) ?? [name];
  }

  /** The values cut by their column's width, by the id their box opens under. */
  let cut = $state<Set<string>>(new Set());

  function setCut(id: string, isCut: boolean): void {
    if (cut.has(id) === isCut) return;
    const next = new Set(cut);
    if (isCut) next.add(id); else next.delete(id);
    cut = next;
  }

  /**
   * Report whether an element's text is wider than the element, now and on
   * every resize (the divider, the window): `report(true)` when it is cut.
   */
  function overflow(node: HTMLElement, report: (isCut: boolean) => void) {
    const check = () => report(node.scrollWidth > node.clientWidth + 1);
    check();
    // Again once the text inside has laid out (a split name renders after the node).
    const frame = typeof requestAnimationFrame === 'undefined' ? 0 : requestAnimationFrame(check);
    if (typeof ResizeObserver === 'undefined') return {};
    const ro = new ResizeObserver(check);
    ro.observe(node);
    return { destroy: () => { ro.disconnect(); if (frame) cancelAnimationFrame(frame); } };
  }
</script>

<!-- A value on one line: cut with "…" when wider than its column, and then
     a click opens its whole value under it. -->
{#snippet oneLine(id: string, text: string, cls: string, label: string, testid: string)}
  {@const isCut = cut.has(id) || open.has(id)}
  <button type="button" class="dv mono tv {cls}" class:cut={isCut} class:on={open.has(id)}
          data-testid={testid} disabled={!isCut} aria-expanded={isCut ? open.has(id) : undefined}
          title={isCut ? (open.has(id) ? `Close the whole ${label}` : `Show the whole ${label}`) : undefined}
          use:overflow={(o) => setCut(id, o)}
          onclick={(e) => { e.stopPropagation(); if (isCut) toggle(id); }}
          onkeydown={(e) => e.stopPropagation()}>{text}</button>
{/snippet}

<!-- The whole value of a cut one, in a box under it. -->
{#snippet wholeBox(id: string, label: string, full: string, cls: string, prose: boolean, testid: string)}
  <div class="valbox" data-testid={testid}>
    <div class="vbhead">
      <span class="fk mono">{label}</span>
      <button type="button" class="vbclose" data-testid="{testid}-close"
              aria-label="Close the whole {label}" onclick={() => toggle(id)}>✕</button>
    </div>
    <pre class="whole tv {cls}" class:prose data-testid="{testid}-full">{full}</pre>
  </div>
{/snippet}

<div class="contract" data-testid="contract-tables">
  {#each sections as s (s.kind)}
    {#if s.rows.length > 0}
      <section class="box" data-testid="contract-table" data-kind={s.kind}>
        <!-- The section's caption heads the name column, so the header bar
             names every column without a second title. -->
        <div class="boxhead grid" data-testid="contract-header">
          <span></span>
          <span class="caption">{s.caption}<span class="count">{s.rows.length}</span></span>
          <span>Type</span>
          <span class="right">{s.kind === 'param' ? 'Default' : ''}</span>
        </div>
        {#each s.rows as r (r.name)}
          {@const rid = `${s.kind}:${r.name}`}
          {@const isOpen = open.has(rid)}
          <div class="entry" class:open={isOpen} data-testid="contract-row" data-name={r.name}>
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <div class="head grid" role="button" tabindex="0" data-testid="row-toggle" aria-expanded={isOpen}
                 onclick={() => toggle(rid)}
                 onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(rid); } }}>
              <span class="chev" class:down={isOpen}>▸</span>
              <span class="name mono" title={!isOpen && cut.has(`n:${rid}`) ? r.name : undefined}
                    data-testid="cell-name" use:overflow={(o) => setCut(`n:${rid}`, o)}
                    >{#if isOpen}{#each nameParts(r.name) as part}{part}<wbr />{/each}{:else}{r.name}{/if}</span>
              <span class="type" data-testid="cell-type">
                {#if s.kind === 'param'}
                  {#if typeof r.decl.type === 'string'}
                    <span class="tchip {typeClass(r.decl.type)}">{r.decl.type}</span>
                  {/if}
                {:else if typeof r.decl.type === 'string' && r.decl.type}
                  <span class="stype" style="--t: {slotColor(r.decl.type)}">{displaySlotType(r.decl.type)}</span>
                {/if}
              </span>
              <span class="value">
                {#if s.kind === 'param'}
                  {@const cell = cellValue(r.decl.default)}
                  {@render oneLine(`v:${rid}`, cell.text, cell.cls, 'default', 'cell-default')}
                {/if}
              </span>
            </div>
            {#if open.has(`v:${rid}`)}
              {@const cell = cellValue(r.decl.default)}
              {@render wholeBox(`v:${rid}`, 'default', cell.pretty ?? cell.text, cell.cls, false, 'default-box')}
            {/if}
            {#if isOpen}
              <div class="details" data-testid="contract-details">
                <div class="dhead sub" data-testid="details-header">
                  <span>Field</span><span>Value</span>
                </div>
                {#each detailFields(r.decl, s.kind) as f (f.key)}
                  {@const fid = `f:${rid}:${f.key}`}
                  <div class="field sub" data-testid="contract-field" data-key={f.key}>
                    <span class="fk mono">{f.key}</span>
                    <span class="value">
                      {@render oneLine(fid, f.text, f.cls, f.key,
                                       f.key === 'description' ? 'contract-description' : 'contract-field-value')}
                    </span>
                  </div>
                  {#if open.has(fid)}
                    {@render wholeBox(fid, f.key, f.full, f.cls, f.prose, 'field-box')}
                  {/if}
                {/each}
              </div>
            {/if}
          </div>
        {/each}
      </section>
    {/if}
  {/each}
  {#if sections.every(s => s.rows.length === 0)}
    <div class="muted">No inputs, outputs or params declared.</div>
  {/if}
</div>

<style>
  .contract { min-width: 0; font-size: 11.5px; }
  /* Each section is its own box with a header bar. */
  .box {
    border: 1px solid #45454b; border-radius: 4px; overflow: hidden; background: #222225;
    margin-bottom: 10px;
  }
  /* One grid for every header and row, name | type | default, so Type lines
     up from Inputs to Params; a slot leaves Default empty. */
  .grid {
    display: grid; grid-template-columns: 12px minmax(0, 1fr) 56px minmax(0, 1.1fr);
    column-gap: 8px; align-items: center;
  }
  /* The details' own grid, indented under the entry's name: field 25%, value
     75%, a line between them; the header names each column centred over it. */
  .sub {
    display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 3fr);
    align-items: stretch;
  }
  .sub > :first-child { padding-right: 8px; display: flex; align-items: center; }
  .sub > :nth-child(2) { padding-left: 8px; border-left: 1px solid rgba(90, 90, 98, 0.6); }
  .dhead.sub > * { justify-content: center; text-align: center; }
  .boxhead {
    padding: 4px 6px; background: #333337;
    font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.5px;
    color: #9a9aa0; font-weight: 600;
  }
  .caption { font-size: 10px; letter-spacing: 0.6px; color: #e6e6e6; }
  .count { color: var(--text-secondary); font-weight: 400; margin-left: 6px; font-size: 10px; letter-spacing: 0; }
  .right { text-align: right; }
  .entry { border-top: 1px solid #38383c; }
  .entry.open { background: rgba(255, 255, 255, 0.02); }
  .head { padding: 4px 6px; cursor: pointer; min-width: 0; }
  .head:hover { background: var(--bg-header); }
  .head:focus-visible { outline: 1px solid var(--accent); outline-offset: -1px; }
  /* An open entry's name is underlined to the row's end: everything under
     the line is that entry's. */
  .entry.open .head { box-shadow: inset 0 -1px 0 #5a5a62; }
  /* A closed entry's name is one line, cut with "…" (its whole name on
     hover); opening the entry shows the whole name, wrapping if it must. */
  .name { color: var(--text-primary); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .entry.open .name { white-space: normal; overflow-wrap: break-word; overflow: visible; color: #e6e6e6; }
  .type { display: flex; }
  /* A value never wraps: wider than its column, it is cut, and a click opens it whole. */
  .value { min-width: 0; display: flex; justify-content: flex-end; }
  .dv {
    background: none; border: none; padding: 0; font: inherit; font-size: 11px; line-height: 16px;
    color: var(--t); text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    max-width: 100%; cursor: inherit;
  }
  .dv:disabled { opacity: 1; }
  .dv.cut { cursor: zoom-in; text-decoration: underline dotted color-mix(in srgb, var(--t) 60%, transparent); }
  .dv.on { cursor: zoom-out; }
  .valbox {
    display: flex; flex-direction: column; gap: 2px; margin: 4px 6px 6px 26px; padding: 5px 8px;
    background: var(--bg-input); border: 1px solid #45454b; border-radius: 3px;
  }
  .vbhead { display: flex; align-items: center; }
  .vbclose {
    margin-left: auto; background: none; border: none; color: var(--text-secondary);
    cursor: pointer; font-size: 11px; padding: 0 2px; line-height: 1;
  }
  .vbclose:hover { color: #e6e6e6; }
  .whole {
    margin: 0; font-family: Consolas, monospace; font-size: 11px; white-space: pre-wrap;
    overflow-wrap: anywhere; max-height: 240px; overflow: auto;
  }
  .whole.prose { font-family: inherit; line-height: 1.45; }
  .mono { font-family: "JetBrains Mono", Consolas, monospace; }
  .muted { color: var(--text-muted); }
  .chev { display: inline-block; width: 10px; color: var(--text-secondary); font-size: 10px; transition: transform 0.12s; }
  .chev.down { transform: rotate(90deg); }
  /* The details sit indented under their entry, a rule on the left tying
     them to it, under a FIELD | VALUE header row. */
  .details {
    position: relative; display: flex; flex-direction: column;
    margin: 0 0 4px 15px; padding-left: 6px;
  }
  .details::before {
    content: ''; position: absolute; left: 0; top: 0; bottom: 2px; width: 2px; background: #4a4a50;
  }
  .details > .valbox { margin-left: 6px; }
  .dhead {
    padding: 3px 6px; background: rgba(255, 255, 255, 0.03);
    font-size: 8.5px; text-transform: uppercase; letter-spacing: 0.5px; color: #8a8a90; font-weight: 600;
  }
  .details > .field { padding: 3px 6px; }
  .details > .field + .field, .details > .valbox + .field { box-shadow: inset 0 1px 0 rgba(62, 62, 66, 0.5); }
  .details > .dhead + .field { box-shadow: inset 0 1px 0 rgba(62, 62, 66, 0.5); }
  .fk { color: var(--text-secondary); font-size: 10.5px; line-height: 16px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* The description is prose; one without a description says so, quietly. */
  .prose { --t: var(--text-primary); font-family: inherit; }
  /* Italic leans past its box: a little room on the right keeps the last letter whole. */
  .none { --t: var(--text-muted); font-style: italic; font-family: inherit; padding-right: 2px; }
  .stype {
    display: inline-block; font-family: Consolas, monospace; font-size: 10px; padding: 0 5px; line-height: 15px;
    border-radius: 3px; color: var(--t); border: 1px solid color-mix(in srgb, var(--t) 45%, transparent);
    background: color-mix(in srgb, var(--t) 14%, transparent); white-space: nowrap;
  }
  /* Declared param types: the type chip and the value share a colour (the Cache-key card's). */
  .t-str { --t: #c9917a; } .t-int { --t: #a9c49a; } .t-float { --t: #86bfae; }
  .t-bool { --t: #7ea6d6; } .t-list { --t: #cdb27a; } .t-dict { --t: #b98fb6; }
  .t-any { --t: #9a9da5; } .t-none { --t: #ccc; }
  .tv { color: var(--t); }
  .tchip {
    display: inline-block; padding: 0 5px; border-radius: 3px; font-family: Consolas, monospace;
    font-size: 9px; line-height: 14px; letter-spacing: 0.3px; color: var(--t); white-space: nowrap;
    background: color-mix(in srgb, var(--t) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--t) 30%, transparent);
  }
</style>
