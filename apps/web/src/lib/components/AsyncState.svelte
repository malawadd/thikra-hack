<script lang="ts">
  import type { Snippet } from 'svelte';
  let { loading, error, empty = false, children }: { loading: boolean; error?: string; empty?: boolean; children: Snippet } = $props();
</script>

{#if loading}
  <div class="card loading" role="status"><div class="spinner"></div>Loading live Thikra data…</div>
{:else if error}
  <div class="error-box" role="alert"><strong>Unable to load this view</strong><br /><span class="small">{error}</span></div>
{:else if empty}
  <div class="card empty"><strong>Nothing here yet</strong><p>Create a brief to begin a verify-then-pay run.</p></div>
{:else}
  {@render children()}
{/if}
