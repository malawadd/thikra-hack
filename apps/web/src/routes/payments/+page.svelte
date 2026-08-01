<script lang="ts">
  import { onMount } from 'svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, money, shortDate, titleCase } from '$lib/api/client';
  let items = $state<any[]>([]); let loading = $state(true); let error = $state('');
  onMount(async () => { try { items = (await api<any>('/thikra/payments')).items; } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); } finally { loading = false; } });
</script>
<PageHeader eyebrow="Commercial ledger" title="Payments" description="Authorization, credential readiness, provider purchase, delivery, acceptance, and redress are represented separately. Raw card data and one-time credentials never appear here." />
<div class="actions" style="margin-bottom:18px"><span class="badge" data-tone="warning">Demo payment mode</span><span class="help">No simulated record is presented as a real Prava transaction.</span></div>
<AsyncState {loading} {error} empty={!loading && items.length === 0}>
  <div class="card table-wrap"><table><thead><tr><th>Session</th><th>Merchant</th><th>Maximum / invoked</th><th>Authorization</th><th>Payment state</th><th>Related run</th><th>Environment</th></tr></thead><tbody>{#each items as item}<tr><td><a href={`/payments/${item.id}`}><strong>{item.external_session_id}</strong></a><div class="small muted">{shortDate(item.created_at)}</div></td><td>{item.merchant}</td><td>{money(item.maximum_amount_minor,item.currency)}<div class="small muted">{money(item.invoked_amount_minor,item.currency)} invoked</div></td><td><StatusBadge status={item.authorization_state} /></td><td><StatusBadge status={item.payment_state} /></td><td><a class="mono" href={`/runs/${item.run_id}`}>{item.run_id?.slice(0,8) ?? 'Not launched'}</a></td><td>{titleCase(item.environment)}</td></tr>{/each}</tbody></table></div>
</AsyncState>
