<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import ArrowLeft from 'lucide-svelte/icons/arrow-left';
  import CheckCircle2 from 'lucide-svelte/icons/circle-check-big';
  import Code2 from 'lucide-svelte/icons/code-2';
  import FileText from 'lucide-svelte/icons/file-text';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import { api, money } from '$lib/api/client';

  let service = $state<any>(null);
  let loading = $state(true);
  let error = $state('');
  let schemaView = $state(false);
  let brief = $state('Create a verified Arabic campaign asset that shows Noura Glow clearly without people or medical claims.');
  let email = $state('buyer@nouraglow.sa');
  let submitting = $state(false);
  let quote = $state<any>(null);
  let order = $state<any>(null);

  onMount(async () => {
    try { service = await api(`/api/v1/services/${page.params.slug}`); }
    catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { loading = false; }
  });

  async function requestQuote() {
    submitting = true; error = '';
    try {
      const input: Record<string, unknown> = { brief, language: 'ar', maximumRetries: Math.min(2, service.maximum_retries_included), forbiddenElements: ['real people', 'medical claims'] };
      if (service.input_schema.properties.durationSeconds?.const) input.durationSeconds = service.input_schema.properties.durationSeconds.const;
      if (service.slug === 'verified-vertical-ad' || service.slug === 'product-video-15s') Object.assign(input, { aspectRatio: '9:16', resolution: '1080x1920' });
      quote = await api('/api/v1/quotes', {
        method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ service: service.slug, input, buyer_principal: { type: 'HUMAN', display_name: 'Thikra Studio buyer', email }, buyer_agent: { name: 'Thikra Studio same-origin buyer', framework: 'SvelteKit' }, maximum_budget: { amount_minor: service.maximum_price_minor, currency: service.currency } })
      });
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { submitting = false; }
  }

  async function createOrder() {
    submitting = true; error = '';
    try {
      await api(`/api/v1/quotes/${quote.id}/accept`, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() } });
      order = await api('/api/v1/orders', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ quote_id: quote.id, external_reference: 'thikra-studio' }) });
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { submitting = false; }
  }
</script>

<a class="btn btn-ghost" href="/services"><ArrowLeft size={15} /> Marketplace</a>
<AsyncState {loading} {error}>
  {#if service}
    <PageHeader eyebrow={`${service.category} · ${service.slug} · v${service.version}`} title={service.name} description={service.long_description} />
    <div class="split-layout">
      <div class="grid">
        <section class="card card-pad">
          <div class="card-head-inline"><h2>Service contract</h2><button class="btn btn-secondary" onclick={() => schemaView = !schemaView}>{#if schemaView}<FileText size={15} /> Human view{:else}<Code2 size={15} /> JSON Schema{/if}</button></div>
          {#if schemaView}
            <h3>Input schema</h3><pre class="code-block">{JSON.stringify(service.input_schema, null, 2)}</pre>
            <h3>Output schema</h3><pre class="code-block">{JSON.stringify(service.output_schema, null, 2)}</pre>
          {:else}
            <div class="grid grid-2 compact-specs">
              <div><small>Pricing model</small><strong>{service.pricing_model}</strong></div>
              <div><small>Price boundary</small><strong>{money(service.minimum_price_minor)}–{money(service.maximum_price_minor)}</strong></div>
              <div><small>Delivery estimate</small><strong>{Math.ceil(service.estimated_delivery_seconds_min / 60)}–{Math.ceil(service.estimated_delivery_seconds_max / 60)} minutes</strong></div>
              <div><small>Included retry ceiling</small><strong>{service.maximum_retries_included}</strong></div>
            </div>
            <h3>Verification contract</h3>
            <div class="service-tags">{#each service.verification_capabilities as item}<span><CheckCircle2 size={12} /> {item}</span>{/each}</div>
            <h3>Commercial-use policy</h3><p class="muted">{service.commercial_use_policy}</p>
          {/if}
        </section>
      </div>
      <aside class="card card-pad sticky-panel">
        <span class="eyebrow">Deterministic quote</span><h2>{money(service.base_price_minor)} starting</h2>
        {#if !quote}
          <div class="field"><label for="brief">Creative brief</label><textarea id="brief" bind:value={brief}></textarea></div>
          <div class="field"><label for="buyer-email">Principal email</label><input id="buyer-email" type="email" bind:value={email} /></div>
          <button class="btn btn-primary" disabled={submitting || brief.length < 10} onclick={requestQuote}>{submitting ? 'Calculating…' : 'Request quote'}</button>
        {:else if !order}
          <div class="quote-total"><small>Quoted total</small><strong>{money(quote.total_minor, quote.currency)}</strong><span>Expires {new Date(quote.expires_at).toLocaleTimeString()}</span></div>
          <details><summary>Price breakdown</summary><pre class="code-block">{JSON.stringify(quote.pricing_breakdown, null, 2)}</pre></details>
          <button class="btn btn-primary" disabled={submitting} onclick={createOrder}>Accept quote & create order</button>
          <p class="help">This does not charge. Payment authorization is a separate human-approved step.</p>
        {:else}
          <div class="notice-success"><CheckCircle2 size={20} /><div><strong>Commercial order created</strong><p>No payment has occurred yet.</p></div></div>
          <a class="btn btn-primary" href={`/orders/${order.public_order_number}`}>Continue to payment</a>
        {/if}
      </aside>
    </div>
  {/if}
</AsyncState>
