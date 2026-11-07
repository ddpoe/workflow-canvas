<script lang="ts">
  /**
   * Where envs are registered: the command line. The canvas cannot register
   * an env yet, so the Envs tab's Register opens this (as a dialog, with
   * Close) and the empty Envs tab shows it inline: the command to copy and
   * the guide that walks through it.
   */
  let { onClose = undefined, headingId = undefined }: {
    onClose?: () => void;
    headingId?: string;
  } = $props();

  const COMMAND = 'wfc register-env --help';
  const GUIDE = 'https://workflow-canvas.readthedocs.io/en/stable/tutorials/registering-an-environment.html';

  let copied = $state(false);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(COMMAND);
      copied = true;
      setTimeout(() => { copied = false; }, 1500);
    } catch {
      // Clipboard refused: nothing is copied.
    }
  }
</script>

<div class="pointer" data-testid="env-cli-pointer">
  <h3 id={headingId}>Envs are registered from the command line</h3>
  <p>The canvas can't register an env yet. Run this in your project folder to see the
    options for pixi, conda, uv and your own image:</p>
  <div class="cmd"><code data-testid="env-cli-command">{COMMAND}</code>
    <button type="button" class="copy" onclick={copy}>{copied ? 'Copied' : 'Copy'}</button></div>
  <p>A registered env shows up here after <b>Refresh</b>.</p>
  <div class="foot">
    <a href={GUIDE} target="_blank" rel="noopener noreferrer">Registering an Environment (guide) ↗</a>
    {#if onClose}<button type="button" class="close" onclick={onClose}>Close</button>{/if}
  </div>
</div>

<style>
  .pointer { max-width: 560px; }
  h3 { margin: 0 0 6px; font-size: 15px; color: var(--text-primary); font-weight: 600; }
  p { color: var(--text-secondary); font-size: 12.5px; line-height: 1.5; margin: 6px 0; }
  b { color: var(--text-primary); }
  .cmd {
    display: flex; align-items: center; gap: 8px; margin: 10px 0; padding: 8px 10px;
    background: #111; border: 1px solid var(--border); border-radius: 5px;
  }
  code { font-family: "JetBrains Mono", Consolas, monospace; font-size: 12.5px; color: var(--text-primary); }
  .copy, .close {
    margin-left: auto; background: transparent; border: 1px solid var(--border); border-radius: 4px;
    color: var(--text-primary); font-size: 11.5px; padding: 2px 9px; cursor: pointer;
  }
  .copy:hover, .close:hover { background: var(--bg-header); }
  .foot { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; gap: 12px; }
  a { color: var(--accent); font-size: 12.5px; }
</style>
