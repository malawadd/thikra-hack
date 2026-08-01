<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, money, shortDate, titleCase } from '$lib/api/client';
  let payment = $state<any>(null); let loading = $state(true); let error = $state('');
  onMount(async () => { try { payment = await api(`/thikra/payments/${page.params.id}`); } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); } finally { loading = false; } });
</script>

<AsyncState {loading} {error}>
  {#if payment}
    <PageHeader eyebrow="Payment detail" title={payment.merchant} description="A sanitized, evidence-linked view of the scoped authorization and its current commercial state.">
      {#snippet actions()}<StatusBadge status={payment.payment_state} />{/snippet}
    </PageHeader>
    <div class="grid grid-3">
      <div class="card stat-card"><span class="stat-label">Maximum amount</span><strong class="stat-value">{money(payment.maximum_amount_minor,payment.currency)}</strong></div>
      <div class="card stat-card"><span class="stat-label">Invoked amount</span><strong class="stat-value">{money(payment.invoked_amount_minor,payment.currency)}</strong></div>
      <div class="card stat-card"><span class="stat-label">Redress state</span><strong class="stat-value" style="font-size:1.2rem">{titleCase(payment.redress_state)}</strong></div>
    </div>
    <section class="card section">
      <div class="card-head"><h2>Session record</h2><span class="badge" data-tone="warning">Secrets redacted</span></div>
      <div class="card-pad grid grid-2"><div><span class="stat-label">Gateway / environment</span><p>{payment.gateway} · {payment.environment}</p></div><div><span class="stat-label">Session / order</span><p class="mono">{payment.external_session_id}<br />{payment.external_order_id}</p></div><div><span class="stat-label">Authorization state</span><p><StatusBadge status={payment.authorization_state} /></p></div><div><span class="stat-label">Expiration</span><p>{shortDate(payment.expires_at)}</p></div></div>
    </section>
    <section class="card section"><div class="card-head"><h2>Sanitized event history</h2></div><div class="card-pad timeline">{#each payment.events as event}<div class="timeline-item"><div class="timeline-dot"></div><div><h3>{titleCase(event.type)}</h3><p>{shortDate(event.timestamp)} · {event.payload.simulated ? 'Simulated demo event' : 'Authenticated Prava reconciliation'}</p></div></div>{/each}</div></section>
  {/if}
</AsyncState>
