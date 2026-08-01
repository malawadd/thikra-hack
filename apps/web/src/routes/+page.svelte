<script lang="ts">
  import { onMount } from 'svelte';
  import ArrowUpRight from 'lucide-svelte/icons/arrow-up-right'; import CircleDollarSign from 'lucide-svelte/icons/circle-dollar-sign'; import Clock3 from 'lucide-svelte/icons/clock-3'; import RotateCcw from 'lucide-svelte/icons/rotate-ccw'; import ShieldCheck from 'lucide-svelte/icons/shield-check'; import Sparkles from 'lucide-svelte/icons/sparkles'; import TriangleAlert from 'lucide-svelte/icons/triangle-alert';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, money, shortDate, titleCase } from '$lib/api/client';

  let data = $state<any>(null);
  let loading = $state(true);
  let error = $state('');
  onMount(async () => {
    try { data = await api('/thikra/overview'); }
    catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { loading = false; }
  });
  const maxBar = (items: any[]) => Math.max(1, ...items.map((item) => item.value));
</script>

<PageHeader eyebrow="Command center" title="Creative commerce, with receipts." description="Track every authorization, provider choice, generated asset, verification result, and human decision from one evidence-backed workspace.">
  {#snippet actions()}<a class="btn btn-primary" href="/briefs/new"><Sparkles size={16} /> Start a creative brief</a>{/snippet}
</PageHeader>

<AsyncState {loading} {error}>
  {#if data}
    <div class="actions" style="margin-bottom:18px">
      <span class="badge" data-tone="warning">Demo data</span>
      <span class="badge" data-tone="info">{data.environment} environment</span>
    </div>
    <section class="grid grid-4 appear">
      <div class="card stat-card" style="--accent:var(--mint-soft)"><span class="stat-label">Authorized budget</span><strong class="stat-value">{money(data.metrics.authorized_minor)}</strong><span class="stat-note">Bounded across active mandates</span></div>
      <div class="card stat-card" style="--accent:#e2f3ff"><span class="stat-label">Actual amount spent</span><strong class="stat-value">{money(data.metrics.spent_minor)}</strong><span class="stat-note">Separate from authorization</span></div>
      <div class="card stat-card" style="--accent:#fff3c8"><span class="stat-label">Savings against caps</span><strong class="stat-value">{money(data.metrics.savings_minor)}</strong><span class="stat-note">Unspent authorized capacity</span></div>
      <div class="card stat-card"><span class="stat-label">Acceptance rate</span><strong class="stat-value">{data.metrics.acceptance_rate}%</strong><span class="stat-note">After verification + review</span></div>
      <div class="card stat-card"><span class="stat-label">Active / completed</span><strong class="stat-value">{data.metrics.active_runs} / {data.metrics.completed_runs}</strong><span class="stat-note">Current run portfolio</span></div>
      <div class="card stat-card"><span class="stat-label">Retry rate</span><strong class="stat-value">{data.metrics.retry_rate}%</strong><span class="stat-note"><RotateCcw size={12} style="display:inline" /> Policy-bounded retries</span></div>
      <div class="card stat-card"><span class="stat-label">Human escalation</span><strong class="stat-value">{data.metrics.escalation_rate}%</strong><span class="stat-note"><ShieldCheck size={12} style="display:inline" /> Rights and approval gates</span></div>
      <div class="card stat-card"><span class="stat-label">Failed delivery count</span><strong class="stat-value">{data.metrics.failed_deliveries}</strong><span class="stat-note"><TriangleAlert size={12} style="display:inline" /> Terminal failures only</span></div>
    </section>

    <div class="grid grid-2 section">
      <section class="card">
        <div class="card-head"><h2>Run portfolio</h2><a class="btn btn-ghost" href="/runs">View all <ArrowUpRight size={14} /></a></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Campaign</th><th>Stage</th><th>Spend</th><th>Status</th></tr></thead>
            <tbody>
              {#each data.runs as run (run.id)}
                <tr>
                  <td><a href={`/runs/${run.id}`}><strong>{run.campaign_name}</strong></a><div class="small muted">{shortDate(run.created_at)}</div></td>
                  <td>{run.current_stage}</td>
                  <td>{money(run.spent_minor, run.currency)} <span class="muted">/ {money(run.authorized_minor, run.currency)}</span></td>
                  <td><StatusBadge status={run.status} /></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </section>
      <section class="card">
        <div class="card-head"><h2>Provider usage</h2><span class="badge"><CircleDollarSign size={12} /> Evidence-linked</span></div>
        <div class="card-pad grid" style="gap:15px">
          {#each data.provider_usage as item}
            <div>
              <div style="display:flex;justify-content:space-between;margin-bottom:7px"><strong class="small">{titleCase(item.label)}</strong><span class="small muted">{item.value} assets</span></div>
              <div class="progress"><span style={`width:${Math.round(item.value / maxBar(data.provider_usage) * 100)}%`}></span></div>
            </div>
          {/each}
        </div>
      </section>
    </div>

    <div class="grid grid-2 section">
      <section>
        <div class="section-title"><h2>Recent audit events</h2><a href="/evidence">Open evidence</a></div>
        <div class="card card-pad timeline">
          {#each data.recent_events as event}
            <div class="timeline-item"><div class="timeline-dot"></div><div><h3>{titleCase(event.event_type)}</h3><p>{event.payload.message ?? event.payload.reason ?? 'Material action recorded'} · {shortDate(event.timestamp)}</p></div></div>
          {/each}
        </div>
      </section>
      <section>
        <div class="section-title"><h2>Recent media assets</h2><a href="/assets">Open library</a></div>
        <div class="asset-grid">
          {#each data.recent_assets.slice(0, 4) as asset (asset.id)}
            <a class="card asset-card" href="/assets">
              {#if asset.content_type.startsWith('image/')}
                <img class="asset-preview" src={asset.preview_url} alt={`${asset.type} generated by ${asset.provider}`} />
              {:else}
                <div class="asset-preview" style="display:grid;place-items:center"><Clock3 size={34} /></div>
              {/if}
              <div class="asset-info"><h3>{titleCase(asset.type)}</h3><span class="small muted">{asset.provider} · {asset.model}</span></div>
            </a>
          {/each}
        </div>
      </section>
    </div>
  {/if}
</AsyncState>
