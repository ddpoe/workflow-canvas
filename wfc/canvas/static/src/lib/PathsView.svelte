<script lang="ts">
  import PathNodeCard from './PathNodeCard.svelte';
  import {
    runs,
    filteredRuns,
    visiblePathRows,
    hiddenCachedPathCount,
    showFullyCachedPaths,
    filters,
    effectiveSample,
  } from './historyStore.js';
</script>

<div class="paths-view">
  {#if $hiddenCachedPathCount > 0}
    <div class="cached-paths-toggle">
      {$hiddenCachedPathCount} fully-cached path{$hiddenCachedPathCount === 1 ? '' : 's'}
      {$showFullyCachedPaths ? '' : 'hidden '}·
      <button
        class="cached-paths-toggle-btn"
        on:click={() => showFullyCachedPaths.update(v => !v)}
      >{$showFullyCachedPaths ? 'Hide' : 'Show'}</button>
    </div>
  {/if}
  {#if $visiblePathRows.length === 0}
    <div class="empty-state">
      {#if $runs.length === 0}
        <p>No runs yet. Click Run on the canvas to start one.</p>
      {:else if $filters.sample.length > 0}
        <p>No runs found for sample(s): {$filters.sample.join(', ')}</p>
      {:else}
        <p>No runs match the current filters.</p>
      {/if}
    </div>
  {:else}
    {#each $visiblePathRows as row (row.pathId)}
      {@const rootRun = row.nodes[0]}
      {@const sampleName = rootRun ? effectiveSample(rootRun, $runs) : null}
      <div class="path-row">
        <div class="path-label">
          Path {row.pathId}{sampleName ? ` \u2014 ${sampleName}` : ''}
        </div>
        <div class="path-nodes">
          {#each row.nodes as run, i (run.id)}
            {#if i > 0}
              <span class="path-arrow">{'\u2192'}</span>
            {/if}
            <PathNodeCard {run} />
          {/each}
        </div>
      </div>
    {/each}
  {/if}
</div>

<style>
  .paths-view {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .cached-paths-toggle {
    font-size: 12px;
    color: var(--text-muted, #666);
  }

  .cached-paths-toggle-btn {
    background: none;
    border: none;
    padding: 0;
    font-size: 12px;
    color: var(--accent, #4A90D9);
    cursor: pointer;
    text-decoration: underline;
  }

  .empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 40px;
    color: var(--text-muted, #666);
    font-size: 13px;
  }

  .path-row {
    margin-bottom: 22px;
  }

  .path-label {
    font-size: 12px;
    color: var(--text-muted, #666);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
  }

  .path-nodes {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0;
  }

  .path-arrow {
    color: var(--text-muted, #666);
    margin: 0 10px;
    font-size: 18px;
  }
</style>
