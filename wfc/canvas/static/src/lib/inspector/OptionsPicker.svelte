<script lang="ts">
  /**
   * The value box of an `options_from` row: the text box plus a chevron that
   * opens a list of what the upstream steps offer.
   *
   * The list has one section per wired upstream that declares options
   * (`./options.ts::optionSections`), each with a header band (the node
   * chip, the input it arrives on, its count) and its Fixed, Built-from and
   * Patterns groups. With several sections each folds; with one, none does.
   *
   * Opening: the chevron, a click in the box, or ArrowDown. Focus alone never
   * opens it, because a row's box takes focus when it unlocks. Typing filters
   * the open list (and opens it). ArrowDown and ArrowUp move the highlight;
   * Enter picks the highlighted option, or, with nothing highlighted, closes
   * the list and hands the key to the row (which locks it). Esc closes an
   * open list and keeps the text; with the list closed it goes to the row.
   * A pattern fills its fixed part and keeps the list open.
   *
   * The box always takes free text: a value not among the options is a value
   * like any other, and nothing here can block a lock or a run.
   */
  import { untrack } from 'svelte';
  import {
    chevronState,
    filterSection,
    noMatch,
    openSectionKeys,
    optionSections,
    patternFixedPart,
    sectionIsEmpty,
    sectionsFold,
    tickedOptions,
    builtPillTitle,
    type OptionSection,
    type ParamOptions,
  } from './options.js';
  import { followBuiltFrom } from './builtFromLink.js';

  let {
    value,
    locked,
    options,
    placeholder = 'option',
    onInput,
    onKeydown,
  }: {
    /** The row's text: its draft while unlocked, its value while locked. */
    value: string;
    locked: boolean;
    /** The row's lookup answer; absent while the lookup is pending. */
    options: ParamOptions | undefined;
    placeholder?: string;
    /** A new text for the row: typed, picked or filled from a pattern. */
    onInput: (value: string) => void;
    /** A key the picker does not consume (Enter to lock, Esc to cancel). */
    onKeydown?: (e: KeyboardEvent) => void;
  } = $props();

  interface Item {
    kind: 'opt' | 'pat';
    /** The option, or the pattern as declared. */
    text: string;
    sectionKey: string;
  }

  let sections = $derived(optionSections(options));
  let chev = $derived(chevronState(options, sections, locked));
  let folds = $derived(sectionsFold(sections));

  let open = $state(false);
  let filter = $state('');
  let hl = $state(-1);
  let openKeys = $state<string[]>([]);
  let inputEl: HTMLInputElement | undefined = $state();
  let rootEl: HTMLDivElement | undefined = $state();

  let shown = $derived(sections.map(s => filterSection(s, filter)));
  let items = $derived.by<Item[]>(() => {
    const out: Item[] = [];
    for (const s of shown) {
      if (!openKeys.includes(s.key)) continue;
      for (const n of s.fixed) out.push({ kind: 'opt', text: n, sectionKey: s.key });
      for (const g of s.built) for (const n of g.names) out.push({ kind: 'opt', text: n, sectionKey: s.key });
      for (const p of s.patterns) out.push({ kind: 'pat', text: p, sectionKey: s.key });
    }
    return out;
  });

  // A locked row, or one that lost its sections, never keeps a list open.
  $effect(() => {
    if (!chev.enabled && open) closeList();
  });

  // ✎ unlock puts the caret in the box, as every other editor's autofocus
  // does. Focus never opens the list.
  let focused = $state(false);
  let wasLocked = untrack(() => locked);
  $effect(() => {
    const now = locked;
    if (wasLocked && !now) inputEl?.focus();
    wasLocked = now;
  });

  function openList(): void {
    if (!chev.enabled) return;
    open = true;
    filter = '';
    hl = -1;
    openKeys = openSectionKeys(sections, value);
  }

  function closeList(): void {
    open = false;
    filter = '';
    hl = -1;
  }

  function itemIndex(sectionKey: string, kind: Item['kind'], text: string): number {
    return items.findIndex(i => i.sectionKey === sectionKey && i.kind === kind && i.text === text);
  }

  function choose(item: Item): void {
    if (item.kind === 'pat') {
      const fixed = patternFixedPart(item.text);
      onInput(fixed);
      filter = fixed;
      hl = -1;
      inputEl?.focus();
      return;
    }
    onInput(item.text);
    closeList();
  }

  function toggleSection(key: string): void {
    if (!folds) return;
    openKeys = openKeys.includes(key) ? openKeys.filter(k => k !== key) : [...openKeys, key];
    hl = -1;
  }

  function onChevronDown(e: MouseEvent): void {
    e.preventDefault();
    if (!chev.enabled) return;
    if (open) closeList();
    else openList();
    inputEl?.focus();
  }

  function onBoxClick(): void {
    if (!open) openList();
  }

  function onBoxInput(e: Event): void {
    const text = (e.currentTarget as HTMLInputElement).value;
    onInput(text);
    if (!chev.enabled) return;
    if (!open) openList();
    filter = text;
    hl = -1;
  }

  function onBoxKeydown(e: KeyboardEvent): void {
    if (locked) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) openList();
      else hl = Math.min(hl + 1, items.length - 1);
    } else if (e.key === 'ArrowUp') {
      if (!open) return;
      e.preventDefault();
      hl = Math.max(hl - 1, -1);
    } else if (e.key === 'Enter') {
      if (open && hl >= 0 && items[hl]) {
        e.preventDefault();
        choose(items[hl]);
        return;
      }
      if (open) closeList();
      onKeydown?.(e);
    } else if (e.key === 'Escape') {
      if (open) {
        e.preventDefault();
        e.stopPropagation();
        closeList();
        return;
      }
      onKeydown?.(e);
    }
  }

  function onFocusOut(e: FocusEvent): void {
    const next = e.relatedTarget as Node | null;
    if (next && rootEl?.contains(next)) return;
    closeList();
  }

  function pick(e: MouseEvent, item: Item): void {
    e.preventDefault();
    choose(item);
  }

  // A Built-from pill goes to the parameter that built the names: the
  // upstream node's Inspector row, or the run's Params tab in History. The
  // row's draft is kept across the switch (`rowActors.ts`), never committed.
  function pill(e: MouseEvent, section: OptionSection, param: string): void {
    e.preventDefault();
    e.stopPropagation();
    closeList();
    followBuiltFrom(section, param);
  }
</script>

<div class="opts-picker" class:focus={!locked && (open || focused)}
  bind:this={rootEl} onfocusout={onFocusOut} data-testid="options-picker">
  <input class="text" type="text"
    bind:this={inputEl}
    onfocus={() => (focused = true)}
    onblur={() => (focused = false)}
    value={value}
    readonly={locked}
    autocomplete="off"
    spellcheck="false"
    placeholder={locked ? '' : placeholder}
    data-testid="options-input"
    onclick={onBoxClick}
    oninput={onBoxInput}
    onkeydown={onBoxKeydown} />
  <button type="button" class="caret"
    class:open
    class:off={chev.dimmed}
    disabled={!chev.enabled}
    title={chev.hover || undefined}
    aria-label={chev.hover || 'Options'}
    aria-expanded={open}
    data-testid="options-chevron"
    onmousedown={onChevronDown}>
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 L5 6.5 L8 3.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
  </button>

  {#if open}
    <div class="opts-list" class:multi={folds} data-testid="options-list" role="listbox">
      {#each shown as s (s.key)}
        {@const isOpen = openKeys.includes(s.key)}
        {@const ticked = tickedOptions(s, value)}
        <div class="sect" class:shut={!isOpen} data-testid="options-section" data-section-key={s.key}>
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <div class="shead" class:folds role="presentation"
            data-testid="options-section-head"
            onmousedown={(e) => { e.preventDefault(); toggleSection(s.key); }}>
            {#if folds}
              <span class="sc"><svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 L5 6.5 L8 3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
            {/if}
            <span class="lbl">Options of</span>
            <span class="chip" style:--m={s.chip.color}><span class="n">{s.chip.text}</span></span>
            <span class="via">via <span class="mono">{s.input}</span></span>
            <span class="scount">{s.count}</span>
          </div>
          {#if isOpen && !sectionIsEmpty(s)}
            <div class="sbody">
              {#if s.fixed.length > 0}
                <div class="grp">Fixed</div>
                {#each s.fixed as n}
                  {@const i = itemIndex(s.key, 'opt', n)}
                  <div class="opt" class:cur={ticked.names.includes(n)} class:hl={i === hl}
                    role="option" aria-selected={ticked.names.includes(n)}
                    tabindex="-1" data-testid="options-item"
                    onmousedown={(e) => pick(e, { kind: 'opt', text: n, sectionKey: s.key })}>{n}</div>
                {/each}
              {/if}
              {#each s.built as g}
                <div class="grp">
                  <span>Built from</span>
                  {#each g.params as p}
                    <a class="kv plink" href={'#'} role="button" tabindex="-1"
                      title={builtPillTitle(s, p)}
                      data-testid="options-param-pill"
                      onmousedown={(e) => e.preventDefault()}
                      onclick={(e) => pill(e, s, p)}><span class="k">param</span><span class="v">{p} ↗</span></a>
                  {/each}
                </div>
                {#each g.names as n}
                  {@const i = itemIndex(s.key, 'opt', n)}
                  <div class="opt" class:cur={ticked.names.includes(n)} class:hl={i === hl}
                    role="option" aria-selected={ticked.names.includes(n)}
                    tabindex="-1" data-testid="options-item"
                    onmousedown={(e) => pick(e, { kind: 'opt', text: n, sectionKey: s.key })}>{n}</div>
                {/each}
              {/each}
              {#if s.patterns.length > 0}
                <div class="grp">Patterns</div>
                {#each s.patterns as p}
                  {@const i = itemIndex(s.key, 'pat', p)}
                  {@const parts = p.split('*')}
                  <div class="pat" class:cur={ticked.patterns.includes(p)} class:hl={i === hl}
                    role="option" aria-selected={ticked.patterns.includes(p)}
                    tabindex="-1" data-testid="options-item"
                    title={`Fill ${patternFixedPart(p)}`}
                    onmousedown={(e) => pick(e, { kind: 'pat', text: p, sectionKey: s.key })}><span>{#each parts as part, k}{part}{#if k < parts.length - 1}<span class="star">*</span>{/if}{/each}</span></div>
                {/each}
              {/if}
            </div>
          {/if}
        </div>
      {/each}
      {#if noMatch(shown)}
        <div class="none" data-testid="options-no-match">No match</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  /* Colours: the canvas tokens and the neutral greys the value box already
     uses. The controls blue marks the open chevron and the param links; the
     node chip is filled with the upstream's module colour. */
  .opts-picker {
    display: flex; align-items: center; gap: 6px;
    flex: 1; min-width: 0;
  }
  input.text {
    flex: 1; min-width: 0; width: 100%;
    background: transparent; border: none; outline: none;
    color: #ddd; padding: 6px 0;
    font-family: Consolas, monospace; font-size: 12px;
    box-sizing: border-box; min-height: 30px;
  }
  input.text::placeholder { color: var(--text-muted); }
  input.text:read-only { color: #aaa; cursor: default; }

  .caret {
    width: 22px; height: 22px; flex: none; padding: 0;
    display: flex; align-items: center; justify-content: center;
    border-radius: 3px; color: #aaa; background: #2a2a2d;
    border: 1px solid var(--border); cursor: pointer;
  }
  .caret:hover:not(:disabled) { color: #ddd; border-color: #555; }
  .caret.open { color: #ddd; border-color: var(--accent); }
  .caret.open svg { transform: rotate(180deg); }
  .caret.off { color: #555; background: transparent; border-color: #2a2a2d; cursor: not-allowed; }

  .opts-list {
    position: absolute; left: -1px; right: -1px; top: calc(100% + 3px); z-index: 20;
    background: #1b1b1d; border: 1px solid #555; border-radius: 5px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, .65);
    max-height: 320px; overflow: auto; padding: 0 0 4px;
    font-family: inherit;
  }
  .sect + .sect { border-top: 2px solid #555; }
  .shead {
    display: flex; align-items: center; gap: 6px;
    padding: 7px 10px; background: #2a2a2d;
    font-size: 11px; color: #aaa; white-space: nowrap; min-width: 0;
    border-bottom: 1px solid var(--border);
  }
  /* One line: the chip and the count keep their width; "via <input>" gives
     way first, with an ellipsis, and the label only once it is gone. "via"
     has no width of its own (basis 0) and takes only the space left over,
     so the label shrinks only when nothing is left for it. */
  .shead > :not(.via, .lbl) { flex: none; }
  .lbl { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .shead.folds { cursor: pointer; user-select: none; }
  .shead.folds:hover { background: var(--bg-header); }
  .sc { display: inline-flex; color: #bbb; transition: transform .12s; }
  .sect.shut .sc { transform: rotate(-90deg); }
  .sect.shut .shead { border-bottom: none; }
  .via {
    color: var(--text-secondary); font-size: 10.5px;
    flex: 1 1 0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .mono { font-family: Consolas, monospace; color: #bbb; }
  .scount { margin-left: auto; color: var(--text-secondary); font-size: 10.5px; }

  /* The node chip: a module chip, filled with the module colour, white
     text. A label, not a link. */
  .chip {
    display: inline-flex; align-items: stretch; border-radius: 3px; overflow: hidden;
    font-size: 10.5px; line-height: 16px; white-space: nowrap;
    border: 1px solid var(--m); flex: none;
  }
  .chip .n {
    background: var(--m); color: #fff; padding: 0 6px;
    font-family: Consolas, monospace; overflow: hidden; text-overflow: ellipsis;
  }

  .sbody { padding: 2px 0 0; }
  .grp {
    display: flex; align-items: center; gap: 4px; white-space: nowrap;
    padding: 6px 10px 2px; font-size: 9.5px; color: var(--text-secondary);
    letter-spacing: .08em; text-transform: uppercase;
    border-top: 1px solid var(--border); margin-top: 3px;
  }
  .sbody > .grp:first-child { border-top: none; margin-top: 0; }

  .opt, .pat {
    position: relative; display: flex; align-items: center; gap: 6px;
    padding: 4px 10px 4px 22px; cursor: pointer;
    font-family: Consolas, monospace; font-size: 12px; color: #ddd;
  }
  .pat { color: #bbb; }
  .pat .star { color: #777; }
  .opt:hover, .opt.hl, .pat:hover, .pat.hl { background: var(--bg-header); }
  .opt.cur, .pat.cur { color: #fff; font-weight: 600; }
  .opt.cur::before, .pat.cur::before {
    content: "\2713"; position: absolute; left: 8px;
    color: #d4d4d4; font-size: 11px; font-weight: 400;
  }

  /* key | value pill; the value of a link pill is the controls blue. */
  .kv {
    display: inline-flex; border: 1px solid var(--border); border-radius: 3px; overflow: hidden;
    font-size: 10.5px; line-height: 16px; text-transform: none; letter-spacing: 0;
    margin: 0 1px; text-decoration: none; cursor: pointer;
  }
  .kv .k { background: var(--bg-header); color: #999; padding: 0 5px; font-family: inherit; }
  .kv .v { background: var(--bg-input); color: var(--accent); padding: 0 6px; font-family: Consolas, monospace; }
  .kv:hover { border-color: var(--accent); }
  .kv:hover .v { text-decoration: underline; }

  .none { padding: 6px 10px; font-size: 11.5px; color: #999; }
</style>
