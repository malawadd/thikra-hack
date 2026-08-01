<script lang="ts">
  import { onMount } from 'svelte';
  import Search from 'lucide-svelte/icons/search';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, money, shortDate, titleCase } from '$lib/api/client';
  import type { RunSummary } from '$lib/types';

  let runs = $state<RunSummary[]>([]);
  let loading = $state(true);
  let error = $state('');
  let query = $state('');
  let status = $state('');
  let provider = $state('');
  let paymentState = $state('');
  const filtered = $derived(runs.filter((run) =>
    (!query || run.campaign_name.toLowerCase().includes(query.toLowerCase())) &&
    (!status || run.status === status) &&
    (!provider || JSON.stringify(run.provider_selection).includes(provider)) &&
    (!paymentState || run.payment_state === paymentState)
  ));
  onMount(async () => {
    try { runs = (await api<{ items: RunSummary[] }>('/thikra/runs')).items; }
    catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { loading = false; }
  });
</script>

<PageHeader eyebrow="Generation portfolio" title="Runs" description="Search and filter every procurement run by execution, provider, payment, budget utilization, escalation, and final decision.">
  {#snippet actions()}<a class="btn btn-primary" href="/briefs/new">New brief</a>{/snippet}
</PageHeader>

<div class="card card-pad form-grid" style="margin-bottom:18px">
  <div class="field"><label for="search">Search campaign</label><div style="position:relative"><Search size={15} style="position:absolute;left:12px;top:13px;color:var(--muted)" /><input id="search" style="padding-left:36px" bind:value={query} placeholder="Noura Glow" /></div></div>
  <div class="field"><label for="status">Status</label><select id="status" bind:value={status}><option value="">All statuses</option><option>GENERATING</option><option>HUMAN_REVIEW</option><option>COMPLETED</option><option>REJECTED</option><option>FAILED</option></select></div>
  <div class="field"><label for="provider">Provider</label><select id="provider" bind:value={provider}><option value="">All providers</option><option>openai</option><option>replicate</option><option>google</option><option>runway</option></select></div>
  <div class="field"><label for="payment">Payment state</label><select id="payment" bind:value={paymentState}><option value="">All payment states</option><option>SIMULATED_INVOKED</option><option>CREDENTIAL_READY</option><option>APPROVED</option><option>FAILED</option></select></div>
</div>

<AsyncState {loading} {error} empty={!loading && !error && filtered.length === 0}>
  <div class="card table-wrap">
    <table>
      <thead><tr><th>Campaign</th><th>Current stage</th><th>Budget</th><th>Providers</th><th>Created</th><th>Latest event</th><th>Final status</th></tr></thead>
      <tbody>
        {#each filtered as run (run.id)}
          <tr>
            <td><a href={`/runs/${run.id}`}><strong>{run.campaign_name}</strong></a><div class="small muted">{run.human_escalation ? 'Human escalation' : 'Policy automated'}</div></td>
            <td>{run.current_stage}<div class="small muted">Payment: {titleCase(run.payment_state)}</div></td>
            <td><strong>{money(run.spent_minor, run.currency)}</strong><div class="small muted">of {money(run.authorized_minor, run.currency)} authorized</div><div class="progress" style="margin-top:7px"><span style={`width:${Math.min(100, run.spent_minor / run.authorized_minor * 100)}%`}></span></div></td>
            <td><div class="actions">{#each Array.from(new Set(Object.values(run.provider_selection).map((item) => item.vendor))).slice(0, 3) as name}<span class="badge">{name}</span>{/each}</div></td>
            <td>{shortDate(run.created_at)}</td>
            <td>{titleCase(run.latest_event)}</td>
            <td><StatusBadge status={run.status} /></td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</AsyncState>
