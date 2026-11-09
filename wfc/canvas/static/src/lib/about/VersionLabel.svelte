<script lang="ts">
  /**
   * The `v<installed>` label beside the Toolbar title and the version menu
   * it opens (request decisions 4, 16, 17, 19, 21, 22).
   *
   * The label is small muted text with a ▾ chevron; an accent dot follows
   * the version while an update is offered that the menu was not opened
   * for, and the tooltip says "Update available: <v>". The menu opens under
   * the title from the left: the update block (when there is an offer), the
   * "Your version" lines that open What's new, Report a bug, the guide links
   * and the footer saying what the check does and which `wf-canvas.toml` key
   * changes it. The page asks `/api/about` on mount and whenever the tab is
   * shown again; until it first answers the label is not drawn.
   */
  import { onMount } from 'svelte';
  import ChangelogSections from './ChangelogSections.svelte';
  import InlineText from './InlineText.svelte';
  import WhatsNewDialog from './WhatsNewDialog.svelte';
  import { about, menuOpened, openWhatsNew, refreshAbout, seenOffer } from './aboutStore.js';
  import { fetchBugReportUrl } from './api.js';
  import { footerParts, knownIssuesCount, showsDot } from './versionMenu.js';
  import { showFlash } from '../builder/stores.js';

  let open = $state(false);
  let notesOpen = $state(true);
  let copied = $state(false);
  let wrapEl = $state<HTMLElement | undefined>();

  const offer = $derived($about?.offer ?? null);
  const dot = $derived(showsDot(offer, $seenOffer));
  const installedEntry = $derived($about?.changelog.versions[0]);
  const kiCount = $derived(knownIssuesCount(installedEntry));
  const foot = $derived($about ? footerParts($about.footer) : null);
  const labelText = $derived($about?.version ? `v${$about.version}` : 'wfc');
  const tooltip = $derived(offer ? `Update available: ${offer.version}`
    : $about?.version ? `wfc ${$about.version}` : 'wfc');

  onMount(() => {
    void refreshAbout();
    const onVisible = () => { if (document.visibilityState === 'visible') void refreshAbout(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  });

  function toggle(): void {
    open = !open;
    if (open) menuOpened();
  }

  function close(): void { open = false; }

  function onWindowClick(e: MouseEvent): void {
    if (!open) return;
    if (wrapEl && e.target instanceof Node && wrapEl.contains(e.target)) return;
    open = false;
  }

  function onKey(e: KeyboardEvent): void {
    if (open && e.key === 'Escape') open = false;
  }

  function whatsNewFromMenu(atKnownIssues: boolean): void {
    close();
    openWhatsNew(atKnownIssues);
  }

  async function copyCommand(): Promise<void> {
    if (!offer) return;
    try {
      await navigator.clipboard.writeText(offer.command);
      copied = true;
      setTimeout(() => { copied = false; }, 1500);
    } catch {
      // Clipboard refused: nothing is copied.
    }
  }

  /**
   * Report a bug: the tab opens now (inside the click, so no popup blocker
   * stops it) and goes to the new-issue URL once the server has built it.
   */
  async function reportBug(): Promise<void> {
    close();
    const tab = window.open('', '_blank');
    try {
      const url = await fetchBugReportUrl();
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
      } else {
        window.open(url, '_blank', 'noopener');
      }
    } catch (err) {
      tab?.close();
      showFlash(`Could not open the bug report: ${err instanceof Error ? err.message : String(err)}`, 'error', 5000);
    }
  }
</script>

<svelte:window onclick={onWindowClick} onkeydown={onKey} />

{#if $about}
  <div class="ver-wrap" bind:this={wrapEl}>
    <button class="ver-b" class:open data-testid="version-label" title={tooltip}
            aria-haspopup="menu" aria-expanded={open} onclick={toggle}>
      {labelText}
      {#if dot}<span class="dot" data-testid="version-dot"></span>{/if}
      <span class="chev">&#9662;</span>
    </button>
    {#if open}
      <div class="menu" role="menu" data-testid="version-menu">
        {#if offer}
          <div class="sec update" data-testid="version-update">
            <div class="up-title"><span class="dot"></span> Update available: {offer.version}</div>
            <div class="cmd">
              <code data-testid="update-command">{offer.command}</code>
              <button class="copy" onclick={copyCommand}>{copied ? 'Copied' : 'Copy'}</button>
            </div>
            <div class="item"><a class="link ext" href={$about.links.plugin_install} target="_blank"
              rel="noopener noreferrer" data-testid="update-plugin">Then update the wfc plugin too</a></div>
            {#if offer.notes.sections.length}
              <button class="item row-btn" data-testid="update-notes-toggle" aria-expanded={notesOpen}
                      onclick={() => { notesOpen = !notesOpen; }}>
                <span>What's in {offer.version}</span>
                <span class="chev-r">{notesOpen ? '▾' : '▸'}</span>
              </button>
              {#if notesOpen}
                <div class="expand" data-testid="update-notes">
                  <ChangelogSections sections={offer.notes.sections} />
                  <div class="small"><a class="link ext" href={offer.notes.link} target="_blank"
                    rel="noopener noreferrer">All of {offer.version}'s notes</a></div>
                </div>
              {/if}
            {:else}
              <div class="item"><a class="link ext" href={offer.notes.link} target="_blank"
                rel="noopener noreferrer" data-testid="update-notes-link">What's in {offer.version}</a></div>
            {/if}
          </div>
        {/if}
        <div class="sec">
          <div class="sub-h">Your version &middot; <span class="ver-t">{$about.version ?? 'unknown'}</span></div>
          {#if installedEntry}
            <button class="item row-btn" data-testid="menu-whats-new" onclick={() => whatsNewFromMenu(false)}>
              <span>What's new</span><span class="chev-r">&rsaquo;</span>
            </button>
            <button class="item row-btn" data-testid="menu-known-issues" onclick={() => whatsNewFromMenu(true)}>
              <span>Known issues<span class="count" data-testid="known-issues-count">{kiCount}</span></span>
              <span class="chev-r">&rsaquo;</span>
            </button>
          {:else}
            <div class="item"><a class="link ext" href={$about.changelog.link ?? $about.links.changelog}
              target="_blank" rel="noopener noreferrer" data-testid="menu-whats-new-link">What's new</a></div>
            <div class="item"><a class="link ext" href={$about.links.known_issues}
              target="_blank" rel="noopener noreferrer">Known issues</a></div>
          {/if}
        </div>
        <div class="sec">
          <button class="item row-btn" data-testid="report-bug" onclick={reportBug}>
            <span class="ext">Report a bug</span><span class="hint">opens GitHub, nothing sent</span>
          </button>
          <div class="item links-row">
            <a class="link ext" href={$about.links.docs} target="_blank" rel="noopener noreferrer"
               data-testid="link-docs">Docs</a>
            <a class="link ext" href={$about.links.known_issues} target="_blank" rel="noopener noreferrer"
               data-testid="link-known-issues">Known Issues page</a>
            <a class="link ext" href={$about.links.changelog} target="_blank" rel="noopener noreferrer"
               data-testid="link-changelog">Full changelog</a>
          </div>
        </div>
        {#if foot}
          <div class="foot" data-testid="version-footer"><!--
            -->{#each foot.before as t, i (i)}{#if t.kind === 'code'}<code>{t.text}</code>{:else}<InlineText text={t.text} />{/if}{/each}<!--
            -->{#if foot.link}<a class="link ext" href={$about.links.updates_guide} target="_blank"
               rel="noopener noreferrer" data-testid="footer-link">{foot.link}</a>{/if}
          </div>
        {/if}
      </div>
    {/if}
  </div>
{/if}

<WhatsNewDialog />

<style>
  .ver-wrap { display: flex; align-items: center; }
  .ver-b {
    font-size: 12px; color: var(--text-secondary); background: none; border: 1px solid transparent;
    border-radius: 4px; padding: 1px 6px; display: flex; align-items: center; gap: 5px; cursor: pointer;
    font-family: inherit; white-space: nowrap;
  }
  .ver-b:hover, .ver-b.open { border-color: var(--border); color: var(--text-primary); background: var(--bg-header); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); flex-shrink: 0; display: inline-block; }
  .chev { color: var(--text-secondary); font-size: 10px; margin-left: 1px; }
  /* The menu, in the archive badge's popover style; placed against the
     title-wrap (the nearest positioned ancestor), under the title, from the left. */
  .menu {
    position: absolute; left: 0; top: calc(100% + 14px); width: 320px; background: var(--bg-panel);
    border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
    z-index: 1000; text-align: left; font-size: 13px; color: var(--text-primary); cursor: default;
  }
  .sec { padding: 8px 13px; border-top: 1px solid var(--border); }
  .sec:first-child { border-top: none; }
  .update { background: color-mix(in srgb, var(--accent) 9%, transparent); border-radius: 8px 8px 0 0; }
  .up-title { display: flex; align-items: center; gap: 7px; color: #e8e8e8; font-weight: 700; padding-top: 2px; }
  .cmd {
    display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 7px 0 4px;
    background: var(--bg-input); border: 1px solid var(--border); border-radius: 4px; padding: 4px 8px;
  }
  .cmd code { color: #ddd; font-size: 11.5px; font-family: ui-monospace, Consolas, monospace; }
  .copy { font-size: 11px; color: var(--accent); background: none; border: none; cursor: pointer; padding: 0; }
  .item { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; }
  .row-btn {
    width: 100%; background: none; border: none; color: var(--text-primary); font: inherit;
    font-size: 13px; cursor: pointer; text-align: left;
  }
  .row-btn:hover { color: #fff; }
  .hint { color: var(--text-secondary); font-size: 11px; }
  .link { color: var(--accent); text-decoration: none; }
  .link:hover { text-decoration: underline; }
  .ext::after { content: " \2197"; color: var(--text-secondary); font-size: 10px; }
  .count {
    background: var(--bg-header); border: 1px solid var(--border); border-radius: 999px; padding: 0 6px;
    font-size: 11px; margin-left: 4px;
  }
  .expand {
    margin: 2px 0 4px; padding: 7px 9px; background: var(--bg-input); border: 1px solid var(--border);
    border-radius: 6px;
  }
  .small { margin-top: 4px; font-size: 12px; }
  .links-row { justify-content: flex-start; gap: 14px; font-size: 12px; }
  .sub-h {
    font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-secondary);
    padding: 2px 0;
  }
  .ver-t { text-transform: none; }
  .chev-r { color: var(--text-secondary); font-size: 15px; line-height: 1; }
  .foot {
    padding: 6px 13px 7px; border-top: 1px solid var(--border); font-size: 11px; color: var(--text-muted);
    background: var(--bg-input); border-radius: 0 0 8px 8px;
  }
  .foot code { font-family: ui-monospace, Consolas, monospace; font-size: 10.5px; color: #aaa; white-space: nowrap; }
  .foot .link { font-size: 11px; white-space: nowrap; }
</style>
