<script lang="ts">import { page } from '$app/state';</script>
<svelte:head><title>Thikra · Error</title></svelte:head>
<div class="error-box"><h1>We couldn’t open this view.</h1><p>{page.error?.message ?? 'An unexpected application error occurred.'}</p><a class="btn btn-secondary" href="/">Return to overview</a></div>
