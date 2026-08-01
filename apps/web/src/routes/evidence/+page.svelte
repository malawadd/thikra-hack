<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import Download from 'lucide-svelte/icons/download'; import Network from 'lucide-svelte/icons/network'; import ScrollText from 'lucide-svelte/icons/scroll-text';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import AsyncState from '$lib/components/AsyncState.svelte';
  import { api, shortDate, titleCase } from '$lib/api/client';
  let timeline = $state<any[]>([]); let graph = $state<any>({ nodes: [], edges: [] }); let loading = $state(true); let error = $state(''); let tab = $state<'timeline'|'graph'>('timeline'); let selected = $state<any>(null);
  const runId = page.url.searchParams.get('run');
  const point = (id: string) => { const index = graph.nodes.findIndex((node: any) => node.id === id); return { x: 120 + (index % 4) * 225, y: 75 + Math.floor(index / 4) * 145 }; };
  onMount(async () => { try { const suffix = runId ? `?run_id=${runId}` : ''; [timeline, graph] = await Promise.all([api<any>(`/thikra/evidence${suffix}`).then((r) => r.items), api(`/thikra/evidence/graph${suffix}`)]); } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); } finally { loading = false; } });
</script>

<PageHeader eyebrow="Accountability record" title="Evidence" description="Follow the chronological hash chain or inspect the relationship graph from principal mandate through payment, generation, evaluation, approval, and redress.">
  {#snippet actions()}<div class="actions"><button class="btn btn-secondary" class:btn-primary={tab === 'timeline'} onclick={() => tab = 'timeline'}><ScrollText size={16} /> Timeline</button><button class="btn btn-secondary" class:btn-primary={tab === 'graph'} onclick={() => tab = 'graph'}><Network size={16} /> Graph</button>{#if graph.run_id}<a class="btn btn-secondary" href={`/api/thikra/evidence/export/${graph.run_id}`}><Download size={16} /> Export</a>{/if}</div>{/snippet}
</PageHeader>

<AsyncState {loading} {error} empty={!loading && timeline.length === 0}>
  {#if tab === 'timeline'}
    <div class="card table-wrap"><table><thead><tr><th>Timestamp</th><th>Actor</th><th>Event</th><th>Run / references</th><th>Hash link</th></tr></thead><tbody>{#each timeline as event (event.event_id)}<tr><td>{shortDate(event.timestamp)}</td><td><strong>{titleCase(event.actor_type)}</strong><div class="small muted">{event.actor_id}</div></td><td><strong>{titleCase(event.event_type)}</strong><div class="small muted">{event.payload.message ?? event.payload.reason ?? 'Material state change'}</div></td><td><span class="mono">{event.run_id?.slice(0,8) ?? 'workspace'}</span><div class="small muted">{event.related_object_ids.length} linked objects</div></td><td><div class="mono">prev {event.previous_event_hash.slice(0,10)}…</div><div class="mono">this {event.event_hash.slice(0,10)}…</div></td></tr>{/each}</tbody></table></div>
  {:else}
    <div class="split-layout">
      <div class="card graph-wrap" aria-label="Evidence relationship graph">
        <svg viewBox="0 0 950 600" role="img">
          <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#9caf9f" /></marker></defs>
          {#each graph.edges as edge}
            {@const source = point(edge.source)}{@const target = point(edge.target)}
            <path class="graph-edge" d={`M${source.x+75},${source.y+24} C${source.x+125},${source.y+24} ${target.x-50},${target.y+24} ${target.x},${target.y+24}`}><title>{edge.label}</title></path>
          {/each}
          {#each graph.nodes as node}
            {@const p = point(node.id)}
            <g class="graph-node" class:selected={selected?.id === node.id} transform={`translate(${p.x},${p.y})`} onclick={() => selected = node} role="button" tabindex="0" onkeydown={(event) => event.key === 'Enter' && (selected = node)}>
              <rect width="150" height="50"></rect><text x="75" y="22" text-anchor="middle">{node.label}</text><text x="75" y="38" text-anchor="middle" style="font-size:9px;fill:#738078">{titleCase(node.kind)}</text>
            </g>
          {/each}
        </svg>
      </div>
      <aside class="card card-pad sticky-panel">{#if selected}<span class="badge">{selected.kind}</span><h2>{selected.label}</h2><p class="muted">{selected.detail}</p><div class="mono">{selected.id}</div>{:else}<div class="empty"><Network size={30} /><p>Select a node to inspect its backend relationship.</p></div>{/if}</aside>
    </div>
  {/if}
</AsyncState>
