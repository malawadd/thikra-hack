<script lang="ts">
  import { onMount } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import AlertTriangle from 'lucide-svelte/icons/triangle-alert'; import Check from 'lucide-svelte/icons/check'; import CircleX from 'lucide-svelte/icons/circle-x'; import Download from 'lucide-svelte/icons/download'; import FileJson from 'lucide-svelte/icons/file-json'; import Network from 'lucide-svelte/icons/network'; import RefreshCw from 'lucide-svelte/icons/refresh-cw'; import ShieldCheck from 'lucide-svelte/icons/shield-check'; import X from 'lucide-svelte/icons/x';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, money, shortDate, titleCase } from '$lib/api/client';
  import type { LiveEvent } from '$lib/types';

  const runId = page.params.id;
  let run = $state<any>(null);
  let events = $state<LiveEvent[]>([]);
  let seen = new SvelteSet<string>();
  let loading = $state(true);
  let actionBusy = $state(false);
  let error = $state('');
  let connection = $state<'connecting' | 'live' | 'polling' | 'closed'>('connecting');
  let note = $state('');
  let caseReason = $state('The delivery needs formal investigation and redress.');
  let showCase = $state(false);
  let pollTimer: number | undefined;
  let sceneSaving = $state('');
  let reconnectAttempts = 0;

  async function refresh() {
    run = await api(`/thikra/runs/${runId}`);
    if (['COMPLETED', 'REJECTED', 'CANCELLED', 'FAILED', 'HUMAN_REVIEW', 'REDRESS_OPEN'].includes(run.status) && pollTimer) {
      clearInterval(pollTimer); pollTimer = undefined; connection = 'closed';
    }
  }
  function connect() {
    connection = 'connecting';
    const lastId = events.at(-1)?.eventId;
    const source = new EventSource(`/api/thikra/runs/${runId}/events${lastId ? `?last_event_id=${encodeURIComponent(lastId)}` : ''}`);
    source.onopen = () => connection = 'live';
    source.onmessage = async (message) => {
      try {
        const event = JSON.parse(message.data) as LiveEvent;
        if (!seen.has(event.eventId)) { seen.add(event.eventId); events = [...events, event]; }
        if (event.progress >= 1) { source.close(); await refresh(); connection = 'closed'; }
      } catch { /* malformed event is ignored; authoritative snapshot remains */ }
    };
    source.onerror = () => {
      source.close();
      if (reconnectAttempts < 3) {
        connection = 'connecting';
        const delay = 500 * 2 ** reconnectAttempts++;
        window.setTimeout(() => connect(), delay);
      } else {
        connection = 'polling';
        if (!pollTimer) pollTimer = window.setInterval(() => void refresh().catch(() => {}), 2500);
      }
    };
    return () => source.close();
  }
  onMount(() => {
    let close = () => {};
    void refresh().then(() => { loading = false; if (run.status === 'GENERATING') close = connect(); else connection = 'closed'; }).catch((cause) => { error = cause instanceof Error ? cause.message : String(cause); loading = false; });
    return () => { close(); if (pollTimer) clearInterval(pollTimer); };
  });
  async function action(path: string, body: any = {}) {
    actionBusy = true; error = '';
    try {
      run = await api(`/thikra/runs/${runId}/${path}`, { method: 'POST', body: JSON.stringify(body) });
      if (path === 'start') connect();
    }
    catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { actionBusy = false; }
  }
  async function saveScene(scene: any) {
    sceneSaving = scene.id; error = '';
    try {
      run = await api(`/thikra/runs/${runId}/scenes/${scene.id}`, {
        method: 'PUT', body: JSON.stringify({ prompt: scene.prompt, narration: scene.narration })
      });
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { sceneSaving = ''; }
  }
  async function openCase() {
    actionBusy = true;
    try {
      const created = await api<any>('/thikra/cases', { method: 'POST', body: JSON.stringify({ run_id: runId, reason: caseReason, severity: 'MEDIUM', owner: 'Trust operations' }) });
      await goto(`/cases/${created.id}`);
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); actionBusy = false; }
  }
  const finalAsset = $derived(run?.assets?.find((asset: any) => asset.type === 'final') ?? null);
  const allAssets = $derived(run?.assets ?? []);
  const progress = $derived(events.length ? events.at(-1)?.progress ?? 0 : (run?.status === 'HUMAN_REVIEW' ? 1 : 0));
</script>

<AsyncState {loading} {error}>
  {#if run}
    <PageHeader eyebrow="Principal run" title={run.campaign_name} description="The live, backend-authoritative path from mandate to provider purchase, stored delivery, layered verification, and human decision.">
      {#snippet actions()}<div class="actions"><StatusBadge status={run.status} /><span class="badge" data-tone={connection === 'live' ? 'success' : 'info'}>{connection}</span></div>{/snippet}
    </PageHeader>

    {#if error}<div class="error-box" style="margin-bottom:18px">{error}</div>{/if}
    <div class="progress" style="margin-bottom:24px;height:9px"><span style={`width:${progress * 100}%`}></span></div>

    {#if run.status === 'PLANNING'}
      <div class="notice section"><div><strong>Storyboard review is open</strong><p>Edit every scene prompt or Arabic narration now. Once generation starts, the confirmed storyboard becomes immutable evidence.</p></div><button class="btn btn-primary" disabled={actionBusy} onclick={() => action('start')}><ShieldCheck size={16} /> Confirm storyboard & start</button></div>
    {/if}

    <div class="split-layout">
      <div>
        <section class="card">
          <div class="card-head"><h2>Mandate summary</h2><span class="badge">Version {run.mandate_version ?? 1}</span></div>
          <div class="card-pad grid grid-2">
            <div><span class="stat-label">Objective</span><p>{run.mandate.objective}</p></div>
            <div><span class="stat-label">Audience & language</span><p>{run.mandate.target_audience} · {run.mandate.language}</p></div>
            <div><span class="stat-label">Required delivery</span><p>{run.mandate.deliverables?.map((item: any) => `${item.variants}× ${titleCase(item.modality)} · ${item.aspect_ratio}`).join(', ')}</p></div>
            <div><span class="stat-label">Retry and review policy</span><p>{run.maximum_retries} retries · {run.mandate.human_review_triggers?.join(' · ')}</p></div>
            <div class="full"><span class="stat-label">Deterministic constraints</span><div class="actions" style="margin-top:8px">{#each [...(run.mandate.required_elements ?? []), ...(run.mandate.claim_constraints ?? [])] as item}<span class="badge">{item}</span>{/each}</div></div>
          </div>
        </section>

        <section class="section">
          <div class="section-title"><h2>Live execution timeline</h2><span class="small muted">SSE with polling fallback</span></div>
          <div class="card card-pad timeline">
            {#if events.length}
              {#each events as event (event.eventId)}<div class="timeline-item"><div class="timeline-dot"></div><div><h3>{event.message}</h3><p>{titleCase(event.stage)} · {shortDate(event.timestamp)} {event.data.simulated ? '· Simulated demo event' : ''}</p></div></div>{/each}
            {:else}
              {#each run.timeline as event (event.event_id)}<div class="timeline-item"><div class="timeline-dot"></div><div><h3>{titleCase(event.event_type)}</h3><p>{event.payload.message ?? event.payload.reason ?? 'Recorded in the audit chain'} · {shortDate(event.timestamp)}</p></div></div>{/each}
            {/if}
          </div>
        </section>

        <section class="section">
          <div class="section-title"><h2>Progressive scene strip</h2><span class="small muted">Keyframe · narration · verification</span></div>
          {#if run.scenes?.length}
            <div class="scene-strip">
              {#each run.scenes as scene (scene.id)}
                <article class="card scene-card">
                  {#if scene.assets.find((asset: any) => asset.type === 'image')}
                    <img src={scene.assets.find((asset: any) => asset.type === 'image').preview_url} alt={`Scene ${scene.position} Noura Glow keyframe`} />
                  {:else}<div class="asset-preview loading"><div class="spinner"></div>Planning scene</div>{/if}
                  <div style="display:flex;justify-content:space-between;align-items:center"><h3>Scene {scene.position}</h3><StatusBadge status={scene.verification_state} /></div>
                  {#if run.status === 'PLANNING'}
                    <label class="field"><span>Generation prompt</span><textarea bind:value={scene.prompt} aria-label={`Scene ${scene.position} prompt`}></textarea></label>
                    <label class="field"><span>Arabic narration</span><textarea dir="rtl" lang="ar" bind:value={scene.narration} aria-label={`Scene ${scene.position} narration`}></textarea></label>
                    <button class="btn btn-secondary" disabled={sceneSaving === scene.id} onclick={() => saveScene(scene)}>{sceneSaving === scene.id ? 'Saving…' : 'Save scene'}</button>
                  {:else}
                    <p>{scene.prompt}</p><p dir="rtl" lang="ar">{scene.narration}</p>
                  {/if}
                  <div class="actions"><span class="badge">{scene.provider}</span><span class="badge">{money(scene.cost_minor)}</span><span class="badge">Retry {scene.retry_count}</span></div>
                  {#if scene.assets.find((asset: any) => asset.type === 'narration')}<audio controls preload="none" src={scene.assets.find((asset: any) => asset.type === 'narration').preview_url} style="width:100%;margin-top:10px"></audio>{/if}
                </article>
              {/each}
            </div>
          {:else}<div class="card loading"><div class="spinner"></div>Storyboard scenes will appear as planning completes.</div>{/if}
        </section>

        <section class="section">
          <div class="section-title"><h2>Verification checks</h2><span class="badge"><ShieldCheck size={12} /> Layered</span></div>
          <div class="card card-pad check-list">
            {#each run.verification as check (check.id)}
              <div class="check" data-status={check.status}><div class="icon">{#if check.status === 'PASS'}<Check size={16} />{:else if check.status === 'FAIL'}<X size={16} />{:else}<AlertTriangle size={16} />{/if}</div><div><h3>{check.check_name}</h3><p>{check.explanation}</p><StatusBadge status={check.status} /></div></div>
            {/each}
          </div>
        </section>
      </div>

      <aside class="sticky-panel grid">
        <section class="card">
          <div class="card-head"><h2>Budget ledger</h2></div>
          <div class="card-pad grid" style="gap:14px">
            <div><span class="stat-label">Maximum mandate budget</span><strong class="stat-value">{money(run.budget_cap_minor, run.currency)}</strong></div>
            <div class="grid grid-2"><div><span class="stat-label">Authorized</span><strong>{money(run.authorized_minor, run.currency)}</strong></div><div><span class="stat-label">Spent</span><strong>{money(run.spent_minor, run.currency)}</strong></div><div><span class="stat-label">Retry reserve</span><strong>{money(run.retry_reserved_minor, run.currency)}</strong></div><div><span class="stat-label">Remaining</span><strong>{money(run.remaining_minor, run.currency)}</strong></div></div>
            <div class="progress"><span style={`width:${Math.min(100, run.spent_minor / run.authorized_minor * 100)}%`}></span></div>
            <div class="help">Estimated versus actual costs remain distinct in provider evidence. Payment state: {titleCase(run.payment_state)}.</div>
          </div>
        </section>
        <section class="card">
          <div class="card-head"><h2>Decision controls</h2></div>
          <div class="card-pad grid" style="gap:9px">
            <textarea bind:value={note} placeholder="Decision note (optional)" aria-label="Decision note"></textarea>
            <button class="btn btn-primary" disabled={!run.actions.approve.enabled || actionBusy} title={run.actions.approve.reason} onclick={() => action('approve', { note })}><Check size={16} /> Approve final delivery</button>
            <button class="btn btn-secondary" disabled={!run.actions.retry_component.enabled || actionBusy} title={run.actions.retry_component.reason} onclick={() => action('retry', { component: 'narration', reason: 'Failed verification' })}><RefreshCw size={16} /> Retry failed component</button>
            <button class="btn btn-danger" disabled={!run.actions.reject.enabled || actionBusy} onclick={() => action('reject', { note })}><CircleX size={16} /> Reject final delivery</button>
            <button class="btn btn-secondary" disabled={!run.actions.open_case.enabled || actionBusy} onclick={() => showCase = !showCase}><AlertTriangle size={16} /> Open redress case</button>
            {#if showCase}<textarea bind:value={caseReason} aria-label="Case reason"></textarea><button class="btn btn-danger" onclick={openCase}>Create evidence-backed case</button>{/if}
            {#if finalAsset}<a class="btn btn-secondary" href={finalAsset.download_url}><Download size={16} /> Download final asset</a>{/if}
            {#if allAssets[0]}<a class="btn btn-secondary" href="/assets"><FileJson size={16} /> View asset record</a>{/if}
            <a class="btn btn-secondary" href={`/evidence?run=${run.id}`}><Network size={16} /> View evidence graph</a>
          </div>
        </section>
      </aside>
    </div>
  {/if}
</AsyncState>
