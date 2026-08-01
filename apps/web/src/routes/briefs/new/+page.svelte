<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import Check from 'lucide-svelte/icons/check'; import ChevronLeft from 'lucide-svelte/icons/chevron-left'; import ChevronRight from 'lucide-svelte/icons/chevron-right'; import Code2 from 'lucide-svelte/icons/code-2'; import CreditCard from 'lucide-svelte/icons/credit-card'; import Play from 'lucide-svelte/icons/play'; import ShieldCheck from 'lucide-svelte/icons/shield-check'; import Sparkles from 'lucide-svelte/icons/sparkles';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import PravaCardForm from '$lib/components/PravaCardForm.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, money, titleCase } from '$lib/api/client';

  const labels = ['Creative objective', 'Deliverables', 'Constraints', 'Compile mandate', 'Provider strategy', 'Authorization', 'Launch'];
  let step = $state(1);
  let busy = $state(false);
  let error = $state('');
  let advancedJson = $state(false);
  let brief = $state({
    campaign_name: 'Noura Glow — Saudi launch', product: 'Noura Glow skincare',
    objective: 'Create three warm, modern vertical advertisements for young adults in Saudi Arabia.',
    target_audience: 'Young adults in Saudi Arabia', language: 'Arabic', tone: 'Warm and modern',
    creative_brief: 'Create three 15-second vertical advertisements for Noura Glow. Use Arabic narration. Show the product clearly. Do not use real human or celebrity likenesses. Do not invent medical claims. Use commercially permitted providers. Require my approval before final delivery.',
    deliverable: 'combined_advertisement', variants: 3, duration_sec: 15, aspect_ratio: '9:16', resolution: '1080x1920', file_format: 'mp4',
    maximum_budget: 20, deadline: new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 16), maximum_retries: 2,
    permitted_providers: 'openai, replicate, google, runway', forbidden_providers: '', human_approval_threshold: 10,
    commercial_use_required: true, likeness_restrictions: 'No real human or celebrity likenesses',
    forbidden_elements: 'medical before-and-after imagery, celebrity likenesses',
    required_elements: 'Noura Glow product visible in every variant',
    claim_constraints: 'Do not invent medical or therapeutic claims', required_language: 'Arabic',
    attribution_requirements: 'Record provider and model provenance'
  });
  let compiled = $state<any>(null);
  let strategy = $state<any>(null);
  let selection = $state<Record<string, { vendor: string; model: string }>>({});
  let payment = $state<any>(null);
  let mode = $state('DEMO');
  const merchantUrls: Record<string, string> = {
    gmicloud: 'https://console.gmicloud.ai', openai: 'https://openai.com',
    replicate: 'https://replicate.com'
  };
  onMount(async () => { try { mode = (await api<any>('/health/ready')).mode; } catch { mode = 'UNKNOWN'; } });

  const csv = (value: string) => value.split(',').map((item) => item.trim()).filter(Boolean);
  const estimatedAmount = () => Math.min(compiled?.mandate?.budget_cap_minor ?? 0, 99);
  function errorMessage(cause: unknown): string {
    if (cause instanceof Error) return cause.message;
    if (cause && typeof cause === 'object' && 'message' in cause) {
      const message = (cause as { message?: unknown }).message;
      if (typeof message === 'string') return message;
    }
    try { return JSON.stringify(cause); } catch { return 'Authorization failed.'; }
  }
  async function compileMandate() {
    busy = true; error = '';
    try {
      compiled = await api('/thikra/briefs/compile', { method: 'POST', body: JSON.stringify({
        campaign_name: brief.campaign_name, product: brief.product, objective: brief.objective,
        target_audience: brief.target_audience, language: brief.language, tone: brief.tone,
        creative_brief: brief.creative_brief,
        deliverables: [{ modality: brief.deliverable, variants: brief.variants, duration_sec: brief.duration_sec, aspect_ratio: brief.aspect_ratio, resolution: brief.resolution, file_format: brief.file_format }],
        maximum_budget_minor: Math.round(brief.maximum_budget * 100), currency: 'USD', deadline: new Date(brief.deadline).toISOString(),
        maximum_retries: brief.maximum_retries, permitted_providers: csv(brief.permitted_providers), forbidden_providers: csv(brief.forbidden_providers),
        human_approval_threshold_minor: Math.round(brief.human_approval_threshold * 100), commercial_use_required: brief.commercial_use_required,
        likeness_restrictions: brief.likeness_restrictions, forbidden_elements: csv(brief.forbidden_elements), required_elements: csv(brief.required_elements),
        claim_constraints: csv(brief.claim_constraints), required_language: brief.required_language, attribution_requirements: csv(brief.attribution_requirements)
      }) });
      step = 4;
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { busy = false; }
  }
  async function saveAndConfirm() {
    busy = true; error = '';
    try {
      await api(`/thikra/mandates/${compiled.mandate_id}`, { method: 'PUT', body: JSON.stringify({ mandate: compiled.mandate, edit_summary: 'Principal reviewed all material constraints' }) });
      const fresh = await api<any>(`/thikra/mandates/${compiled.mandate_id}`);
      compiled.mandate = fresh.mandate;
      await api(`/thikra/mandates/${compiled.mandate_id}/confirm`, { method: 'POST', body: '{}' });
      strategy = await api('/thikra/providers/strategy', { method: 'POST', body: JSON.stringify({ mandate_id: compiled.mandate_id, mode: 'automatic', selections: {} }) });
      selection = strategy.selection;
      step = 5;
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { busy = false; }
  }
  async function authorize(verificationOnly = false) {
    busy = true; error = '';
    try {
      payment = await api('/thikra/payments/authorizations', { method: 'POST', body: JSON.stringify({
        mandate_id: compiled.mandate_id, merchant: `${selection.video?.vendor ?? 'Selected provider'} creative services`,
        merchant_url: merchantUrls[selection.video?.vendor] ?? 'https://openai.com',
        maximum_amount_minor: verificationOnly ? 0 : compiled.mandate.budget_cap_minor,
        estimated_amount_minor: verificationOnly ? 0 : estimatedAmount(),
        retry_reserve_minor: verificationOnly ? 0 : compiled.mandate.retry_budget_minor,
        verification_only: verificationOnly,
        currency: compiled.mandate.currency, idempotency_key: `authorization-${compiled.mandate_id}-${crypto.randomUUID()}`
      }) });
    } catch (cause) { error = errorMessage(cause); }
    finally { busy = false; }
  }
  async function pollPayment() {
    busy = true;
    try {
      const current = payment;
      const result = await api<any>(`/thikra/payments/${payment.id}/poll`, { method: 'POST', body: '{}' });
      payment = { ...current, ...result.payment };
      if (payment.authorization_state === 'FAILED') {
        error = errorMessage(result.result?.transactions?.[0]?.error ?? 'Prava authorization failed.');
      }
    }
    catch (cause) { error = errorMessage(cause); }
    finally { busy = false; }
  }
  async function retryAuthorization(verificationOnly = false) {
    const previous = payment;
    payment = null;
    if (previous?.id) {
      try { await api(`/thikra/payments/${previous.id}/revoke`, { method: 'POST', body: '{}' }); }
      catch { /* Consumed and expired sessions may no longer be revocable. */ }
    }
    await authorize(verificationOnly);
  }
  async function launch() {
    busy = true; error = '';
    try {
      const run = await api<any>('/thikra/runs', { method: 'POST', body: JSON.stringify({
        mandate_id: compiled.mandate_id, payment_id: payment.id, provider_selection: selection,
        idempotency_key: `run-${compiled.mandate_id}`
      }) });
      await goto(`/runs/${run.id}`);
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); busy = false; }
  }
  function choose(slot: string, quote: any) { selection = { ...selection, [slot]: { vendor: quote.vendor, model: quote.default_model } }; }
</script>

<PageHeader eyebrow="New procurement" title="Turn intent into an enforceable mandate." description="Define the creative outcome and commercial boundaries first. Thikra will not select a provider or request authorization until you confirm them." />

<div class="wizard-shell appear">
  <aside class="card steps" aria-label="Brief progress">
    {#each labels as label, index}
      <div class="step" class:active={step === index + 1} class:done={step > index + 1}><b>{step > index + 1 ? '✓' : index + 1}</b><span>{label}</span></div>
    {/each}
  </aside>
  <section class="card wizard-body">
    {#if error}<div class="error-box" style="margin-bottom:18px" role="alert">{error}</div>{/if}
    {#if step === 1}
      <h2>Creative objective</h2><p class="lede">Tell Thikra what the campaign must accomplish.</p>
      <div class="form-grid section">
        <div class="field"><label for="campaign">Campaign name</label><input id="campaign" bind:value={brief.campaign_name} /></div>
        <div class="field"><label for="product">Product or subject</label><input id="product" bind:value={brief.product} /></div>
        <div class="field full"><label for="objective">Objective</label><input id="objective" bind:value={brief.objective} /></div>
        <div class="field"><label for="audience">Target audience</label><input id="audience" bind:value={brief.target_audience} /></div>
        <div class="field"><label for="language">Language</label><input id="language" bind:value={brief.language} /></div>
        <div class="field"><label for="tone">Desired tone</label><input id="tone" bind:value={brief.tone} /></div>
        <div class="field full"><label for="creative">Freeform creative brief</label><textarea id="creative" bind:value={brief.creative_brief}></textarea></div>
      </div>
    {:else if step === 2}
      <h2>Deliverables</h2><p class="lede">Describe exactly what the provider must return.</p>
      <div class="form-grid section">
        <div class="field"><label for="deliverable">Deliverable</label><select id="deliverable" bind:value={brief.deliverable}><option value="image">Image</option><option value="video">Video</option><option value="voice">Voice / narration</option><option value="music">Music</option><option value="combined_advertisement">Combined advertisement</option></select></div>
        <div class="field"><label for="variants">Number of variants</label><input id="variants" type="number" min="1" max="12" bind:value={brief.variants} /></div>
        <div class="field"><label for="duration">Duration per variant (seconds)</label><input id="duration" type="number" bind:value={brief.duration_sec} /></div>
        <div class="field"><label for="ratio">Aspect ratio</label><select id="ratio" bind:value={brief.aspect_ratio}><option>9:16</option><option>16:9</option><option>1:1</option><option>4:5</option></select></div>
        <div class="field"><label for="resolution">Resolution</label><select id="resolution" bind:value={brief.resolution}><option>1080x1920</option><option>1920x1080</option><option>1080x1080</option></select></div>
        <div class="field"><label for="format">File format</label><select id="format" bind:value={brief.file_format}><option>mp4</option><option>png</option><option>wav</option><option>webm</option></select></div>
      </div>
    {:else if step === 3}
      <h2>Commercial and creative constraints</h2><p class="lede">These limits become backend-enforced policy, not suggestions in a prompt.</p>
      <div class="form-grid section">
        <div class="field"><label for="budget">Maximum budget (USD)</label><input id="budget" type="number" min="1" step="0.01" bind:value={brief.maximum_budget} /></div>
        <div class="field"><label for="deadline">Deadline</label><input id="deadline" type="datetime-local" bind:value={brief.deadline} /></div>
        <div class="field"><label for="retries">Maximum retries</label><input id="retries" type="number" min="0" max="5" bind:value={brief.maximum_retries} /></div>
        <div class="field"><label for="threshold">Human approval threshold (USD)</label><input id="threshold" type="number" min="0" bind:value={brief.human_approval_threshold} /></div>
        <div class="field"><label for="permitted">Permitted providers</label><input id="permitted" bind:value={brief.permitted_providers} /></div>
        <div class="field"><label for="forbiddenp">Forbidden providers</label><input id="forbiddenp" bind:value={brief.forbidden_providers} placeholder="Comma separated" /></div>
        <div class="field full"><label for="likeness">Likeness restrictions</label><input id="likeness" bind:value={brief.likeness_restrictions} /></div>
        <div class="field full"><label for="forbidden">Forbidden visual or spoken elements</label><input id="forbidden" bind:value={brief.forbidden_elements} /></div>
        <div class="field full"><label for="required">Required product visibility / elements</label><input id="required" bind:value={brief.required_elements} /></div>
        <div class="field full"><label for="claims">Claims that must not be invented</label><input id="claims" bind:value={brief.claim_constraints} /></div>
        <div class="field"><label for="requiredlang">Required language</label><input id="requiredlang" bind:value={brief.required_language} /></div>
        <div class="field"><label for="attr">Attribution requirements</label><input id="attr" bind:value={brief.attribution_requirements} /></div>
        <label class="check-row full"><input type="checkbox" bind:checked={brief.commercial_use_required} /><span>Require commercially permitted generation</span></label>
      </div>
    {:else if step === 4 && compiled}
      <h2>Review the compiled mandate</h2><p class="lede">Every material field remains editable. Saving creates a new mandate version before confirmation.</p>
      <div class="form-grid section">
        <div class="field full"><label for="mobjective">Objective</label><textarea id="mobjective" bind:value={compiled.mandate.objective}></textarea></div>
        <div class="field"><label for="mlang">Required language</label><input id="mlang" bind:value={compiled.mandate.language} /></div>
        <div class="field"><label for="mtone">Tone</label><input id="mtone" bind:value={compiled.mandate.tone} /></div>
        <div class="field"><label for="mbudget">Budget cap (minor units)</label><input id="mbudget" type="number" bind:value={compiled.mandate.budget_cap_minor} /></div>
        <div class="field"><label for="mretry">Maximum retries</label><input id="mretry" type="number" bind:value={compiled.mandate.maximum_retries} /></div>
        <div class="field full"><label for="mlikeness">Likeness policy</label><input id="mlikeness" bind:value={compiled.mandate.likeness_policy} /></div>
        <div class="field full"><label for="mforbidden">Forbidden elements</label><input id="mforbidden" value={compiled.mandate.forbidden_elements.join(', ')} onchange={(event) => compiled.mandate.forbidden_elements = csv(event.currentTarget.value)} /></div>
        <div class="field full"><label for="mrequired">Required elements</label><input id="mrequired" value={compiled.mandate.required_elements.join(', ')} onchange={(event) => compiled.mandate.required_elements = csv(event.currentTarget.value)} /></div>
        <label class="check-row full"><input type="checkbox" bind:checked={compiled.mandate.commercial_use_required} /><span>Commercial-use status must be known</span></label>
      </div>
      <button class="btn btn-secondary" onclick={() => advancedJson = !advancedJson}><Code2 size={15} /> {advancedJson ? 'Hide' : 'Inspect'} JSON</button>
      {#if advancedJson}<pre class="card card-pad mono" style="white-space:pre-wrap;max-height:380px;overflow:auto">{JSON.stringify(compiled.mandate, null, 2)}</pre>{/if}
    {:else if step === 5 && strategy}
      <h2>Provider strategy</h2><p class="lede">Cost and latency are estimates. Select any configured, mandate-compliant provider per modality.</p>
      {#each Object.keys(strategy.selection) as slot}
        <div class="section"><div class="section-title"><h2>{titleCase(slot)}</h2><span class="badge" data-tone="info">Estimated</span></div>
          <div class="grid grid-3">
            {#each strategy.quotes.filter((quote: any) => quote.modality === slot).slice(0, 3) as quote}
              <button class="card card-pad" style={`text-align:left;cursor:pointer;border-color:${selection[slot]?.vendor === quote.vendor ? 'var(--mint-deep)' : 'var(--line)'}`} onclick={() => choose(slot, quote)} disabled={!quote.configured || !quote.compliant}>
                <div style="display:flex;justify-content:space-between;gap:8px"><strong>{titleCase(quote.vendor)}</strong><StatusBadge status={quote.configured ? 'configured' : 'missing'} tone={quote.configured ? 'success' : 'danger'} /></div>
                <p class="small muted">{quote.default_model}</p><div class="small">~{money(quote.estimated_cost_minor)} · ~{quote.estimated_latency_sec}s · quality {quote.quality_prior}</div>
              </button>
            {/each}
          </div>
        </div>
      {/each}
    {:else if step === 6}
      <h2>Scoped authorization</h2><p class="lede">Authorization is not settlement. This permits one bounded provider purchase within the confirmed mandate.</p>
      <div class="grid grid-3 section">
        <div class="card card-pad"><span class="stat-label">Maximum authorized</span><strong class="stat-value">{money(compiled.mandate.budget_cap_minor)}</strong></div>
        <div class="card card-pad"><span class="stat-label">Estimated purchase</span><strong class="stat-value">{money(estimatedAmount())}</strong><span class="stat-note">Audited call ceiling</span></div>
        <div class="card card-pad"><span class="stat-label">Retry reserve</span><strong class="stat-value">{money(compiled.mandate.retry_budget_minor)}</strong></div>
      </div>
      <div class="card card-pad section"><strong>Exact action</strong><p class="muted">Authorize Thikra to obtain scoped payment credentials for the selected generative-media merchant, up to {money(compiled.mandate.budget_cap_minor)}, expiring in 15 minutes. Provider delivery and final acceptance remain separate states.</p><span class="badge" data-tone={mode === 'DEMO' ? 'warning' : 'info'}>{mode === 'DEMO' ? 'Demo payment mode' : `${mode} Prava mode`}</span></div>
      {#if !payment}
        <div class="actions section">
          <button class="btn btn-primary" onclick={() => authorize(false)} disabled={busy}><CreditCard size={16} /> {busy ? 'Creating session…' : 'Authorize bounded amount'}</button>
          {#if mode === 'SANDBOX'}<button class="btn btn-secondary" onclick={() => authorize(true)} disabled={busy}>Test card with $0</button>{/if}
        </div>
        {#if mode === 'SANDBOX'}<p class="small muted">The $0 option verifies the Prava iframe and card enrollment only. It never authorizes provider spend or unlocks generation.</p>{/if}
      {:else if payment.authorization_state === 'FAILED' || payment.authorization_state === 'REVOKED'}
        <div class="card card-pad section"><StatusBadge status={payment.authorization_state} /><p class="muted">The Prava session failed, expired, or was consumed. Requesting again creates a fresh single-use session on this page.</p><div class="actions"><button class="btn btn-primary" onclick={() => retryAuthorization(false)} disabled={busy}>{busy ? 'Creating session…' : 'Request new $1 session'}</button>{#if mode === 'SANDBOX'}<button class="btn btn-secondary" onclick={() => retryAuthorization(true)} disabled={busy}>Request new $0 test session</button>{/if}</div></div>
      {:else if payment.authorization_state === 'VERIFIED'}
        <div class="card card-pad section"><StatusBadge status={payment.authorization_state} tone="success" /><p class="muted">The $0 sandbox iframe completed successfully. This verifies card enrollment only and does not authorize paid generation.</p><button class="btn btn-primary" onclick={() => retryAuthorization(false)} disabled={busy}>Request $1 authorization session</button></div>
      {:else if payment.checkout && !payment.checkout.simulated}
        {#key payment.id}
          <PravaCardForm session={payment.checkout} publishableKey={payment.checkout.publishable_key} onerror={(message) => error = errorMessage(message)} onnewsession={() => retryAuthorization(payment.maximum_amount_minor === 0)} />
        {/key}
        <div class="actions"><button class="btn btn-secondary" onclick={pollPayment}>Check authorization</button><button class="btn btn-secondary" onclick={() => retryAuthorization(payment.maximum_amount_minor === 0)} disabled={busy}>{busy ? 'Creating session…' : 'Request fresh session'}</button></div>
        {#if payment.maximum_amount_minor === 0}<p class="small muted">Verification-only sandbox session · $0.00 · cannot unlock generation</p>{/if}
      {:else}<div class="card card-pad section"><StatusBadge status={payment.authorization_state} /><p class="muted">{payment.checkout?.simulated ? 'Simulated authorization recorded as demo data. No real card or payment was used.' : 'Authorization recorded. Continue when it is approved.'}</p></div>{/if}
    {:else if step === 7}
      <h2>Ready to launch</h2><p class="lede">The confirmed mandate, selected providers, bounded authorization, retry reserve, and human review trigger will travel with this run.</p>
      <div class="card card-pad section"><div class="check-list"><div class="check" data-status="PASS"><div class="icon"><Check size={16} /></div><div><h3>Mandate confirmed</h3><p>Versioned policy is immutable for this launch.</p></div></div><div class="check" data-status="PASS"><div class="icon"><Check size={16} /></div><div><h3>Provider strategy selected</h3><p>Per-modality models and estimates are recorded.</p></div></div><div class="check" data-status="PASS"><div class="icon"><ShieldCheck size={16} /></div><div><h3>Budget authorization approved</h3><p>Demo authorization only; verification and acceptance remain pending.</p></div></div></div></div>
      <button class="btn btn-primary section" onclick={launch} disabled={busy}><Play size={16} /> {busy ? 'Creating run…' : 'Launch accountable generation'}</button>
    {/if}

    {#if step <= 6}
      <div class="wizard-actions">
        <button class="btn btn-secondary" disabled={step === 1 || busy} onclick={() => step--}><ChevronLeft size={16} /> Back</button>
        {#if step < 3}<button class="btn btn-primary" onclick={() => step++}>Continue <ChevronRight size={16} /></button>
        {:else if step === 3}<button class="btn btn-primary" onclick={compileMandate} disabled={busy}><Sparkles size={16} /> {busy ? 'Compiling…' : 'Compile mandate'}</button>
        {:else if step === 4}<button class="btn btn-primary" onclick={saveAndConfirm} disabled={busy}><ShieldCheck size={16} /> Save version & confirm</button>
        {:else if step === 5}<button class="btn btn-primary" onclick={() => step = 6}>Review authorization <ChevronRight size={16} /></button>
        {:else if step === 6}<button class="btn btn-primary" onclick={() => step = 7} disabled={!payment || payment.authorization_state !== 'AUTHORIZED'}>Continue to launch <ChevronRight size={16} /></button>{/if}
      </div>
    {/if}
  </section>
</div>
