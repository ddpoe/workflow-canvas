<script lang="ts">
  /**
   * What's new: a centered dialog that dims the canvas until closed (request
   * decision 20). It lists each version newest first, its sections Upgrading
   * first and Known issues last; opened from the menu's Known issues line it
   * starts scrolled to that version's Known issues. With no versions (the
   * packaged changelog has no section) it shows the Changelog link alone.
   * It closes on ×, Escape, or a click on the dimmed canvas.
   */
  import { tick } from 'svelte';
  import ChangelogSections from './ChangelogSections.svelte';
  import { closeWhatsNew, whatsNew, about } from './aboutStore.js';

  let body = $state<HTMLDivElement | undefined>();
  let top = $state(57);

  $effect(() => {
    const view = $whatsNew;
    if (!view) return;
    // Dim from the Toolbar's bottom edge down, as the mockup does.
    const bar = document.querySelector('.toolbar');
    if (bar) top = Math.round(bar.getBoundingClientRect().bottom);
    if (view.atKnownIssues) {
      void tick().then(() => {
        const ki = body?.querySelector('[data-known-issues]') as HTMLElement | null;
        // The body is the heading's offset parent (position: relative).
        if (ki && body) body.scrollTop = ki.offsetTop - 8;
      });
    }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') closeWhatsNew(); };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  });
</script>

{#if $whatsNew}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="scrim" style="top: {top}px" data-testid="whats-new-scrim" onclick={closeWhatsNew}></div>
  <div class="panel dialog" role="dialog" aria-modal="true" aria-label={$whatsNew.title}
       data-testid="whats-new-dialog">
    <div class="panel-head">
      <span class="t">{$whatsNew.title}</span>
      <button class="x" aria-label="Close" data-testid="whats-new-close" onclick={closeWhatsNew}>&times;</button>
    </div>
    <div class="panel-body" bind:this={body}>
      {#each $whatsNew.versions as v, i (v.version)}
        <div class="vhead" class:first={i === 0} data-testid="whats-new-version">{v.version}
          <span class="date">{v.date}</span></div>
        <ChangelogSections sections={v.sections} knownIssuesLink={$about?.links.known_issues ?? null} />
      {:else}
        <p class="none">The notes for this version are in the
          <a class="link ext" href={$whatsNew.link} target="_blank" rel="noopener noreferrer">changelog</a>.</p>
      {/each}
    </div>
    <div class="panel-foot">
      {#if $about}
        <a class="link ext" href={$about.links.changelog} target="_blank" rel="noopener noreferrer">Full changelog</a>
      {:else}<span></span>{/if}
      <span class="small">Reopen from the version menu</span>
    </div>
  </div>
{/if}

<style>
  .scrim { position: fixed; left: 0; right: 0; bottom: 0; background: rgba(0, 0, 0, 0.45); z-index: 1200; }
  .panel {
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: 8px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5); font-size: 13px; color: var(--text-primary);
    text-align: left;
  }
  .dialog {
    position: fixed; top: 110px; left: 50%; transform: translateX(-50%); width: 560px;
    max-width: calc(100vw - 32px); z-index: 1201; display: flex; flex-direction: column;
  }
  .panel-head {
    display: flex; justify-content: space-between; align-items: center; padding: 9px 13px;
    border-bottom: 1px solid var(--border);
  }
  .panel-head .t { color: #e8e8e8; font-weight: 700; }
  .x {
    color: var(--text-secondary); font-size: 15px; background: none; border: none; cursor: pointer;
    padding: 0 2px; line-height: 1;
  }
  .x:hover { color: var(--text-primary); }
  .panel-body { padding: 8px 13px; overflow-y: auto; max-height: min(440px, calc(100vh - 220px)); position: relative; }
  .panel-foot {
    display: flex; justify-content: space-between; padding: 7px 13px; border-top: 1px solid var(--border);
    font-size: 12px;
  }
  .vhead {
    color: var(--accent); font-weight: 700; margin: 10px 0 2px; border-bottom: 1px solid var(--border);
    padding-bottom: 2px;
  }
  .vhead.first { margin-top: 0; }
  .vhead .date { color: var(--text-secondary); font-weight: 400; font-size: 11px; margin-left: 6px; }
  .small { font-size: 12px; color: #999; }
  .none { font-size: 12px; color: #bbb; }
  .link { color: var(--accent); text-decoration: none; }
  .link:hover { text-decoration: underline; }
  .ext::after { content: " \2197"; color: var(--text-secondary); font-size: 10px; }
</style>
