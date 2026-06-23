<script lang="ts">
  import DescendantNodeCard from './DescendantNodeCard.svelte';
  import { runs, hideDescendants, selectRun, loadRuns } from './historyStore.js';
  import type { WfcRun } from './historyApi.js';

  interface Props {
    runId: string;
  }

  let { runId }: Props = $props();

  interface TreeNode {
    run: WfcRun;
    children: TreeNode[];
  }

  /**
   * Build tree from allRuns (not filteredRuns). The user committed to this
   * root, so we show the full descendant structure.
   */
  let tree = $derived.by(() => {
    const allRuns = $runs;
    return buildTree(allRuns, runId);
  });

  let rootRun = $derived(tree?.run ?? null);

  function buildTree(allRuns: WfcRun[], rootId: string): TreeNode | null {
    const runMap = new Map<string, WfcRun>();
    for (const r of allRuns) runMap.set(r.id, r);

    const root = runMap.get(rootId);
    if (!root) return null;

    // Track which descendants we've already placed. Now that parents form
    // a DAG (a single run can have multiple upstreams), a diamond child
    // — e.g. spatialdata_export fed by both cellpose and regionprops —
    // would otherwise render once per parent path. Visit-once yields a
    // clean tree projection of the DAG.
    const visited = new Set<string>([rootId]);

    function getChildren(parentId: string): TreeNode[] {
      return allRuns
        .filter(r => r.parentRunIds.includes(parentId) && !visited.has(r.id))
        .map(r => {
          visited.add(r.id);
          return { run: r, children: getChildren(r.id) };
        });
    }

    return {
      run: root,
      children: getChildren(rootId),
    };
  }

  let collapsed = $state<Record<string, boolean>>({});

  function toggleCollapse(nodeId: string) {
    collapsed = { ...collapsed, [nodeId]: !collapsed[nodeId] };
  }

  function handleRefresh() {
    loadRuns();
  }
</script>

<div class="descendant-tree">
  <div class="desc-header">
    <button class="back-btn" onclick={() => hideDescendants()}>
      {'\u2190'} Back to Paths
    </button>
    <span class="title">
      Descendants of {rootRun?.runName || rootRun?.id || runId}
    </span>
    <button class="refresh-btn" onclick={handleRefresh}>
      {'\u21BB'} Refresh
    </button>
  </div>

  {#if !tree}
    <div class="tree-empty">No data found for run {runId}.</div>
  {:else if tree.children.length === 0}
    <!-- Root card with no children -->
    <DescendantNodeCard run={tree.run} isRoot={true} />
    <div class="tree-empty">No descendant runs yet.</div>
  {:else}
    <!-- Root card -->
    <DescendantNodeCard run={tree.run} isRoot={true} />

    <!-- Recursive tree -->
    <div class="desc-tree">
      {#snippet treeChildren(children: TreeNode[], depth: number)}
        {#each children as child, i (child.run.id)}
          {@const isLast = i === children.length - 1}
          {@const hasChildren = child.children.length > 0}
          <div class="desc-row" style="padding-left: {depth * 24}px;">
            {#if hasChildren}
              <button class="collapse-toggle" onclick={() => toggleCollapse(child.run.id)}>
                {collapsed[child.run.id] ? '\u25B6' : '\u25BC'}
              </button>
            {/if}
            <div class="desc-connector">
              {isLast ? '\u2514\u2500\u25B6' : '\u251C\u2500\u25B6'}
            </div>
            <div class="desc-card-wrap">
              <DescendantNodeCard run={child.run} />
            </div>
          </div>
          {#if hasChildren && !collapsed[child.run.id]}
            {@render treeChildren(child.children, depth + 1)}
          {/if}
        {/each}
      {/snippet}

      {@render treeChildren(tree.children, 0)}
    </div>
  {/if}
</div>

<style>
  .descendant-tree {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .desc-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border, #3e3e42);
    margin-bottom: 14px;
  }
  .back-btn {
    background: var(--bg-panel, #252526);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    padding: 4px 10px;
    color: var(--accent, #4A90D9);
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
  }
  .back-btn:hover {
    border-color: var(--accent, #4A90D9);
  }
  .title {
    color: #fff;
    font-weight: 600;
    font-size: 13px;
    flex: 1;
  }
  .refresh-btn {
    background: var(--bg-panel, #252526);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    padding: 4px 10px;
    color: var(--accent, #4A90D9);
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
    margin-left: auto;
  }
  .refresh-btn:hover {
    border-color: var(--accent, #4A90D9);
  }
  .tree-empty {
    padding: 20px;
    text-align: center;
    font-size: 12px;
    color: var(--text-muted, #666);
  }

  .desc-tree {
    margin-left: 20px;
    border-left: 2px solid #333;
    padding-left: 0;
  }
  .desc-row {
    display: flex;
    align-items: center;
    margin: 10px 0;
  }
  .collapse-toggle {
    background: none;
    border: none;
    color: var(--text-muted, #666);
    cursor: pointer;
    font-size: 9px;
    width: 16px;
    padding: 0;
    flex-shrink: 0;
  }
  .desc-connector {
    color: var(--text-muted, #666);
    font-family: inherit;
    min-width: 34px;
    margin-right: 6px;
    flex-shrink: 0;
  }
  .desc-card-wrap {
    flex: 1;
    min-width: 0;
  }
</style>
