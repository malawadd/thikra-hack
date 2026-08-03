<script lang="ts">
  import '../studio.css';
  import '@fontsource/noto-sans/400.css';
  import '@fontsource/noto-sans/700.css';
  import '@fontsource/noto-sans-arabic/400.css';
  import '@fontsource/noto-sans-arabic/700.css';
  import '@fontsource/noto-serif/600.css';
  import '@xyflow/svelte/dist/style.css';
  let { children } = $props();
</script>

{@render children()}
