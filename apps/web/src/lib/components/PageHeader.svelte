<script lang="ts">
  import type { Snippet } from 'svelte';
  let { eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: Snippet } = $props();
</script>

<header class="page-head appear">
  <div>
    <div class="eyebrow">{eyebrow}</div>
    <h1>{title}</h1>
    <p class="lede">{description}</p>
  </div>
  <div>{#if actions}{@render actions()}{/if}</div>
</header>
