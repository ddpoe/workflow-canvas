<!--
  The side-panel error box: "✗ This step failed", a See output button, and
  the run's message under it cut to two lines (all of it in the hover title;
  a traceback summarised to its exception line).

  The Inspector shows it above its tabs and the run detail panel inside its
  hero. Its failed tint, rule and outline are the colour SOP's side-panel
  error box (Governing Rule, outside nodes and cards); See output is a
  control, so it is the controls blue, never red beside a status pill.
-->
<script lang="ts">
  import { errorSummary } from './errorSummary.js';

  interface Props {
    /** The run's error message, if it has one. */
    message: string | null | undefined;
    /** Opens the Output tab; the button is left out without it. */
    onSeeOutput?: () => void;
    testid: string;
  }

  let { message, onSeeOutput, testid }: Props = $props();
</script>

<div class="err-box" data-testid={testid}>
  <div class="err-box-head">
    <span class="err-box-icon" aria-hidden="true">✗</span>
    <span class="err-box-title">This step failed</span>
    {#if onSeeOutput}
      <button type="button" class="see-output" onclick={onSeeOutput}>See output ›</button>
    {/if}
  </div>
  {#if message}
    <div class="err-box-msg" title={message}>{errorSummary(message)}</div>
  {/if}
</div>

<style>
  .err-box {
    padding: 6px 8px 7px;
    background: color-mix(in srgb, var(--status-failed) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--status-failed) 25%, transparent);
    border-left: 3px solid var(--status-failed);
    border-radius: 3px;
    font-size: 11.5px;
  }
  .err-box-head { display: flex; align-items: center; gap: 6px; }
  .err-box-icon { color: var(--status-failed-ink); font-weight: 700; }
  .err-box-title { color: var(--status-failed-ink); font-weight: 600; flex: 1; }
  /* Two full-width lines; long tokens break anywhere so the lines run to
     the edge. */
  .err-box-msg {
    margin-top: 3px;
    color: #ecc;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    line-height: 1.4;
    overflow-wrap: anywhere;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .see-output {
    flex: none;
    font: inherit;
    font-size: 10px;
    font-weight: 600;
    line-height: 16px;
    padding: 0 7px;
    border-radius: 3px;
    white-space: nowrap;
    cursor: pointer;
    color: var(--accent);
    border: 1px solid color-mix(in oklab, var(--accent) 55%, transparent);
    background: color-mix(in oklab, var(--accent) 14%, transparent);
  }
  .see-output:hover { background: color-mix(in oklab, var(--accent) 26%, transparent); }
</style>
