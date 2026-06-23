<script lang="ts">
  import { BaseEdge, getBezierPath } from '@xyflow/svelte';
  import { edges } from './stores.js';
  import { pushState } from './history.js';

  let {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    markerStart,
    markerEnd,
    style,
    interactionWidth,
  }: {
    id: string;
    sourceX: number;
    sourceY: number;
    targetX: number;
    targetY: number;
    sourcePosition: any;
    targetPosition: any;
    markerStart?: string;
    markerEnd?: string;
    style?: string;
    interactionWidth?: number;
  } = $props();

  let [path, labelX, labelY] = $derived(
    getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  );

  let hovered = $state(false);

  function onEnter() { hovered = true; }
  function onLeave() { hovered = false; }

  function removeEdge(e: MouseEvent) {
    e.stopPropagation();
    pushState();
    edges.update(es => es.filter(edge => edge.id !== id));
  }
</script>

<!--
  Wrap BaseEdge in a group whose hover state controls the delete button
  visibility. The foreignObject hosts an HTML button so we get normal pointer
  events without fighting the SVG layer.
-->
<g onmouseenter={onEnter} onmouseleave={onLeave} role="presentation">
  <BaseEdge
    {id}
    {path}
    {markerStart}
    {markerEnd}
    {style}
    interactionWidth={interactionWidth ?? 20}
  />
  <foreignObject
    x={labelX - 10}
    y={labelY - 10}
    width={20}
    height={20}
    class="edge-delete-fo"
    style:opacity={hovered ? 1 : 0}
    style:pointer-events={hovered ? 'all' : 'none'}
  >
    <button
      type="button"
      class="edge-delete-btn"
      onclick={removeEdge}
      aria-label="Delete edge"
      title="Delete edge"
    >×</button>
  </foreignObject>
</g>

<style>
  .edge-delete-fo {
    overflow: visible;
    transition: opacity 0.12s ease;
  }
  .edge-delete-btn {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 1px solid #aaa;
    background: #222;
    color: #eee;
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
  }
  .edge-delete-btn:hover {
    background: #8c2b2b;
    border-color: #c94b4b;
    color: #fff;
  }
</style>
