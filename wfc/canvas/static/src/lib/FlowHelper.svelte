<script lang="ts">
  import { useSvelteFlow } from '@xyflow/svelte';
  import { onMount } from 'svelte';

  const { screenToFlowPosition, fitView } = useSvelteFlow();

  let { onReady }: { onReady: (helpers: { screenToFlowPosition: typeof screenToFlowPosition; fitView: typeof fitView }) => void } = $props();

  onMount(() => {
    onReady({ screenToFlowPosition, fitView });
    // Initial fit after the flow is ready
    fitView();
  });
</script>
