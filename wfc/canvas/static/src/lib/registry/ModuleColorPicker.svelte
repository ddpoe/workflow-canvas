<script lang="ts">
  /**
   * A module's Colour picker (SOP Canvas Colours, Module Colours): the
   * sixteen named palette colours and a Custom box for a typed colour.
   *
   * The picker decides nothing about colours. A typed colour is checked by
   * the server (`checkModuleColor`); a refused one shows the server's
   * plain-words reason, a "Use <name>" button with the nearest passing shade
   * the server suggested, and "or pick from the palette." Apply saves the
   * chosen colour (`setModuleColor`), which every view then shows without a
   * reload; Reset to default asks the server for the module's default.
   */
  import LabelTag from '../shared/LabelTag.svelte';
  import { PALETTE } from '../shared/palette.js';
  import { checkModuleColor, setModuleColor } from './api.js';
  import type { ColorCheck } from './api.js';

  let { module, current, sample, onClose }: {
    module: string;
    /** The module's colour now. */
    current: string;
    /** A method name the preview header and card show. */
    sample: string;
    onClose: () => void;
  } = $props();

  // The colour Apply would save; null until the user picks one.
  let chosen = $state<string | null>(null);
  let custom = $state('');
  let refusal = $state<ColorCheck | null>(null);
  let error = $state<string | null>(null);
  let busy = $state(false);

  const shown = $derived(chosen ?? current);
  const canApply = $derived(chosen !== null && refusal === null && error === null && !busy);

  // The protected colours and what each shows, as the SOP words them.
  const RESERVED: { token: string; text: string }[] = [
    { token: '--status-ok', text: 'green = completed' },
    { token: '--status-active', text: 'amber = running' },
    { token: '--status-failed', text: 'red = failed' },
    { token: '--status-cancelled', text: 'slate = cancelled' },
    { token: '--status-cached', text: 'lilac = cached results' },
    { token: '--accent', text: 'blue = buttons and links' },
    { token: '--fill-system', text: 'teal = system nodes' },
  ];

  function same(a: string, b: string): boolean {
    return a.toLowerCase() === b.toLowerCase();
  }

  function pick(hex: string): void {
    chosen = hex;
    custom = '';
    refusal = null;
    error = null;
  }

  async function checkCustom(): Promise<void> {
    const typed = custom.trim();
    if (typed === '') return;
    refusal = null;
    error = null;
    busy = true;
    try {
      const res = await checkModuleColor(typed);
      if (!res.ok) { error = res.error; return; }
      if (res.check.ok) chosen = res.check.color;
      else refusal = res.check;
    } catch (err) {
      error = String(err);
    } finally {
      busy = false;
    }
  }

  function useSuggested(): void {
    const s = refusal?.suggested;
    if (!s) return;
    pick(s);
    custom = s;
  }

  async function save(color: string | null): Promise<void> {
    refusal = null;
    error = null;
    busy = true;
    try {
      const res = await setModuleColor(module, color);
      if (res.ok) { onClose(); return; }
      if ('refusal' in res) refusal = res.refusal;
      else error = res.error;
    } catch (err) {
      error = String(err);
    } finally {
      busy = false;
    }
  }
</script>

<div class="picker" data-testid="colour-picker">
  <div class="k">COLOUR <span class="hint">· one setting, shown in the Builder, Registry and History</span></div>
  <div class="swatches">
    {#each PALETTE as c (c.name)}
      <button type="button" class="sw" class:cur={same(c.hex, shown)} data-testid="palette-swatch"
              title={c.name} aria-label={c.name} aria-pressed={same(c.hex, shown)}
              style:background={c.hex} onclick={() => pick(c.hex)}></button>
    {/each}
  </div>

  <div class="preview" data-testid="colour-preview" style="--m: {shown}">
    Preview:
    <span class="phd">{sample}</span>
    <span class="pcard"><i class="acc"></i>{sample}</span>
    <LabelTag kind="module" label="MODULE" value={module} color={shown} />
  </div>

  <label class="custom">
    <span class="dim">Custom</span>
    <span class="prv" style:background={custom.trim() || 'transparent'}></span>
    <input type="text" data-testid="custom-colour" placeholder="#rrggbb" spellcheck="false"
           class:bad={refusal !== null || error !== null}
           bind:value={custom} onchange={checkCustom} />
  </label>

  {#if refusal}
    <div class="msg" data-testid="colour-refusal">
      {refusal.message}
      {#if refusal.suggested}
        <div class="use-line">
          <button type="button" class="use" data-testid="use-suggested" title="Fills the Custom box and the preview"
                  onclick={useSuggested}><i style:background={refusal.suggested}></i>Use {refusal.suggestedName}</button>
          <span class="dim">or pick from the palette.</span>
        </div>
      {/if}
    </div>
  {/if}
  {#if error}
    <div class="msg" data-testid="colour-error">{error}</div>
  {/if}

  <details class="why" data-testid="colour-why">
    <summary>Why some colours can't be used</summary>
    <div class="why-tx">Some colours can't be used for modules because Workflow Canvas uses them to show status:</div>
    <div class="resv">
      {#each RESERVED as r (r.token)}
        <div class="rr" data-testid="reserved-colour"><span style="background: var({r.token})"></span>{r.text}</div>
      {/each}
    </div>
  </details>

  <div class="btns">
    <button type="button" class="btn" data-testid="colour-reset" disabled={busy} onclick={() => save(null)}>Reset to default</button>
    <button type="button" class="btn" data-testid="colour-cancel" onclick={onClose}>Cancel</button>
    <button type="button" class="btn pri" data-testid="colour-apply" disabled={!canApply}
            onclick={() => { if (chosen) void save(chosen); }}>Apply</button>
  </div>
</div>

<style>
  .picker { border: 1px solid var(--border); border-radius: 6px; background: #232323; padding: 11px 13px; }
  .k { font-size: 10.5px; letter-spacing: .08em; color: #bbb; font-weight: 600; margin-bottom: 6px; }
  .hint { color: var(--text-secondary); font-weight: 400; letter-spacing: 0; }
  .dim { color: var(--text-secondary); }
  .swatches { display: grid; grid-template-columns: repeat(8, 26px); gap: 7px; margin-top: 6px; }
  .sw { width: 26px; height: 26px; border-radius: 5px; border: none; padding: 0; cursor: pointer; }
  .sw.cur { box-shadow: 0 0 0 2px #232323, 0 0 0 4px #fff; }
  .preview { display: flex; gap: 10px; align-items: center; margin-top: 12px; font-size: 11px;
             color: var(--text-secondary); flex-wrap: wrap; }
  .phd { background: var(--m); color: #fff; font-weight: 600; padding: 3px 8px; border-radius: 4px 4px 0 0; }
  .pcard { position: relative; background: #252526; border: 1px solid var(--border); padding: 3px 8px 3px 12px;
           border-radius: 4px; color: #ddd; }
  .pcard .acc { position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--m);
                border-radius: 4px 0 0 4px; }
  .custom { display: flex; gap: 8px; align-items: center; margin-top: 12px; font-size: 11.5px; }
  .prv { width: 26px; height: 22px; border-radius: 3px; border: 1px solid var(--border); }
  .custom input { width: 96px; background: var(--bg-input); border: 1px solid var(--border); border-radius: 4px;
                  padding: 3px 8px; font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px;
                  color: var(--text-primary); outline: none; }
  .custom input.bad { border-color: var(--status-failed); }
  .msg { margin-top: 8px; font-size: 11.5px; padding: 6px 9px; border-radius: 4px; line-height: 1.5;
         color: var(--status-failed-ink); background: color-mix(in srgb, var(--status-failed) 8%, transparent);
         border: 1px solid color-mix(in srgb, var(--status-failed) 35%, transparent); }
  .use-line { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  .use { display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px; border-radius: 3px; font-size: 11.5px;
         border: 1px solid var(--border); background: #2d2d30; color: #eee; cursor: pointer; }
  .use i { width: 12px; height: 12px; border-radius: 2px; display: inline-block; }
  .why { margin-top: 12px; border-top: 1px solid #333; padding-top: 8px; font-size: 11.5px; }
  .why summary { color: var(--accent); cursor: pointer; }
  .why-tx { margin-top: 6px; color: #bbb; line-height: 1.5; }
  .resv { margin-top: 6px; color: var(--text-secondary); display: grid; grid-template-columns: 1fr 1fr; gap: 5px 14px; }
  .rr { display: flex; align-items: center; gap: 8px; }
  .rr span { width: 16px; height: 16px; border-radius: 4px; position: relative; flex: none; }
  .rr span::after { content: ""; position: absolute; left: -2px; right: -2px; top: 7px; height: 2px;
                    background: var(--bg-canvas); transform: rotate(-45deg); }
  .btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px; }
  .btn { background: transparent; border: 1px solid var(--border); color: var(--text-primary); font-size: 11.5px;
         padding: 3px 10px; border-radius: 3px; cursor: pointer; }
  .btn:hover:not(:disabled) { background: var(--bg-header); }
  .btn.pri { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn:disabled { opacity: .5; cursor: default; }
</style>
