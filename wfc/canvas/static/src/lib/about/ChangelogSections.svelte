<script lang="ts">
  /**
   * A version's changelog sections, as the server ordered them (Upgrading
   * first, Known issues last): each heading, its intro paragraph and its
   * items. A Known issues section ends with "Ways around each one", the
   * guide's Known Issues page, when `knownIssuesLink` is given.
   */
  import InlineText from './InlineText.svelte';
  import { KNOWN_ISSUES_HEADING } from './versionMenu.js';
  import type { ChangelogSection } from './api.js';

  let { sections, knownIssuesLink = null }: {
    sections: ChangelogSection[];
    knownIssuesLink?: string | null;
  } = $props();
</script>

{#each sections as sec, i (sec.heading + i)}
  <h4 class:first={i === 0} data-testid="changelog-heading"
      data-known-issues={sec.heading === KNOWN_ISSUES_HEADING ? '' : undefined}>{sec.heading}</h4>
  {#if sec.intro}<p class="intro"><InlineText text={sec.intro} /></p>{/if}
  {#if sec.items.length}
    <ul class="cl">
      {#each sec.items as item, j (j)}<li><InlineText text={item} /></li>{/each}
    </ul>
  {/if}
  {#if sec.heading === KNOWN_ISSUES_HEADING && knownIssuesLink}
    <div class="small"><a class="link ext" href={knownIssuesLink} target="_blank" rel="noopener noreferrer"
      data-testid="ways-around">Ways around each one</a></div>
  {/if}
{/each}

<style>
  h4 { font-size: 12px; color: #e8e8e8; margin: 7px 0 2px; }
  h4.first { margin-top: 0; }
  .intro { font-size: 12px; color: #bbb; line-height: 1.4; margin: 0 0 2px; }
  ul.cl { padding-left: 15px; margin: 0; line-height: 1.4; color: #bbb; font-size: 12px; }
  .small { margin-top: 4px; font-size: 12px; }
  .link { color: var(--accent); text-decoration: none; }
  .link:hover { text-decoration: underline; }
  .ext::after { content: " \2197"; color: var(--text-secondary); font-size: 10px; }
</style>
