<script lang="ts">
  import { onMount } from 'svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, money, shortDate } from '$lib/api/client';
  let orders = $state<any[]>([]); let loading = $state(true); let error = $state(''); let status = $state('');
  onMount(load);
  async function load() { try { orders = (await api<{items:any[]}>('/api/v1/orders')).items; } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); } finally { loading = false; } }
  const visible = () => status ? orders.filter((order) => order.status === status) : orders;
</script>
<PageHeader eyebrow="Commercial oversight" title="External orders, separated from generation runs." description="Revenue, customer payment, fulfillment, verification, delivery, acceptance, disputes, and redress remain explicit linked states." />
<div class="actions" style="margin-bottom:16px"><label for="status">Status</label><select id="status" bind:value={status} style="width:auto"><option value="">All states</option>{#each [...new Set(orders.map((order) => order.status))] as value}<option>{value}</option>{/each}</select></div>
<AsyncState {loading} {error}>
  <section class="card"><div class="table-wrap"><table><thead><tr><th>Order</th><th>Service</th><th>Commercial</th><th>Payment</th><th>Fulfillment</th><th>Revenue</th><th>Created</th></tr></thead><tbody>
    {#each visible() as order (order.id)}<tr><td><a href={`/orders/${order.public_order_number}`}><strong>{order.public_order_number}</strong></a><div class="small muted">{order.id.slice(0,8)}</div></td><td>{order.service}<div class="small muted">v{order.service_version}</div></td><td><StatusBadge status={order.status} /></td><td><StatusBadge status={order.payment_state} /></td><td><StatusBadge status={order.fulfillment_state} /></td><td>{money(order.quoted_total_minor, order.currency)}<div class="small muted">paid {money(order.paid_total_minor, order.currency)}</div></td><td>{shortDate(order.created_at)}</td></tr>{/each}
  </tbody></table></div>{#if !visible().length}<div class="empty">No commercial orders match this filter.</div>{/if}</section>
</AsyncState>
