<script lang="ts">
  import { onMount } from 'svelte';
  import ArrowRight from 'lucide-svelte/icons/arrow-right';
  import Bot from 'lucide-svelte/icons/bot';
  import ShieldCheck from 'lucide-svelte/icons/shield-check';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import { api, money } from '$lib/api/client';

  let services = $state<any[]>([]);
  let loading = $state(true);
  let error = $state('');
  onMount(async () => {
    try { services = (await api<{ items: any[] }>('/api/v1/services')).items; }
    catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { loading = false; }
  });
</script>

<PageHeader eyebrow="Creative-services marketplace" title="Outcomes an agent can actually buy." description="Published service versions carry deterministic pricing, input schemas, verification policy, bounded retries, and durable evidence.">
  {#snippet actions()}<a class="btn btn-secondary" href="/developers"><Bot size={16} /> Integrate an agent</a>{/snippet}
</PageHeader>

<AsyncState {loading} {error}>
  <section class="grid grid-3 appear">
    {#each services as service (service.id)}
      <article class="card service-card">
        <div class="service-top"><span class="badge">{service.category}</span><span class="mono muted">{service.slug} · v{service.version}</span></div>
        <h2>{service.name}</h2>
        <p>{service.short_description}</p>
        <div class="service-meta">
          <span><strong>{money(service.base_price_minor, service.currency)}</strong><small>starting price</small></span>
          <span><strong>{Math.ceil(service.estimated_delivery_seconds_max / 60)} min</strong><small>upper estimate</small></span>
        </div>
        <div class="service-tags">
          {#each service.supported_modalities as modality}<span>{modality}</span>{/each}
        </div>
        <div class="service-assurance"><ShieldCheck size={16} /> Verification included · {service.human_review_available ? 'human review available' : 'automated review'}</div>
        <a class="btn btn-primary" href={`/services/${service.slug}`}>Inspect & request quote <ArrowRight size={15} /></a>
      </article>
    {/each}
  </section>
</AsyncState>
