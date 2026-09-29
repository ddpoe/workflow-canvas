<!--
  Stand-in for `@xyflow/svelte`'s `Handle`, for tests that mount a node
  component on its own.

  The real `Handle` reads three things out of Svelte context that only a
  mounted `SvelteFlow` provides: the node id, the node's connectable flag,
  and the flow store (it derives `store.ariaLabelConfig`). Mounted outside
  a flow it throws "Handle must be used within a Custom Node component".
  A node component's own drawing — labels, icons, chips, slots — is what
  those tests are about; connecting edges is the library's business and
  the Playwright suite's.

  Renders a marker element so a test can still count a node's handles.
-->
<script lang="ts">
  let { id = null, type = 'source' }: { id?: string | null; type?: string } = $props();
</script>

<span data-testid="handle" data-handle-id={id} data-handle-type={type}></span>
