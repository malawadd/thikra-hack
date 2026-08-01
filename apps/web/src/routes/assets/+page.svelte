<script lang="ts">
  import { onMount } from 'svelte';
  import Download from 'lucide-svelte/icons/download'; import Grid3X3 from 'lucide-svelte/icons/grid-3x3'; import List from 'lucide-svelte/icons/list'; import Search from 'lucide-svelte/icons/search'; import X from 'lucide-svelte/icons/x';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, money, shortDate, titleCase } from '$lib/api/client';
  import type { AssetRecord } from '$lib/types';
  let assets = $state<AssetRecord[]>([]);
  let loading = $state(true); let error = $state(''); let query = $state(''); let modality = $state(''); let provider = $state(''); let approval = $state(''); let view = $state<'grid' | 'list'>('grid'); let selected = $state<AssetRecord | null>(null);
  const filtered = $derived(assets.filter((asset) => (!query || `${asset.object_key} ${asset.model}`.toLowerCase().includes(query.toLowerCase())) && (!modality || asset.type === modality) && (!provider || asset.provider === provider) && (!approval || asset.approval_state === approval)));
  onMount(async () => { try { assets = (await api<{items: AssetRecord[]}>('/thikra/assets')).items; } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); } finally { loading = false; } });
</script>

<PageHeader eyebrow="Durable media" title="Asset Library" description="Inspect B2-compatible metadata, lineage, verification, payment linkage, and short-lived server-generated download routes. Demo assets are labeled fixtures.">
  {#snippet actions()}<div class="actions"><button class="btn btn-secondary" class:btn-primary={view === 'grid'} onclick={() => view = 'grid'} aria-label="Grid view"><Grid3X3 size={16} /></button><button class="btn btn-secondary" class:btn-primary={view === 'list'} onclick={() => view = 'list'} aria-label="List view"><List size={16} /></button></div>{/snippet}
</PageHeader>

<div class="card card-pad form-grid" style="margin-bottom:18px">
  <div class="field"><label for="asset-search">Search</label><div style="position:relative"><Search size={15} style="position:absolute;left:12px;top:13px;color:var(--muted)" /><input id="asset-search" style="padding-left:36px" bind:value={query} placeholder="Model, object key…" /></div></div>
  <div class="field"><label for="modality">Modality</label><select id="modality" bind:value={modality}><option value="">All</option><option>image</option><option>narration</option><option>final</option></select></div>
  <div class="field"><label for="provider">Provider</label><select id="provider" bind:value={provider}><option value="">All</option>{#each Array.from(new Set(assets.map((asset) => asset.provider))) as item}<option>{item}</option>{/each}</select></div>
  <div class="field"><label for="approval">Approval</label><select id="approval" bind:value={approval}><option value="">All</option><option>PENDING</option><option>APPROVED</option><option>REJECTED</option></select></div>
</div>

<AsyncState {loading} {error} empty={!loading && filtered.length === 0}>
  <div class:split-layout={selected}>
    {#if view === 'grid'}
      <div class="asset-grid">
        {#each filtered as asset (asset.id)}
          <button class="card asset-card" style="border:1px solid var(--line);padding:0;text-align:left" onclick={() => selected = asset}>
            {#if asset.content_type.startsWith('image/')}<img class="asset-preview" src={asset.preview_url} alt={`${titleCase(asset.type)} from ${asset.provider}`} />
            {:else if asset.content_type.startsWith('video/')}<video class="asset-preview" muted preload="metadata" src={asset.preview_url}></video>
            {:else}<div class="asset-preview" style="display:grid;place-items:center"><strong>{titleCase(asset.type)}</strong></div>{/if}
            <div class="asset-info"><div style="display:flex;justify-content:space-between;gap:8px"><h3>{titleCase(asset.type)}</h3><StatusBadge status={asset.approval_state} /></div><div class="small muted">{asset.provider} · {asset.model}</div><div class="small" style="margin-top:8px">{money(asset.cost_minor)} · {(asset.size/1024).toFixed(1)} KB</div></div>
          </button>
        {/each}
      </div>
    {:else}
      <div class="card table-wrap"><table><thead><tr><th>Asset</th><th>Provider / model</th><th>Run</th><th>Cost</th><th>Approval</th></tr></thead><tbody>{#each filtered as asset}<tr onclick={() => selected = asset} style="cursor:pointer"><td><strong>{titleCase(asset.type)}</strong><div class="mono">{asset.sha256.slice(0,18)}…</div></td><td>{asset.provider}<div class="small muted">{asset.model}</div></td><td class="mono">{asset.run_id.slice(0,8)}</td><td>{money(asset.cost_minor)}</td><td><StatusBadge status={asset.approval_state} /></td></tr>{/each}</tbody></table></div>
    {/if}
    {#if selected}
      <aside class="card sticky-panel">
        <div class="card-head"><h2>Asset record</h2><button class="btn btn-ghost" aria-label="Close details" onclick={() => selected = null}><X size={16} /></button></div>
        {#if selected.content_type.startsWith('image/')}<img src={selected.preview_url} alt="Selected asset preview" style="width:100%;max-height:360px;object-fit:cover" />
        {:else if selected.content_type.startsWith('video/')}<video controls src={selected.preview_url} style="width:100%"><track kind="captions" src="/demo/captions.vtt" srclang="ar" label="Arabic" /></video>{/if}
        <div class="card-pad grid" style="gap:13px"><div><span class="stat-label">Object key</span><div class="mono">{selected.object_key}</div></div><div><span class="stat-label">SHA-256</span><div class="mono">{selected.sha256}</div></div><div class="grid grid-2"><div><span class="stat-label">Provider</span><div>{selected.provider}</div></div><div><span class="stat-label">Model</span><div>{selected.model}</div></div><div><span class="stat-label">Content type</span><div>{selected.content_type}</div></div><div><span class="stat-label">Created</span><div>{shortDate(selected.created_at)}</div></div></div><div><span class="stat-label">Lineage</span><p class="small muted">{selected.parent_asset_ids.length} parents · {selected.child_asset_ids.length} derivatives</p></div><a class="btn btn-primary" href={selected.download_url}><Download size={16} /> Generate download</a></div>
      </aside>
    {/if}
  </div>
</AsyncState>
