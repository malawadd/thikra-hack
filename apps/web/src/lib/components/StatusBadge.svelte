<script lang="ts">
  import { titleCase } from '$lib/api/client';
  let { status, tone }: { status: string; tone?: string } = $props();
</script>

<span class="badge" data-status={status} data-tone={tone}><span class="dot"></span>{titleCase(status)}</span>
