<script lang="ts">
  import '../studio.css';
  import '@xyflow/svelte/dist/style.css';
  let { children } = $props();
</script>

{@render children()}
