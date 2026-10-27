<script lang="ts">
  /**
   * The two-squares copy icon: copies `value` to the clipboard and shows a
   * check for a moment. A clipboard that refuses (an insecure context, no
   * permission) copies nothing and shows no check.
   */
  let { value, label, testid = 'copy' }: { value: string; label: string; testid?: string } = $props();

  let done = $state(false);
  let timer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => () => { if (timer) clearTimeout(timer); });

  async function copy(e: MouseEvent): Promise<void> {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      done = true;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { done = false; timer = null; }, 1500);
    } catch {
      // Clipboard refused: nothing is copied.
    }
  }
</script>

<button type="button" class="copy-ico" class:done data-testid={testid} data-copy={value}
        aria-label={label} title={done ? 'Copied' : label} onclick={copy}>
  {#if done}
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

<style>
  .copy-ico {
    display: inline-flex; align-items: center; justify-content: center; vertical-align: middle;
    width: 15px; height: 15px; padding: 0; margin-left: 4px; border: none; border-radius: 3px;
    background: none; color: #7d7d85; cursor: pointer; flex-shrink: 0;
  }
  .copy-ico:hover, .copy-ico:focus-visible { color: var(--accent); background: rgba(74, 144, 217, 0.12); }
  .copy-ico.done { color: var(--color-completed); }
  .copy-ico svg { display: block; }
</style>
