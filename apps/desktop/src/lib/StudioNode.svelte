<script lang="ts">
  import { Handle, Position, type NodeProps } from '@xyflow/svelte';
  import { Bot, Check, CircleDashed, FileOutput, Film, Image, Layers3, Music2, ScanSearch, Sparkles, Type, Volume2 } from 'lucide-svelte';

  let { data, selected }: NodeProps = $props();
  const icons: Record<string, typeof Type> = {
    creative_brief: Type, reference_asset: Image, look_director: Bot, image_generation: Sparkles,
    asset_selector: Layers3, video_generation: Film, narration: Volume2, music: Music2,
    composition: Film, verification: ScanSearch, export: FileOutput, note: Type, group: Layers3
  };
  const tones: Record<string, string> = { Input: 'blue', Agent: 'violet', Generate: 'rose', Control: 'amber', Audio: 'green', Finish: 'cyan', Organize: 'slate' };
  let Icon = $derived(icons[String(data.type)] ?? CircleDashed);
  let StatusIcon = $derived(data.status === 'SUCCEEDED' || data.status === 'CACHED' ? Check : data.status === 'RUNNING' ? Sparkles : CircleDashed);
  let config = $derived((data.config ?? {}) as Record<string, unknown>);
  let previewUrls = $derived((data.previewUrls ?? []) as string[]);
  let category = $derived(['creative_brief', 'reference_asset'].includes(String(data.type)) ? 'Input' : String(data.type) === 'look_director' ? 'Agent' : ['image_generation', 'video_generation'].includes(String(data.type)) ? 'Generate' : String(data.type) === 'asset_selector' ? 'Control' : ['narration', 'music'].includes(String(data.type)) ? 'Audio' : ['note', 'group'].includes(String(data.type)) ? 'Organize' : 'Finish');
</script>

<article class:node-selected={selected} class="studio-node" data-tone={tones[category]} data-status={data.status}>
  {#each (data.inputs as string[]) ?? [] as input, index}
    <Handle type="target" position={Position.Left} id={input} style={`top:${45 + index * 22}px`} />
  {/each}
  <header>
    <span class="node-icon"><Icon size={15} strokeWidth={2.2} /></span>
    <div><strong>{data.label}</strong><small>{category}</small></div>
    <span class="node-state" title={String(data.status)}><StatusIcon size={13} /></span>
  </header>
  <div class="node-body">
    {#if data.type === 'creative_brief'}<p>{String(config.text ?? 'Add creative direction')}</p>
    {:else if data.type === 'image_generation' && previewUrls.length}<div class="node-previews">{#each previewUrls.slice(0,4) as url}<img src={url} alt="Generated variant" />{/each}</div><p>{previewUrls.length} saved variant{previewUrls.length === 1 ? '' : 's'} · {String(config.vendor ?? 'Auto')}</p>
    {:else if data.type === 'image_generation' || data.type === 'video_generation'}<p>{String(config.vendor ?? 'Auto')} · {Number(config.variants ?? 1)} variant{Number(config.variants ?? 1) === 1 ? '' : 's'}</p>
    {:else if data.type === 'asset_selector'}<p>Variant {Number(config.selected_index ?? 0) + 1} pinned</p>
    {:else}<p>{String(config.prompt_guidance ?? 'Ready for direction')}</p>{/if}
  </div>
  <footer><span>{String(data.status ?? 'IDLE').replace('_', ' ')}</span><span>{((data.outputs as string[]) ?? []).join(' · ') || 'canvas'}</span></footer>
  {#each (data.outputs as string[]) ?? [] as output, index}
    <Handle type="source" position={Position.Right} id={output} style={`top:${45 + index * 22}px`} />
  {/each}
</article>
