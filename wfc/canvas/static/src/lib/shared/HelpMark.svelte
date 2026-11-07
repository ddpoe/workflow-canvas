<!--
  The "?" help marker: a small filled circle (SOP canvas-colours, style
  guide `.qm`). `light` is for a marker on a dark flag or strip, where the
  circle is a faint white instead of a faint black.
-->
<script lang="ts">
  let { light = false, size = 11, title = '' }: { light?: boolean; size?: number; title?: string } = $props();
</script>

<i class="qm" class:light style:width="{size}px" style:height="{size}px"
   style:font-size="{Math.round(size * 0.72)}px" title={title || undefined}
   aria-label="help">?</i>

<style>
  .qm {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.28);
    font-style: normal;
    font-weight: 700;
    line-height: 1;
    margin-left: 4px;
    vertical-align: middle;
    flex: none;
    cursor: help;
  }
  .qm.light { background: rgba(255, 255, 255, 0.14); }
</style>
