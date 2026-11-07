<!--
  A run's stdout/stderr, as every Output tab shows it.

  A bar above the log counts its lines, can carry a short note (Connecting…,
  Connection lost) and the caller's own controls, and copies the lines it
  shows (the stream sends a log's tail unless the caller loads it in full).
  The box is a whole number of lines tall, so its edge never cuts a line:
  12 lines until the user asks for more, then 22 with a scrollbar. "Show
  more" appears only when the log is longer than the short box. A finished
  log opens at its first line; a live one follows its newest line while the
  reader is at the bottom.
-->
<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';

  type LogLine = { kind: 'stdout' | 'stderr'; line: string };

  interface Props {
    lines: LogLine[];
    /** The run is still writing: keep the newest line in view. */
    live?: boolean;
    /** A short state note shown beside the line count. */
    note?: string | null;
    noteTone?: 'muted' | 'failed';
    /** Extra controls placed before Copy (e.g. Load full log). */
    actions?: Snippet;
  }

  let { lines, live = false, note = null, noteTone = 'muted', actions }: Props = $props();

  let pre: HTMLPreElement | undefined = $state();
  let open = $state(false);
  let overflows = $state(false);
  let copied = $state(false);
  let copiedTimer: ReturnType<typeof setTimeout> | undefined;
  // A live log follows its newest line only while the reader is at the
  // bottom; scrolling up to read stops it until they scroll back down.
  let atBottom = true;

  function onScroll(): void {
    if (!pre) return;
    atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 20;
  }

  /** Longer than the short box? Measured only while the box is short. */
  function measure(): void {
    if (!pre || open) return;
    overflows = pre.scrollHeight > pre.clientHeight + 1;
  }

  $effect(() => {
    void lines.length;
    void open;
    measure();
    if (pre && live && atBottom) pre.scrollTop = pre.scrollHeight;
  });

  // When a followed run finishes in the short box, it goes back to its first
  // line, as a finished log opens: the short box cannot be scrolled, so the
  // lines above would otherwise be out of reach under a fade.
  let wasLive = false;
  $effect(() => {
    const nowLive = live;
    if (wasLive && !nowLive && pre && !open) {
      pre.scrollTop = 0;
      atBottom = pre.scrollHeight - pre.clientHeight < 20;
    }
    wasLive = nowLive;
  });

  onDestroy(() => clearTimeout(copiedTimer));

  $effect(() => {
    // The panel can be resized, which rewraps the lines.
    if (!pre || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(pre);
    return () => ro.disconnect();
  });

  /** Show more / Show less. Closing goes back to where a short box starts:
   *  the first line for a finished log, the newest (and following) for a
   *  live one, since the short box cannot be scrolled. */
  function toggle(): void {
    open = !open;
    if (open || !pre) return;
    if (live) {
      atBottom = true;
      pre.scrollTop = pre.scrollHeight;
    } else {
      pre.scrollTop = 0;
    }
  }

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(lines.map(l => l.line).join('\n'));
    } catch {
      return;
    }
    copied = true;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => { copied = false; }, 1500);
  }
</script>

<div class="log-box">
  <div class="log-bar">
    <span class="log-count" data-testid="log-count">{lines.length} {lines.length === 1 ? 'line' : 'lines'}</span>
    {#if note}<span class="log-note {noteTone}" data-testid="log-note">{note}</span>{/if}
    <span class="log-spacer"></span>
    {@render actions?.()}
    <button type="button" class="log-btn" class:done={copied} onclick={copy}>{copied ? '✓ Copied' : 'Copy'}</button>
  </div>
  <div class="log-frame" class:fade={overflows && !open && !live} class:has-more={overflows}>
    <pre bind:this={pre} class="log-pane" class:open data-testid="log-pane" onscroll={onScroll}>{#each lines as l}<span class="log-{l.kind}">{l.line}
</span>{/each}</pre>
  </div>
  {#if overflows}
    <button type="button" class="log-more" aria-expanded={open} onclick={toggle}>{open ? 'Show less ▴' : 'Show more ▾'}</button>
  {/if}
</div>

<style>
  .log-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 4px 2px 8px;
    background: var(--bg-header);
    border: 1px solid var(--border);
    border-bottom: none;
    border-radius: 3px 3px 0 0;
    font-size: 10.5px;
  }
  .log-count { color: var(--text-muted); font-family: 'Consolas', 'Courier New', monospace; white-space: nowrap; }
  /* The bar stays one line in the narrow inspector: the caller's controls
     (the run id) give way, never the count or the note. */
  .log-note { color: var(--text-secondary); white-space: nowrap; flex-shrink: 0; }
  .log-note.failed { color: var(--status-failed-ink); }
  .log-spacer { flex: 1; min-width: 0; }
  .log-btn, .log-more { white-space: nowrap; }
  .log-btn {
    font: inherit;
    font-size: 10.5px;
    line-height: 17px;
    padding: 0 8px;
    color: var(--text-primary);
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
  }
  .log-btn:hover { background: var(--border); }
  .log-btn.done { color: var(--status-ok); }

  /* The padding sits on the frame so the <pre> is exactly N lines tall and
     its edge never shows part of the next line. */
  .log-frame {
    position: relative;
    padding: 8px 10px;
    background: rgba(0, 0, 0, 0.25);
    border: 1px solid var(--border);
    border-radius: 0 0 3px 3px;
  }
  .log-frame.has-more { border-radius: 0; }
  .log-frame.fade::after {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 28px;
    pointer-events: none;
    /* The frame's own colour: its 25% black over the panel. */
    background: linear-gradient(to bottom, transparent, color-mix(in srgb, var(--bg-panel), #000 25%));
  }
  .log-pane {
    margin: 0;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: calc(12 * 1.45em);
    overflow: hidden;
    scrollbar-width: thin;
  }
  .log-pane.open {
    max-height: calc(22 * 1.45em);
    overflow-y: auto;
  }
  .log-stdout { color: var(--text-primary); }
  .log-stderr { color: var(--status-failed-ink); }

  .log-more {
    display: block;
    width: 100%;
    font: inherit;
    font-size: 11px;
    padding: 2px 0 1px;
    color: var(--accent);
    background: var(--bg-header);
    border: 1px solid var(--border);
    border-top: none;
    border-radius: 0 0 3px 3px;
    cursor: pointer;
  }
  .log-more:hover { background: var(--border); }
</style>
