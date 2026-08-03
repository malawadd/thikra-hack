<script lang="ts">
  import { onMount } from 'svelte';
  import { SvelteMap, SvelteSet } from 'svelte/reactivity';
  import { invoke } from '@tauri-apps/api/core';
  import { AlignCenter, Captions, ChevronDown, ChevronUp, Copy, Download, Eye, EyeOff, Film, ImagePlus, LoaderCircle, Lock, Music, Pause, Play, Plus, Redo2, Scissors, Trash2, Type, Undo2, Unlock, Volume2, VolumeX, WandSparkles, ZoomIn } from 'lucide-svelte';
  import { api, assetDownloadUrl, assetProxyUrl, assetThumbnailUrl, assetUrl, renderEvents } from '$lib/api';
  import type { ProviderMatrix, SequenceClip, SequenceDocument, SequencePreset, SequenceRevision, SequenceTrack, StudioAsset, StudioRender, StudioSequence } from '$lib/types';
  import { deleteClips, reduceRenderEvent, snapFrame, splitClip } from '$lib/timeline';

  export let projectId: string;
  export let projectName: string;
  export let providerMatrix: ProviderMatrix;
  export let onAssetsChanged: () => Promise<void> = async () => {};

  let sequences: StudioSequence[] = [], sequence: StudioSequence | null = null;
  let document: SequenceDocument | null = null, assets: StudioAsset[] = [];
  let playhead = 0, zoom = 80, selected = new SvelteSet<string>(), loading = true, saving = false, playing = false;
  let error = '', notice = '', ripple = false, importInput: HTMLInputElement;
  let history: SequenceDocument[] = [], historyIndex = -1, saveTimer: ReturnType<typeof setTimeout> | null = null;
  let viewTimer: ReturnType<typeof setTimeout> | null = null;
  let playbackTimer: ReturnType<typeof setInterval> | null = null;
  let programVideo: HTMLVideoElement | null = null;
  const audioElements = new SvelteMap<string, HTMLAudioElement>();
  let render: StudioRender | null = null, renderSource: EventSource | null = null, exportPreset: SequencePreset = 'landscape_1080';
  let showGenerator = false, generationKind: 'image'|'video' = 'image', generationVendor = '', generationModel = '', generationPrompt = '';
  let generationReference = '', generationVariants = 1, generationDuration = 4000;
  let generationEstimate: {estimate_hash:string;estimated_cost_minor:number;within_budget:boolean;credential_source:string}|null = null;
  let generationResumeEstimate: {estimate_hash:string;estimated_cost_minor:number;within_budget:boolean;checkpoint_asset_ids:string[]}|null = null;
  let generationJob: {id:string;status:string;progress:number;result_asset_ids:string[];error?:string}|null = null;
  let captionEstimate: {estimate_hash:string;estimated_cost_minor:number;within_budget:boolean}|null = null;
  let captionJob: {id:string;status:string;progress:number;cues:{id:string;start_ms:number;end_ms:number;text:string}[];error?:string}|null = null;
  let showAgent = false, agentPrompt = '';
  let agentProposal: {id:string;base_revision_id:string;rationale:string;operations:{id:string;type:string;summary:string;depends_on:string[];clip?:SequenceClip;clip_id?:string;patch?:Record<string,unknown>}[]}|null = null;
  let selectedAgentOps = new SvelteSet<string>();
  let lastProject = '';

  $: selectedClip = document?.clips.find((clip) => selected.has(clip.id)) ?? null;
  $: duration = Math.max(10_000, ...(document?.clips.map((clip) => clip.start_ms + clip.duration_ms) ?? [0]));
  $: activeClips = document?.clips.filter((clip) => clip.start_ms <= playhead && clip.start_ms + clip.duration_ms > playhead) ?? [];
  $: activeVisual = [...activeClips].reverse().find((clip) => clip.kind === 'video' || clip.kind === 'image');
  $: activeText = activeClips.filter((clip) => clip.kind === 'text' || clip.kind === 'caption');
  $: activeAudio = activeClips.filter((clip)=>clip.kind==='audio' && !clip.audio.muted);
  $: activeAsset = assets.find((asset) => asset.id === activeVisual?.asset_id);
  $: generationProviders = (providerMatrix[generationKind] ?? []).filter((item) => item.key_available);
  $: generationProvider = generationProviders.find((item) => item.vendor === generationVendor);
  $: ghostClips = agentProposal?.operations.filter((item)=>item.type==='add_clip'||item.type==='add_caption').map((item)=>item.clip).filter(Boolean) as SequenceClip[] ?? [];
  $: if (projectId && projectId !== lastProject) { lastProject = projectId; void bootstrap(); }

  const id = (prefix: string) => `${prefix}-${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const defaults = {
    transform: { fit:'fill' as const, position_x:.5, position_y:.5, scale:1, rotation:0, opacity:1, fade_in_ms:0, fade_out_ms:0, ken_burns:false },
    audio: { gain_db:0, muted:false, fade_in_ms:0, fade_out_ms:0, role:'other' as const },
  };
  function cloneDocument() { return structuredClone(document!); }
  function remember(summary = 'Timeline edit') {
    if (!document) return;
    if (document.clips.some((clip)=>clip.kind==='caption') && /(Move|Trim|Split|Delete|audio|Insert)/i.test(summary)) document.captions_stale = true;
    history = [...history.slice(0, historyIndex + 1), cloneDocument()].slice(-60);
    historyIndex = history.length - 1;
    scheduleSave(summary);
  }
  function scheduleSave(summary: string) {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => void saveRevision(summary), 420);
  }
  function scheduleViewSave(){if(!sequence)return;if(viewTimer)clearTimeout(viewTimer);viewTimer=setTimeout(()=>void api(`/studio/sequences/${sequence!.id}/view`,{method:'PATCH',body:JSON.stringify({playhead_ms:Math.min(300000,playhead),zoom,selection:[...selected],panel_layout:{}})}),350);}
  async function saveRevision(summary = 'Timeline edit') {
    if (!sequence || !document || saving) return;
    saving = true;
    try {
      const revision = await api<SequenceRevision>(`/studio/sequences/${sequence.id}/revisions`, { method:'POST', body:JSON.stringify({ base_revision_id:sequence.current_revision_id, document, summary }) });
      sequence = { ...sequence, current_revision_id:revision.id, current_revision_number:revision.number, revision };
      sequences = sequences.map((item) => item.id === sequence?.id ? { ...item, ...sequence } : item);
    } catch (cause) { error = cause instanceof Error ? cause.message : 'Timeline could not be saved'; }
    finally { saving = false; }
  }
  async function bootstrap() {
    loading = true; error = '';
    try {
      const response = await api<{items:StudioSequence[]}>(`/studio/projects/${projectId}/sequences`);
      sequences = response.items;
      if (!sequences.length) {
        const created = await api<StudioSequence>(`/studio/projects/${projectId}/sequences`, { method:'POST', body:JSON.stringify({ name:'Main edit', preset:'landscape_1080' }) });
        sequences = [created];
      }
      await loadSequence(sequences[0].id);
      await loadAssets();
    } catch (cause) { error = cause instanceof Error ? cause.message : 'Editor could not open'; }
    finally { loading = false; }
  }
  async function loadSequence(sequenceId: string) {
    sequence = await api<StudioSequence>(`/studio/sequences/${sequenceId}`);
    document = structuredClone(sequence.revision!.document);
    playhead = sequence.view_state.playhead_ms ?? 0; zoom = sequence.view_state.zoom ?? 80;
    selected = new SvelteSet(); history = [cloneDocument()]; historyIndex = 0; exportPreset = document.preset;
  }
  async function createSequence() {
    const created = await api<StudioSequence>(`/studio/projects/${projectId}/sequences`, { method:'POST', body:JSON.stringify({ name:`Cut ${sequences.length + 1}`, preset:exportPreset }) });
    sequences = [...sequences, created]; await loadSequence(created.id);
  }
  async function loadAssets() { assets = (await api<{items:StudioAsset[]}>(`/studio/projects/${projectId}/assets`)).items;const pending=assets.filter((asset)=>asset.analysis_status==='PENDING').slice(0,4);for(const asset of pending)void api(`/studio/assets/${asset.id}/prepare`,{method:'POST'});if(pending.length)setTimeout(async()=>{assets=(await api<{items:StudioAsset[]}>(`/studio/projects/${projectId}/assets`)).items;},1200); }
  async function importAssets(event: Event) {
    const files = (event.currentTarget as HTMLInputElement).files; if (!files) return;
    for (const file of files) { const form = new FormData(); form.set('upload', file); await api(`/studio/projects/${projectId}/assets`, { method:'POST', body:form }); }
    (event.currentTarget as HTMLInputElement).value = ''; await loadAssets(); await onAssetsChanged(); notice = 'Media added to the shared project library';
  }
  function trackFor(kind: string): SequenceTrack | undefined {
    const trackKind = kind === 'image' || kind === 'video' ? 'visual' : kind === 'audio' ? 'audio' : kind;
    return document?.tracks.find((track) => track.kind === trackKind && !track.locked);
  }
  function insertAsset(asset: StudioAsset) {
    if (!document || !asset.asset_type || !['image','video','audio'].includes(asset.asset_type)) return;
    const track = trackFor(asset.asset_type); if (!track) { error = `Add or unlock a ${asset.asset_type} track first`; return; }
    const kind = asset.asset_type as 'image'|'video'|'audio';
    document.clips = [...document.clips, { id:id('clip'), track_id:track.id, kind, name:asset.name, asset_id:asset.id, start_ms:snap(playhead), duration_ms:kind === 'image' ? 3000 : Math.min(asset.duration_ms ?? 5000, 300000-playhead), source_in_ms:0, transition_in:'cut', transition_out:'cut', transition_duration_ms:0, transform:{...defaults.transform}, audio:{...defaults.audio, role:kind === 'audio' ? 'other' : 'source'} }];
    selected = new SvelteSet([document.clips.at(-1)!.id]); remember(`Insert ${asset.name}`);
  }
  function addText(kind: 'text'|'caption' = 'text') {
    if (!document) return; const track = trackFor(kind); if (!track) return;
    const clip: SequenceClip = { id:id(kind), track_id:track.id, kind, name:kind === 'caption' ? 'Caption' : 'Title', start_ms:snap(playhead), duration_ms:3000, source_in_ms:0, transition_in:'cut', transition_out:'cut', transition_duration_ms:0, transform:{...defaults.transform}, audio:{...defaults.audio}, text:{ content:kind === 'caption' ? 'Edit caption text' : 'Your title', font_family:kind === 'caption' ? 'Noto Sans Arabic' : 'Noto Sans', font_size:kind === 'caption' ? 48 : 72, font_weight:700, color:'#ffffff', background:kind === 'caption' ? '#00000099' : '#00000000', align:'center', position_x:.5, position_y:kind === 'caption' ? .86 : .5 } };
    document.clips = [...document.clips, clip]; selected = new SvelteSet([clip.id]); remember(`Add ${kind}`);
  }
  function addTrack(kind: SequenceTrack['kind']) {
    if (!document || document.tracks.length >= 16) return;
    document.tracks = [...document.tracks, { id:id('track'), name:`${kind[0].toUpperCase()+kind.slice(1)} ${document.tracks.filter((item)=>item.kind===kind).length+1}`, kind, order:document.tracks.length, locked:false, hidden:false, muted:false }]; remember(`Add ${kind} track`);
  }
  function updateTrack(trackId:string, patch:Partial<SequenceTrack>) { if (!document) return; document.tracks=document.tracks.map((item)=>item.id===trackId?{...item,...patch}:item); remember('Track controls'); }
  function reorderTrack(track:SequenceTrack, direction:number) { if (!document) return; const other=document.tracks.find((item)=>item.order===track.order+direction); if(!other)return; const old=track.order; document.tracks=document.tracks.map((item)=>item.id===track.id?{...item,order:other.order}:item.id===other.id?{...item,order:old}:item); remember('Reorder tracks'); }
  const snap = (value:number) => Math.round(snapFrame(value));
  function selectClip(event:MouseEvent, clip:SequenceClip) { if(event.ctrlKey || event.metaKey){const next=new SvelteSet(selected); if(next.has(clip.id))next.delete(clip.id);else next.add(clip.id);selected=next;}else selected=new SvelteSet([clip.id]);scheduleViewSave(); }
  function beginDrag(event:PointerEvent, clip:SequenceClip, mode:'move'|'left'|'right') {
    if (document?.tracks.find((track)=>track.id===clip.track_id)?.locked) return;
    event.preventDefault(); event.stopPropagation(); selectClip(event, clip);
    const startX=event.clientX, original=structuredClone(clip), before=cloneDocument();
    const move=(next:PointerEvent)=>{ if(!document)return; const delta=snap((next.clientX-startX)/zoom*1000); document.clips=document.clips.map((item)=>{if(item.id!==clip.id)return item; if(mode==='move')return {...item,start_ms:Math.max(0,original.start_ms+delta)}; if(mode==='left'){const change=Math.min(original.duration_ms-33, Math.max(-original.source_in_ms,delta));return {...item,start_ms:original.start_ms+change,source_in_ms:original.source_in_ms+change,duration_ms:original.duration_ms-change};} return {...item,duration_ms:Math.max(33,original.duration_ms+delta)};});};
    const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);history=[...history.slice(0,historyIndex+1),before,cloneDocument()].slice(-60);historyIndex=history.length-1;scheduleSave(mode==='move'?'Move clip':'Trim clip');};
    window.addEventListener('pointermove',move); window.addEventListener('pointerup',up,{once:true});
  }
  function splitSelected() { if(!document || !selectedClip)return; const result=splitClip(selectedClip,playhead,id('clip'));if(!result)return;const [left,right]=result;document.clips=document.clips.map((item)=>item.id===left.id?left:item).concat(right);selected=new SvelteSet([right.id]);remember('Split clip'); }
  function duplicateSelected() { if(!document||!selectedClip)return;const copy={...structuredClone(selectedClip),id:id('clip'),name:`${selectedClip.name} copy`,start_ms:selectedClip.start_ms+selectedClip.duration_ms};document.clips=[...document.clips,copy];selected=new SvelteSet([copy.id]);remember('Duplicate clip'); }
  function deleteSelected() { if(!document||!selected.size)return;document.clips=deleteClips(document.clips,selected,ripple);selected=new SvelteSet();remember(ripple?'Ripple delete':'Delete clips'); }
  function updateClip(patch:Partial<SequenceClip>) { if(!document||!selectedClip)return;document.clips=document.clips.map((item)=>item.id===selectedClip.id?{...item,...patch}:item);remember('Edit clip'); }
  function updateTransform(field:keyof SequenceClip['transform'], value:number|string|boolean) { if(!selectedClip)return;updateClip({transform:{...selectedClip.transform,[field]:value}}); }
  function updateAudio(field:keyof SequenceClip['audio'], value:number|boolean|string) { if(!selectedClip)return;updateClip({audio:{...selectedClip.audio,[field]:value}});document!.captions_stale=true; }
  function updateText(field:string,value:string|number) { if(!selectedClip?.text)return;updateClip({text:{...selectedClip.text,[field]:value}}); }
  function undo(){if(historyIndex<=0||!document)return;historyIndex--;document=structuredClone(history[historyIndex]);scheduleSave('Undo timeline edit');}
  function redo(){if(historyIndex>=history.length-1||!document)return;historyIndex++;document=structuredClone(history[historyIndex]);scheduleSave('Redo timeline edit');}
  function registerAudio(node:HTMLAudioElement, clipId:string){audioElements.set(clipId,node);return{destroy(){audioElements.delete(clipId)}};}
  function syncProgram(){if(programVideo&&activeVisual){const source=(activeVisual.source_in_ms+playhead-activeVisual.start_ms)/1000;if(Math.abs(programVideo.currentTime-source)>.18)programVideo.currentTime=Math.max(0,source);if(playing&&programVideo.paused)void programVideo.play();if(!playing&&!programVideo.paused)programVideo.pause();}const narration=activeAudio.some((clip)=>clip.audio.role==='narration');for(const clip of activeAudio){const element=audioElements.get(clip.id);if(!element)continue;const source=(clip.source_in_ms+playhead-clip.start_ms)/1000;if(Math.abs(element.currentTime-source)>.18)element.currentTime=Math.max(0,source);const gain=Math.min(1,10**(clip.audio.gain_db/20));element.volume=clip.audio.role==='music'&&narration&&document?.duck_music_under_narration?gain*.25:gain;if(playing&&element.paused)void element.play();if(!playing&&!element.paused)element.pause();}}
  function togglePlayback(){playing=!playing;if(playbackTimer)clearInterval(playbackTimer);syncProgram();if(playing){playbackTimer=setInterval(()=>{playhead+=33;syncProgram();if(playhead>=duration){playhead=0;playing=false;programVideo?.pause();if(playbackTimer)clearInterval(playbackTimer);}},33);}}
  function seek(event:MouseEvent){const element=event.currentTarget as HTMLElement;playhead=snap((event.offsetX/element.clientWidth)*duration);syncProgram();scheduleViewSave();}
  async function startRender(){if(!sequence||!document)return;await saveRevision('Prepare export');render=await api<StudioRender>(`/studio/sequences/${sequence.id}/renders`,{method:'POST',body:JSON.stringify({revision_id:sequence.current_revision_id,preset:exportPreset,confirm:true})});watchRender();}
  function watchRender(){renderSource?.close();if(!render)return;renderSource=renderEvents(render.id,(event)=>{const data=JSON.parse(event.data);render=reduceRenderEvent(render!,data);if(['SUCCEEDED','FAILED','CANCELLED'].includes(render.status)){renderSource?.close();void loadAssets();void onAssetsChanged();}},()=>{});}
  async function cancelRender(){if(!render)return;render=await api<StudioRender>(`/studio/renders/${render.id}/cancel`,{method:'POST'});}
  async function retryRender(){if(!render)return;render=await api<StudioRender>(`/studio/renders/${render.id}/retry`,{method:'POST',body:JSON.stringify({confirm:true})});watchRender();}
  async function estimateCaptions(){if(!sequence)return;await saveRevision('Prepare automatic captions');captionEstimate=await api(`/studio/sequences/${sequence.id}/captions/estimate`,{method:'POST',body:JSON.stringify({revision_id:sequence.current_revision_id,language:null})});}
  async function transcribeCaptions(){if(!sequence||!captionEstimate)return;captionJob=await api(`/studio/sequences/${sequence.id}/captions/jobs`,{method:'POST',body:JSON.stringify({revision_id:sequence.current_revision_id,language:null,estimate_hash:captionEstimate.estimate_hash,confirm:true})});captionEstimate=null;pollCaptions();}
  async function pollCaptions(){if(!captionJob)return;const updated=await api<typeof captionJob>(`/studio/captions/jobs/${captionJob.id}`);captionJob=updated;if(['QUEUED','RUNNING'].includes(updated.status)){setTimeout(pollCaptions,700);return;}if(updated.status!=='SUCCEEDED')error=updated.error??'Caption transcription failed';}
  async function cancelCaptions(){if(!captionJob)return;captionJob=await api<typeof captionJob>(`/studio/captions/jobs/${captionJob.id}/cancel`,{method:'POST'});}
  async function applyCaptions(){if(!sequence||!captionJob)return;const revision=await api<SequenceRevision>(`/studio/sequences/${sequence.id}/captions/apply`,{method:'POST',body:JSON.stringify({base_revision_id:sequence.current_revision_id,cues:captionJob.cues})});sequence={...sequence,current_revision_id:revision.id,current_revision_number:revision.number,revision};document=structuredClone(revision.document);history=[cloneDocument()];historyIndex=0;captionJob=null;notice='Automatic captions applied as editable timeline clips';}
  async function askEditorAgent(){if(!sequence||!agentPrompt.trim())return;const proposal=await api<NonNullable<typeof agentProposal>>(`/studio/sequences/${sequence.id}/agent-proposals`,{method:'POST',body:JSON.stringify({base_revision_id:sequence.current_revision_id,prompt:agentPrompt,selected_clip_ids:[...selected]})});agentProposal=proposal;selectedAgentOps=new SvelteSet(proposal.operations.map((item)=>item.id));}
  function toggleAgentOp(operationId:string){const next=new SvelteSet(selectedAgentOps);if(next.has(operationId))next.delete(operationId);else next.add(operationId);if(agentProposal){let changed=true;while(changed){changed=false;for(const operation of agentProposal.operations){if(next.has(operation.id))for(const dependency of operation.depends_on)if(!next.has(dependency)){next.add(dependency);changed=true;}}}}selectedAgentOps=next;}
  async function applyEditorAgent(){if(!sequence||!agentProposal||!selectedAgentOps.size)return;const revision=await api<SequenceRevision>(`/studio/sequences/${sequence.id}/agent-proposals/${agentProposal.id}/apply`,{method:'POST',body:JSON.stringify({base_revision_id:sequence.current_revision_id,operation_ids:[...selectedAgentOps]})});sequence={...sequence,current_revision_id:revision.id,current_revision_number:revision.number,revision};document=structuredClone(revision.document);history=[cloneDocument()];historyIndex=0;agentProposal=null;agentPrompt='';showAgent=false;notice='Approved agent edits applied as one revision';}
  async function nativeSave(assetId:string){try{const saved=await invoke<string|null>('save_studio_asset',{assetId,suggestedName:`${projectName}-${exportPreset}.mp4`});if(saved)notice=`Saved to ${saved}`;}catch{window.open(assetDownloadUrl(assetId),'_blank','noopener,noreferrer');}}
  function chooseGenerationVendor(vendor:string){generationVendor=vendor;generationModel=generationProviders.find((item)=>item.vendor===vendor)?.default_model??'';generationEstimate=null;}
  function generationPayload(){return {kind:generationKind,vendor:generationVendor,model:generationModel,prompt:generationPrompt,variants:generationVariants,duration_ms:generationKind==='video'?generationDuration:null,reference_asset_id:generationReference||null};}
  async function estimateGeneration(){generationEstimate=await api(`/studio/projects/${projectId}/editor-generation/estimate`,{method:'POST',body:JSON.stringify(generationPayload())});}
  async function runGeneration(){if(!generationEstimate||!sequence)return;generationJob=await api(`/studio/projects/${projectId}/editor-generation/jobs`,{method:'POST',body:JSON.stringify({...generationPayload(),sequence_id:sequence.id,estimate_hash:generationEstimate.estimate_hash,confirm:true})});generationEstimate=null;pollGeneration();}
  async function pollGeneration(){if(!generationJob)return;const updated=await api<typeof generationJob>(`/studio/editor-generation/jobs/${generationJob.id}`);generationJob=updated;if(['QUEUED','RUNNING'].includes(updated.status)){setTimeout(pollGeneration,800);return;}if(updated.status==='SUCCEEDED'){await loadAssets();await onAssetsChanged();notice='Generated media is ready for review. Insert it explicitly from the asset bin.';}else error=updated.error??'Generation failed';}
  async function cancelGeneration(){if(!generationJob)return;generationJob=await api<typeof generationJob>(`/studio/editor-generation/jobs/${generationJob.id}/cancel`,{method:'POST'});}
  async function reviewGenerationResume(){if(!generationJob)return;generationResumeEstimate=await api(`/studio/editor-generation/jobs/${generationJob.id}/resume-estimate`,{method:'POST'});}
  async function resumeGeneration(){if(!generationJob||!generationResumeEstimate)return;generationJob=await api(`/studio/editor-generation/jobs/${generationJob.id}/resume`,{method:'POST',body:JSON.stringify({estimate_hash:generationResumeEstimate.estimate_hash,confirm:true})});generationResumeEstimate=null;pollGeneration();}
  function keyboard(event:KeyboardEvent){if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();if(event.shiftKey)redo();else undo();}else if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='d'){event.preventDefault();duplicateSelected();}else if(event.key==='Delete'||event.key==='Backspace'){if((event.target as HTMLElement).matches('input,textarea'))return;deleteSelected();}else if(event.key===' '){if((event.target as HTMLElement).matches('input,textarea'))return;event.preventDefault();togglePlayback();}}
  onMount(()=>()=>{if(saveTimer)clearTimeout(saveTimer);if(viewTimer)clearTimeout(viewTimer);if(playbackTimer)clearInterval(playbackTimer);renderSource?.close();});
</script>

<svelte:window onkeydown={keyboard}/>
{#if loading}<div class="editor-loading"><LoaderCircle class="spin" size={22}/>Opening the edit workspace…</div>
{:else if document && sequence}
<div class="editor-shell">
  <aside class="asset-bin">
    <header><div><strong>Project media</strong><small>{assets.length} shared assets</small></div><button class="editor-icon" onclick={()=>importInput.click()} title="Import media"><Plus size={15}/></button></header>
    <input bind:this={importInput} class="hidden-input" multiple type="file" accept="image/png,image/jpeg,image/webp,video/mp4,video/webm,video/quicktime,audio/wav,audio/mpeg,audio/mp4" onchange={importAssets}/>
    <div class="bin-actions"><button onclick={()=>showGenerator=!showGenerator}><WandSparkles size={14}/>Generate</button><button onclick={()=>addText()}><Type size={14}/>Title</button><button onclick={()=>showAgent=!showAgent}><WandSparkles size={14}/>Agent edit</button><button onclick={()=>importInput.click()}><ImagePlus size={14}/>Import</button></div>
    {#if showAgent}<section class="quick-generate agent-edit"><b>Reviewable editor agent</b><textarea bind:value={agentPrompt} placeholder="Tighten this cut and add a clear opening title…"></textarea><button class="editor-primary" onclick={askEditorAgent} disabled={!agentPrompt.trim()}>Propose timeline edits</button>{#if agentProposal}<p>{agentProposal.rationale}</p>{#each agentProposal.operations as operation}<label><input type="checkbox" checked={selectedAgentOps.has(operation.id)} onchange={()=>toggleAgentOp(operation.id)}/><span><strong>{operation.summary}</strong><small>{operation.type.replaceAll('_',' ')}</small></span></label>{/each}<button class="editor-primary" onclick={applyEditorAgent} disabled={!selectedAgentOps.size}>Apply selected operations</button>{/if}<small>Ghost clips and field changes are proposals only. Nothing renders without approval.</small></section>{/if}
    {#if showGenerator}<section class="quick-generate"><b>Generate for this edit</b><div><button class:active={generationKind==='image'} onclick={()=>{generationKind='image';generationEstimate=null}}>Image</button><button class:active={generationKind==='video'} onclick={()=>{generationKind='video';generationEstimate=null}}>Video</button></div><select value={generationVendor} onchange={(event)=>chooseGenerationVendor(event.currentTarget.value)}><option value="">Configured provider</option>{#each generationProviders as provider}<option value={provider.vendor}>{provider.vendor}</option>{/each}</select><select bind:value={generationModel} disabled={!generationProvider} onchange={()=>generationEstimate=null}><option value="">Model</option>{#each generationProvider?.suggested_models??[] as model}<option>{model}</option>{/each}</select>{#if generationKind==='video'}<select bind:value={generationReference} onchange={()=>generationEstimate=null}><option value="">Text only{generationProvider&&!generationProvider.supports_text_only?' (unsupported)':''}</option>{#each assets.filter((asset)=>asset.asset_type==='image') as asset}<option value={asset.id}>Reference · {asset.name}</option>{/each}</select><select bind:value={generationDuration} onchange={()=>generationEstimate=null}>{#each generationProvider?.duration_grid??[4,5,8,10] as seconds}<option value={seconds*1000}>{seconds}s</option>{/each}</select>{/if}<textarea bind:value={generationPrompt} oninput={()=>generationEstimate=null} placeholder="Describe the additional shot…"></textarea>{#if generationJob&&['QUEUED','RUNNING'].includes(generationJob.status)}<div><button class="editor-primary" disabled><LoaderCircle class="spin" size={13}/>{generationJob.progress}% generating</button><button class="editor-danger" onclick={cancelGeneration}>Cancel</button></div>{:else if generationJob&&['FAILED','CANCELLED'].includes(generationJob.status)&&generationResumeEstimate}<button class="editor-primary" onclick={resumeGeneration} disabled={!generationResumeEstimate.within_budget}>Resume · ${(generationResumeEstimate.estimated_cost_minor/100).toFixed(2)} remaining</button>{:else if generationJob&&['FAILED','CANCELLED'].includes(generationJob.status)}<button class="editor-primary" onclick={reviewGenerationResume}>Review resume cost</button>{:else if generationEstimate}<button class="editor-primary" onclick={runGeneration} disabled={!generationEstimate.within_budget}>Confirm · ${(generationEstimate.estimated_cost_minor/100).toFixed(2)} estimated</button>{:else}<button class="editor-primary" onclick={estimateGeneration} disabled={!generationVendor||!generationModel||!generationPrompt.trim()}>Review generation cost</button>{/if}<small>Results enter the bin for review and are never auto-inserted. Existing assets incur no provider charge.</small></section>{/if}
    <div class="asset-list">{#each assets as asset}<article draggable="true"><button class="asset-preview" ondblclick={()=>insertAsset(asset)}>{#if asset.content_type.startsWith('image/')}<img src={asset.analysis_status==='READY'?assetThumbnailUrl(asset.id):assetUrl(asset.id)} alt=""/>{:else if asset.content_type.startsWith('video/')}<img src={assetThumbnailUrl(asset.id)} alt=""/><Film size={17}/>{:else}<div class="audio-thumb"><Music size={20}/></div>{/if}</button><div><strong>{asset.name}</strong><small>{asset.source_kind?.toLowerCase()} · {asset.duration_ms?`${(asset.duration_ms/1000).toFixed(1)}s`:asset.width?`${asset.width}×${asset.height}`:'analyzing'}</small><button onclick={()=>insertAsset(asset)}>Insert at playhead</button></div></article>{/each}</div>
  </aside>
  <main class="editor-main">
    <header class="editor-toolbar"><div><select value={sequence.id} onchange={(event)=>loadSequence((event.currentTarget as HTMLSelectElement).value)}>{#each sequences as item}<option value={item.id}>{item.name}</option>{/each}</select><button class="editor-icon" onclick={createSequence}><Plus size={14}/></button></div><span>{projectName} · revision {sequence.current_revision_number}{#if saving} · saving…{/if}</span><div><button class="editor-icon" onclick={undo} disabled={historyIndex<=0}><Undo2 size={15}/></button><button class="editor-icon" onclick={redo} disabled={historyIndex>=history.length-1}><Redo2 size={15}/></button></div></header>
    <button class="program-monitor" onclick={seek} aria-label="Seek program monitor" style={`aspect-ratio:${document.preset.startsWith('portrait')?'9/16':document.preset.startsWith('square')?'1/1':'16/9'}`}>
      {#if activeAsset && activeVisual?.kind==='video'}<video bind:this={programVideo} src={activeAsset.proxy_url?assetProxyUrl(activeAsset.id):assetUrl(activeAsset.id)} playsinline preload="auto" onloadedmetadata={syncProgram}><track kind="captions"/></video>{:else if activeAsset}<img src={assetUrl(activeAsset.id)} alt="" style={`object-fit:${activeVisual?.transform.fit};opacity:${activeVisual?.transform.opacity};transform:scale(${activeVisual?.transform.scale}) rotate(${activeVisual?.transform.rotation}deg)`}/>{:else}<div class="empty-monitor"><Film size={30}/><span>Move the playhead over a visual clip</span></div>{/if}
      {#each activeText as clip}<div class="monitor-text" style={`left:${(clip.text?.position_x??.5)*100}%;top:${(clip.text?.position_y??.5)*100}%;color:${clip.text?.color};background:${clip.text?.background};font-size:${Math.max(12,(clip.text?.font_size??56)/3)}px;text-align:${clip.text?.align};font-family:${clip.text?.font_family}`}>{clip.text?.content}</div>{/each}
    </button>
    <div class="preview-audio" aria-hidden="true">{#each activeAudio as clip}{@const audioAsset=assets.find((asset)=>asset.id===clip.asset_id)}{#if audioAsset}<audio use:registerAudio={clip.id} src={assetUrl(audioAsset.id)} preload="auto"></audio>{/if}{/each}</div>
    <div class="transport"><button class="editor-icon" onclick={togglePlayback}>{#if playing}<Pause size={16}/>{:else}<Play size={16}/>{/if}</button><strong>{(playhead/1000).toFixed(2)}</strong><span>/ {(duration/1000).toFixed(2)} sec</span><button onclick={splitSelected} disabled={!selectedClip}><Scissors size={14}/>Split</button><button onclick={duplicateSelected} disabled={!selectedClip}><Copy size={14}/>Duplicate</button><button onclick={deleteSelected} disabled={!selected.size}><Trash2 size={14}/>Delete</button><label><input type="checkbox" bind:checked={ripple}/>Ripple delete</label></div>
    <section class="timeline"><header><div class="track-label"><span>TRACKS</span></div><button class="ruler" style={`width:${duration/1000*zoom}px`} onclick={seek} aria-label="Seek timeline">{#each Array(Math.ceil(duration/1000)+1).keys() as second}<i style={`left:${second*zoom}px`}><b>{second}s</b></i>{/each}<em style={`left:${playhead/1000*zoom}px`}></em></button></header><div class="timeline-body"><div class="track-heads">{#each [...document.tracks].sort((a,b)=>a.order-b.order) as track}<div><button class="editor-icon" onclick={()=>updateTrack(track.id,{locked:!track.locked})}>{#if track.locked}<Lock size={12}/>{:else}<Unlock size={12}/>{/if}</button><strong>{track.name}</strong><button class="editor-icon" onclick={()=>updateTrack(track.id,track.kind==='audio'?{muted:!track.muted}:{hidden:!track.hidden})}>{#if track.muted}<VolumeX size={12}/>{:else if track.hidden}<EyeOff size={12}/>{:else if track.kind==='audio'}<Volume2 size={12}/>{:else}<Eye size={12}/>{/if}</button><span><button onclick={()=>reorderTrack(track,-1)}><ChevronUp size={10}/></button><button onclick={()=>reorderTrack(track,1)}><ChevronDown size={10}/></button></span></div>{/each}</div><div class="track-lanes" style={`width:${duration/1000*zoom}px`}>{#each [...document.tracks].sort((a,b)=>a.order-b.order) as track}<div class="track-lane" class:hidden={track.hidden}>{#each document.clips.filter((clip)=>clip.track_id===track.id) as clip}<button class="timeline-clip {clip.kind}" class:selected={selected.has(clip.id)} style={`left:${clip.start_ms/1000*zoom}px;width:${Math.max(8,clip.duration_ms/1000*zoom)}px`} onpointerdown={(event)=>beginDrag(event,clip,'move')} onclick={(event)=>selectClip(event,clip)}><span role="separator" aria-label="Trim clip start" class="trim left" onpointerdown={(event)=>beginDrag(event,clip,'left')}></span><span>{clip.name}</span><small>{(clip.duration_ms/1000).toFixed(1)}s</small><span role="separator" aria-label="Trim clip end" class="trim right" onpointerdown={(event)=>beginDrag(event,clip,'right')}></span></button>{/each}{#each ghostClips.filter((clip)=>clip.track_id===track.id) as clip}<div class="timeline-clip ghost {clip.kind}" style={`left:${clip.start_ms/1000*zoom}px;width:${Math.max(8,clip.duration_ms/1000*zoom)}px`}><span>{clip.name}</span><small>PROPOSAL</small></div>{/each}</div>{/each}<em class="playhead" style={`left:${playhead/1000*zoom}px`}></em></div></div></section>
    <footer class="timeline-footer"><div><button onclick={()=>addTrack('visual')}>+ Video</button><button onclick={()=>addTrack('audio')}>+ Audio</button><button onclick={()=>addText('caption')}><Captions size={13}/>Caption</button>{#if captionJob&&['QUEUED','RUNNING'].includes(captionJob.status)}<button disabled><LoaderCircle class="spin" size={13}/>{captionJob.progress}%</button><button class="editor-danger" onclick={cancelCaptions}>Cancel</button>{:else if captionEstimate}<button onclick={transcribeCaptions} disabled={!captionEstimate.within_budget}>Confirm captions · ${(captionEstimate.estimated_cost_minor/100).toFixed(2)}</button>{:else}<button onclick={estimateCaptions}><WandSparkles size={13}/>Auto captions</button>{/if}</div><label><ZoomIn size={13}/><input type="range" min="16" max="300" bind:value={zoom} onchange={scheduleViewSave}/></label><div class="export-control"><select bind:value={exportPreset}><option value="landscape_720">Landscape 720p</option><option value="landscape_1080">Landscape 1080p</option><option value="portrait_720">Portrait 720p</option><option value="portrait_1080">Portrait 1080p</option><option value="square_720">Square 720p</option><option value="square_1080">Square 1080p</option></select>{#if render?.status==='RUNNING'||render?.status==='QUEUED'}<span>{render.progress}%</span><button class="editor-danger" onclick={cancelRender}>Cancel</button>{:else}<button class="editor-primary" onclick={startRender}><Download size={14}/>Export MP4</button>{/if}</div></footer>
  </main>
  <aside class="clip-inspector"><header><strong>Inspector</strong><small>{selectedClip?.kind??'No clip selected'}</small></header>{#if selectedClip}<section><label>Name<input value={selectedClip.name} onchange={(event)=>updateClip({name:(event.currentTarget as HTMLInputElement).value})}/></label><div class="field-pair"><label>Start (ms)<input type="number" value={selectedClip.start_ms} onchange={(event)=>updateClip({start_ms:snap(+event.currentTarget.value)})}/></label><label>Duration (ms)<input type="number" min="33" value={selectedClip.duration_ms} onchange={(event)=>updateClip({duration_ms:snap(+event.currentTarget.value)})}/></label></div>{#if ['video','image'].includes(selectedClip.kind)}<label>Fit<select value={selectedClip.transform.fit} onchange={(event)=>updateTransform('fit',event.currentTarget.value)}><option>fill</option><option>fit</option></select></label><label>Scale<input type="range" min="0.1" max="3" step="0.01" value={selectedClip.transform.scale} oninput={(event)=>updateTransform('scale',+event.currentTarget.value)}/></label><label>Opacity<input type="range" min="0" max="1" step="0.01" value={selectedClip.transform.opacity} oninput={(event)=>updateTransform('opacity',+event.currentTarget.value)}/></label><label>Rotation<input type="number" value={selectedClip.transform.rotation} onchange={(event)=>updateTransform('rotation',+event.currentTarget.value)}/></label><label><input type="checkbox" checked={selectedClip.transform.ken_burns} onchange={(event)=>updateTransform('ken_burns',event.currentTarget.checked)}/>Ken Burns motion</label>{/if}{#if selectedClip.kind==='audio'||selectedClip.kind==='video'}<label>Gain (dB)<input type="range" min="-60" max="24" value={selectedClip.audio.gain_db} oninput={(event)=>updateAudio('gain_db',+event.currentTarget.value)}/></label><div class="field-pair"><label>Fade in<input type="number" value={selectedClip.audio.fade_in_ms} onchange={(event)=>updateAudio('fade_in_ms',+event.currentTarget.value)}/></label><label>Fade out<input type="number" value={selectedClip.audio.fade_out_ms} onchange={(event)=>updateAudio('fade_out_ms',+event.currentTarget.value)}/></label></div>{/if}{#if selectedClip.text}<label>Text<textarea value={selectedClip.text.content} oninput={(event)=>updateText('content',event.currentTarget.value)}></textarea></label><label>Font<select value={selectedClip.text.font_family} onchange={(event)=>updateText('font_family',event.currentTarget.value)}><option>Noto Sans</option><option>Noto Sans Arabic</option><option>Noto Serif</option></select></label><div class="field-pair"><label>Size<input type="number" value={selectedClip.text.font_size} onchange={(event)=>updateText('font_size',+event.currentTarget.value)}/></label><label>Color<input type="color" value={selectedClip.text.color} onchange={(event)=>updateText('color',event.currentTarget.value)}/></label></div>{/if}<label>Transition<select value={selectedClip.transition_in} onchange={(event)=>updateClip({transition_in:event.currentTarget.value as SequenceClip['transition_in']})}><option value="cut">Hard cut</option><option value="dissolve">Cross dissolve</option><option value="fade_black">Fade through black</option></select></label><label>Transition ms<input type="number" min="0" max={selectedClip.duration_ms/2} value={selectedClip.transition_duration_ms} onchange={(event)=>updateClip({transition_duration_ms:+event.currentTarget.value})}/></label></section>{:else}<div class="empty-inspector"><AlignCenter size={22}/><p>Select a clip to edit timing, transforms, audio, text, and transitions.</p></div>{/if}
    {#if captionJob?.status==='SUCCEEDED'}<div class="caption-review"><strong>Review caption cues</strong>{#each captionJob.cues as cue}<label>{(cue.start_ms/1000).toFixed(1)}s<input bind:value={cue.text}/></label>{/each}<button class="editor-primary" onclick={applyCaptions}>Apply editable cues</button></div>{/if}
    {#if render}<div class="render-card" data-status={render.status}><strong>{render.status==='SUCCEEDED'?'Export ready':render.status==='FAILED'?'Export failed':render.status==='CANCELLED'?'Export cancelled':'Rendering sequence'}</strong><small>{render.error??`${render.progress}% · H.264 / AAC`}</small>{#if ['FAILED','CANCELLED'].includes(render.status)}<button class="editor-primary" onclick={retryRender}>Retry from this revision</button>{/if}{#if render.output_asset_id}<video src={assetUrl(render.output_asset_id)} controls><track kind="captions"/></video><button onclick={()=>nativeSave(render!.output_asset_id!)}><Download size={13}/>Save As…</button>{/if}</div>{/if}
  </aside>
</div>
{/if}
{#if error}<div class="editor-toast error">{error}<button onclick={()=>error=''}>×</button></div>{/if}{#if notice}<div class="editor-toast">{notice}<button onclick={()=>notice=''}>×</button></div>{/if}
