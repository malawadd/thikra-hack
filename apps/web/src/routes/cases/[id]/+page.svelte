<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import Download from 'lucide-svelte/icons/download'; import MessageSquarePlus from 'lucide-svelte/icons/message-square-plus'; import Save from 'lucide-svelte/icons/save';
  import PageHeader from '$lib/components/PageHeader.svelte'; import AsyncState from '$lib/components/AsyncState.svelte'; import StatusBadge from '$lib/components/StatusBadge.svelte'; import { api, shortDate } from '$lib/api/client';
  const caseId = page.params.id; let item = $state<any>(null); let loading = $state(true); let error = $state(''); let busy = $state(false); let note = $state(''); let status = $state('OPEN'); let owner = $state(''); let resolution = $state('');
  async function load() { item = await api(`/thikra/cases/${caseId}`); status = item.status; owner = item.owner; resolution = item.resolution ?? ''; }
  onMount(async () => { try { await load(); } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); } finally { loading = false; } });
  async function save() { busy = true; try { item = await api(`/thikra/cases/${caseId}`, { method: 'PATCH', body: JSON.stringify({ status, owner, resolution: resolution || null }) }); } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); } finally { busy = false; } }
  async function addNote() { if (!note.trim()) return; busy = true; try { item = await api(`/thikra/cases/${caseId}/notes`, { method: 'POST', body: JSON.stringify({ body: note, author: 'Brand manager' }) }); note = ''; } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); } finally { busy = false; } }
</script>

<AsyncState {loading} {error}>
  {#if item}
    <PageHeader eyebrow="Incident case" title={`Case ${item.id.slice(0,8)}`} description={item.reason}>
      {#snippet actions()}<div class="actions"><StatusBadge status={item.status} /><a class="btn btn-secondary" href={`/api/thikra/cases/${item.id}/export`}><Download size={16} /> Export evidence JSON</a></div>{/snippet}
    </PageHeader>
    <div class="split-layout">
      <div>
        <section class="card"><div class="card-head"><h2>Evidence snapshot</h2><span class="badge">Immutable at opening</span></div><div class="card-pad"><pre class="mono" style="white-space:pre-wrap;max-height:420px;overflow:auto">{JSON.stringify(item.evidence_snapshot, null, 2)}</pre></div></section>
        <section class="card section"><div class="card-head"><h2>Case notes</h2></div><div class="card-pad timeline">{#each item.notes as entry}<div class="timeline-item"><div class="timeline-dot"></div><div><h3>{entry.author}</h3><p>{entry.body} · {shortDate(entry.created_at)}</p></div></div>{/each}</div><div class="card-pad" style="border-top:1px solid var(--line)"><textarea bind:value={note} placeholder="Add an observable fact or decision…" aria-label="Case note"></textarea><button class="btn btn-secondary" style="margin-top:9px" onclick={addNote} disabled={busy || !note.trim()}><MessageSquarePlus size={16} /> Add note</button></div></section>
      </div>
      <aside class="card sticky-panel"><div class="card-head"><h2>Case handling</h2></div><div class="card-pad grid"><div class="field"><label for="severity">Severity</label><input id="severity" value={item.severity} disabled /></div><div class="field"><label for="owner">Owner</label><input id="owner" bind:value={owner} /></div><div class="field"><label for="state">Status</label><select id="state" bind:value={status}><option>OPEN</option><option>INVESTIGATING</option><option>WAITING</option><option>RESOLVED</option></select></div><div class="field"><label for="resolution">Resolution</label><textarea id="resolution" bind:value={resolution} placeholder="Required when resolved"></textarea></div><button class="btn btn-primary" onclick={save} disabled={busy || (status === 'RESOLVED' && !resolution.trim())}><Save size={16} /> Save case</button><p class="help">A refund reference is only recorded after a supported external refund actually completes. An internal request is not labeled a refund.</p></div></aside>
    </div>
  {/if}
</AsyncState>
