<script lang="ts">
  import { onMount } from 'svelte';
  import { invoke } from '@tauri-apps/api/core';
  import { SvelteSet } from 'svelte/reactivity';
  import { addEdge, Background, BackgroundVariant, Controls, MiniMap, SvelteFlow, SvelteFlowProvider, type Connection, type Edge, type Node } from '@xyflow/svelte';
  import { Bot, Check, ChevronDown, CircleDollarSign, CloudOff, Command, Film, FolderOpen, History, ImagePlus, KeyRound, LoaderCircle, PanelRightClose, Play, Plus, Redo2, Save, Settings, Sparkles, Trash2, Undo2, X } from 'lucide-svelte';
  import StudioNode from '$lib/StudioNode.svelte';
  import EditorWorkspace from '$lib/EditorWorkspace.svelte';
  import { api, ApiError, assetUrl, configureApi, studioEvents } from '$lib/api';
  import { fromFlow, operationClosure, PORTS, toFlowEdges, toFlowNodes, validateConnection } from '$lib/graph';
  import type { CatalogNode, Estimate, Execution, NodeStatus, Project, Proposal, ProviderConnection, ProviderMatrix, Revision, StorageConnection, StudioAsset, StudioEvent, WorkflowNode } from '$lib/types';

  let online = false, loading = true, saving = false, running = false, inspectorOpen = true;
  type RuntimeInfo = { apiBaseUrl:string; status:string; managed:boolean; logPath:string; diagnostic:string };
  let runtimeInfo: RuntimeInfo | null = null;
  let workspaceMode: 'generate' | 'edit' = 'generate';
  let error = '', notice = '';
  let project: Project | null = null;
  let catalog: CatalogNode[] = [];
  let providerMatrix: ProviderMatrix = { chat: [], image: [], video: [], tts: [], music: [] };
  let nodes: Node[] = [], edges: Edge[] = [];
  let selectedNodeId: string | null = null;
  let agentPrompt = '', proposal: Proposal | null = null, selectedOperations = new SvelteSet<string>();
  let estimate: Estimate | null = null, resumeEstimate: Estimate | null = null, execution: Execution | null = null;
  let activity: StudioEvent[] = [], projectAssets: StudioAsset[] = [], variantAssets: StudioAsset[] = [], showVariants = false, showOutput = false, showLibrary = false;
  let showSettings = false, projects: Project[] = [], connections: ProviderConnection[] = [];
  let newProjectName = '', providerVendor = 'openai', providerSecret = '';
  let storageConnection: StorageConnection | null = null;
  let b2Region = '', b2KeyId = '', b2ApplicationKey = '', b2BucketName = '';
  let annotationAsset: StudioAsset | null = null, annotationBody = '', annotationKind: 'point' | 'rectangle' = 'point';
  let annotationGeometry: Record<string, number> | null = null;
  let history: { nodes: Node[]; edges: Edge[] }[] = [], historyIndex = -1;
  let fileInput: HTMLInputElement;
  let eventSource: EventSource | null = null;
  const nodeTypes = { studio: StudioNode };

  function isTauri() { return '__TAURI_INTERNALS__' in window; }

  async function waitForRuntime(): Promise<RuntimeInfo> {
    if (!isTauri()) {
      const development = { apiBaseUrl:'http://127.0.0.1:43192', status:'development', managed:false, logPath:'', diagnostic:'' };
      configureApi(development.apiBaseUrl);
      return development;
    }
    const deadline = Date.now() + 45_000;
    let current = await invoke<RuntimeInfo>('desktop_runtime_info');
    while (!['ready', 'failed'].includes(current.status) && Date.now() < deadline) {
      runtimeInfo = current;
      await new Promise((resolve) => setTimeout(resolve, 500));
      current = await invoke<RuntimeInfo>('desktop_runtime_info');
    }
    if (current.status !== 'ready') throw new Error(current.diagnostic || 'The embedded creative engine did not become ready.');
    configureApi(current.apiBaseUrl);
    return current;
  }

  async function startStudio() {
    loading = true; online = false; error = '';
    try { runtimeInfo = await waitForRuntime(); await bootstrap(); }
    catch (cause) { loading = false; online = false; error = cause instanceof Error ? cause.message : 'The creative engine could not start.'; }
  }

  async function restartEngine() {
    loading = true; error = '';
    try { runtimeInfo = await invoke<RuntimeInfo>('restart_desktop_runtime'); await startStudio(); }
    catch (cause) { loading = false; error = cause instanceof Error ? cause.message : 'The creative engine could not restart.'; }
  }

  async function copyDiagnostic() {
    const diagnostic = `Thikra Studio 0.1.1\nStatus: ${runtimeInfo?.status ?? 'unknown'}\nEngine: ${runtimeInfo?.apiBaseUrl ?? 'unavailable'}\nLogs: ${runtimeInfo?.logPath ?? 'unavailable'}\n${runtimeInfo?.diagnostic || error}`;
    await navigator.clipboard.writeText(diagnostic);
    notice = 'Diagnostic copied';
  }

  $: selectedNode = nodes.find((item) => item.id === selectedNodeId) ?? null;
  $: budgetPercent = project ? Math.min(100, Math.round((project.spent_minor / Math.max(1, project.budget_cap_minor)) * 100)) : 0;
  $: categories = catalog.map((item) => item.category).filter((item, index, all) => all.indexOf(item) === index);
  $: selectedSlot = selectedNode ? slotForNode(String(selectedNode.data.type)) : null;
  $: configuredProviders = selectedSlot ? (providerMatrix[selectedSlot] ?? []).filter((item) => item.key_available) : [];
  $: configuredVendor = selectedNode ? String((selectedNode.data.config as Record<string, unknown>).vendor ?? '') : '';
  $: configuredProvider = configuredProviders.find((item) => item.vendor === configuredVendor) ?? null;
  $: configuredModel = selectedNode ? String((selectedNode.data.config as Record<string, unknown>).model ?? '') : '';
  $: latestActivity = activity.at(-1) ?? null;
  $: finalVideoAsset = projectAssets.find((asset) => asset.content_type.startsWith('video/') && asset.name.startsWith('compose output')) ?? projectAssets.find((asset) => asset.content_type.startsWith('video/')) ?? null;
  $: canResume = !running && !!execution && ['FAILED', 'CANCELLED'].includes(execution.status);

  function slotForNode(type: string): keyof ProviderMatrix | null {
    return type === 'image_generation' ? 'image' : type === 'video_generation' ? 'video' : type === 'narration' ? 'tts' : type === 'music' ? 'music' : null;
  }

  async function refreshNodeCatalog() {
    const response = await api<{ nodes: CatalogNode[]; providers: ProviderMatrix }>('/studio/node-catalog');
    catalog = response.nodes; providerMatrix = response.providers;
  }

  function cloneState() { return { nodes: structuredClone(nodes), edges: structuredClone(edges) }; }
  function remember() { history = [...history.slice(0, historyIndex + 1), cloneState()].slice(-30); historyIndex = history.length - 1; estimate = null; }
  function undo() { if (historyIndex > 0) { historyIndex -= 1; ({ nodes, edges } = structuredClone(history[historyIndex])); } }
  function redo() { if (historyIndex < history.length - 1) { historyIndex += 1; ({ nodes, edges } = structuredClone(history[historyIndex])); } }

  async function bootstrap() {
    loading = true; error = '';
    try {
      await refreshNodeCatalog(); online = true;
      const response = await api<{ items: Project[] }>('/studio/projects'); projects = response.items;
      if (projects.length) await loadProject(projects[0].id, true);
      else {
        const created = await api<Project>('/studio/projects', { method: 'POST', body: JSON.stringify({ name: 'Untitled creative', description: 'Your local visual workflow', budget_cap_minor: 500, currency: 'USD' }) });
        applyProject(created); await loadProjectAssets(created.id);
      }
    } catch (cause) { online = false; error = cause instanceof Error ? cause.message : 'The local Studio API is unavailable.'; }
    finally { loading = false; }
  }

  async function loadProject(id: string, revealOutput = false) { applyProject(await api<Project>(`/studio/projects/${id}`)); await loadProjectAssets(id, revealOutput); }
  async function loadProjectAssets(id: string, revealOutput = false) {
    const response = await api<{ items: StudioAsset[]; latest_image_variants: StudioAsset[] }>(`/studio/projects/${id}/assets`);
    projectAssets = response.items;
    variantAssets = response.latest_image_variants;
    const previewUrls = variantAssets.map((asset) => assetUrl(asset.id));
    nodes = nodes.map((node) => node.data.type === 'image_generation' ? { ...node, data: { ...node.data, previewUrls } } : node);
    if (revealOutput && response.items.some((asset) => asset.content_type.startsWith('video/') && asset.name.startsWith('compose output'))) showOutput = true;
  }
  async function refreshProjects() { projects = (await api<{ items: Project[] }>('/studio/projects')).items; }
  async function createStudioProject() {
    if (!newProjectName.trim()) return;
    const created = await api<Project>('/studio/projects', { method: 'POST', body: JSON.stringify({ name: newProjectName.trim(), description: 'Local creative workflow', budget_cap_minor: 500, currency: 'USD' }) });
    newProjectName = ''; await refreshProjects(); applyProject(created); await loadProjectAssets(created.id); showSettings = false;
  }
  async function renameProject(name: string) {
    if (!project || name.trim().length < 2 || name.trim() === project.name) return;
    applyProject(await api<Project>(`/studio/projects/${project.id}`, { method: 'PATCH', body: JSON.stringify({ name: name.trim() }) })); await refreshProjects();
  }
  async function removeProject(item: Project) {
    if (!confirm(`Delete “${item.name}” and its local Studio files? This cannot be undone.`)) return;
    await api(`/studio/projects/${item.id}`, { method: 'DELETE' }); await refreshProjects();
    if (project?.id === item.id) { if (projects.length) await loadProject(projects[0].id); else { newProjectName = 'Untitled creative'; await createStudioProject(); } }
  }
  async function openSettings() {
    [connections, storageConnection] = await Promise.all([
      api<{ items: ProviderConnection[] }>('/studio/provider-connections').then((value)=>value.items),
      api<StorageConnection>('/studio/storage-connection')
    ]);
    b2Region = storageConnection.region; b2BucketName = storageConnection.bucket_name;
    showSettings = true;
  }
  async function saveProviderSecret() {
    if (!providerSecret.trim()) return;
    await api(`/studio/provider-connections/${providerVendor}`, { method: 'PUT', body: JSON.stringify({ secret: providerSecret }) }); providerSecret = ''; await refreshNodeCatalog(); await openSettings(); notice = 'Personal provider key stored in Windows Credential Manager';
  }
  async function clearProviderSecret(vendor: string) { await api(`/studio/provider-connections/${vendor}`, { method: 'DELETE' }); await refreshNodeCatalog(); await openSettings(); }
  async function saveStorageConnection() {
    storageConnection = await api<StorageConnection>('/studio/storage-connection', { method:'PUT', body:JSON.stringify({region:b2Region,key_id:b2KeyId,application_key:b2ApplicationKey,bucket_name:b2BucketName}) });
    b2KeyId = ''; b2ApplicationKey = ''; notice = 'B2 connection stored in Windows Credential Manager';
  }
  async function clearStorageConnection() {
    storageConnection = await api<StorageConnection>('/studio/storage-connection', {method:'DELETE'});
    b2Region = ''; b2KeyId = ''; b2ApplicationKey = ''; b2BucketName = ''; notice = 'Studio returned to local-only storage';
  }
  function applyProject(value: Project) {
    project = value; nodes = toFlowNodes(value.revision.graph, value.layout); edges = toFlowEdges(value.revision.graph.edges);
    history = [cloneState()]; historyIndex = 0; selectedNodeId = null; proposal = null; estimate = null; resumeEstimate = null;
  }

  function addNode(item: CatalogNode) {
    const id = `${item.type}-${crypto.randomUUID().slice(0, 8)}`;
    const slot = slotForNode(item.type); const firstProvider = slot ? providerMatrix[slot].find((provider) => provider.key_available) : null;
    const config: Record<string, unknown> = item.type.includes('generation') ? { variants: 1 } : {};
    if (firstProvider) { config.vendor = firstProvider.vendor; config.model = firstProvider.default_model; }
    const semantic: WorkflowNode = { id, type: item.type, label: item.label, config };
    nodes = [...nodes, { id, type: 'studio', position: { x: 220 + nodes.length * 24, y: 150 + nodes.length * 18 }, data: { ...semantic, inputs: PORTS[item.type].inputs, outputs: PORTS[item.type].outputs, status: 'IDLE' } }];
    selectedNodeId = id; showLibrary = false; remember();
  }

  function connect(connection: Connection) {
    if (!connection.sourceHandle || !connection.targetHandle) return;
    if (!validateConnection(connection, nodes, edges)) { error = 'That connection is incompatible or would create a cycle.'; return; }
    edges = addEdge({ ...connection, id: `edge-${crypto.randomUUID().slice(0, 8)}` }, edges); remember();
  }

  function selectNode(event: { node: Node }) { selectedNodeId = event.node.id; inspectorOpen = true; }
  function updateSelected(field: string, value: unknown) {
    if (!selectedNodeId) return;
    nodes = nodes.map((item) => item.id === selectedNodeId ? { ...item, data: { ...item.data, config: { ...(item.data.config as Record<string, unknown>), [field]: value } } } : item);
    remember();
  }
  function updateProvider(vendor: string) {
    if (!selectedNodeId || !selectedSlot) return;
    const provider = providerMatrix[selectedSlot].find((item) => item.vendor === vendor && item.key_available); if (!provider) return;
    nodes = nodes.map((item) => item.id === selectedNodeId ? { ...item, data: { ...item.data, config: { ...(item.data.config as Record<string, unknown>), vendor, model: provider.default_model, ...(item.data.type === 'video_generation' && provider.duration_grid?.length ? { duration_sec: provider.duration_grid[0] } : {}) } } } : item);
    remember();
  }
  function removeSelected() {
    if (!selectedNodeId) return;
    nodes = nodes.filter((item) => item.id !== selectedNodeId); edges = edges.filter((item) => item.source !== selectedNodeId && item.target !== selectedNodeId); selectedNodeId = null; remember();
  }

  async function saveRevision(summary = 'Manual canvas edit'): Promise<Revision | null> {
    if (!project) return null; saving = true; error = '';
    try {
      const revision = await api<Revision>(`/studio/projects/${project.id}/revisions`, { method: 'POST', body: JSON.stringify({ base_revision_id: project.current_revision_id, graph: fromFlow(nodes, edges), summary }) });
      await saveLayout(); await loadProject(project.id); notice = `Saved revision ${revision.number}`; return revision;
    } catch (cause) { error = cause instanceof ApiError && cause.code === 'STALE_REVISION' ? 'This workflow changed. Reload it before saving.' : cause instanceof Error ? cause.message : 'Save failed'; return null; }
    finally { saving = false; }
  }

  async function saveLayout() {
    if (!project) return;
    const positions = Object.fromEntries(nodes.map((node) => [node.id, node.position]));
    await api(`/studio/projects/${project.id}/layout`, { method: 'PATCH', body: JSON.stringify({ positions, viewport: project.viewport ?? { x: 0, y: 0, zoom: 1 } }) });
  }

  async function askAgent() {
    if (!project || !agentPrompt.trim()) return; error = '';
    try {
      const assetIds = [...new Set([...nodes.map((item) => String((item.data.config as Record<string, unknown>).asset_id ?? '')).filter(Boolean), ...variantAssets.map((item) => item.id)])].slice(0, 4);
      proposal = await api<Proposal>(`/studio/projects/${project.id}/agent-proposals`, { method: 'POST', body: JSON.stringify({ base_revision_id: project.current_revision_id, prompt: agentPrompt, selected_node_ids: selectedNodeId ? [selectedNodeId] : [], asset_ids: assetIds }) });
      selectedOperations = new SvelteSet(proposal.operations.map((item) => item.id));
    } catch (cause) { error = cause instanceof Error ? cause.message : 'The agent could not prepare a proposal'; }
  }

  function toggleOperation(id: string) {
    if (!proposal) return;
    const next = new SvelteSet(selectedOperations);
    if (next.has(id)) next.delete(id); else next.add(id);
    selectedOperations = new SvelteSet(operationClosure([...next], proposal.operations));
  }

  async function applyAgentProposal() {
    if (!project || !proposal || !selectedOperations.size) return;
    try {
      await api(`/studio/projects/${project.id}/agent-proposals/${proposal.id}/apply`, { method: 'POST', body: JSON.stringify({ base_revision_id: project.current_revision_id, operation_ids: [...selectedOperations] }) });
      agentPrompt = ''; proposal = null; await loadProject(project.id); notice = 'Agent changes applied as a new revision';
    } catch (cause) { error = cause instanceof Error ? cause.message : 'Proposal could not be applied'; }
  }

  async function getEstimate() {
    if (!project) return;
    estimate = await api<Estimate>(`/studio/projects/${project.id}/estimate`, { method: 'POST', body: JSON.stringify({ revision_id: project.current_revision_id, target_node_ids: [], force_rerun: false }) });
  }

  async function runWorkflow() {
    if (!project || !estimate?.within_budget) return; running = true; activity = []; variantAssets = [];
    try {
      execution = await api<Execution>(`/studio/projects/${project.id}/executions`, { method: 'POST', body: JSON.stringify({ revision_id: estimate.revision_id, estimate_hash: estimate.estimate_hash, target_node_ids: estimate.target_node_ids, force_rerun: false }) });
      subscribe(execution.id);
    } catch (cause) { running = false; error = cause instanceof Error ? cause.message : 'Execution failed to start'; }
  }

  async function reviewResume() {
    if (!execution) return;
    error = '';
    try {
      resumeEstimate = await api<Estimate>(`/studio/executions/${execution.id}/resume-estimate`, { method: 'POST' });
    } catch (cause) { error = cause instanceof Error ? cause.message : 'Could not calculate the remaining cost'; }
  }

  async function resumeWorkflow() {
    if (!execution || !resumeEstimate?.within_budget) return;
    const failedExecutionId = execution.id; running = true; activity = []; variantAssets = []; error = '';
    try {
      execution = await api<Execution>(`/studio/executions/${failedExecutionId}/resume`, { method: 'POST', body: JSON.stringify({ estimate_hash: resumeEstimate.estimate_hash }) });
      resumeEstimate = null; subscribe(execution.id);
    } catch (cause) { running = false; error = cause instanceof Error ? cause.message : 'Resume failed to start'; }
  }

  function subscribe(id: string) {
    eventSource?.close();
    eventSource = studioEvents(id, async (message) => {
      const event = JSON.parse(message.data) as StudioEvent; activity = [...activity.filter((item) => item.eventId !== event.eventId), event].slice(-12);
      const statuses: Record<string, NodeStatus> = {}; for (const item of activity) if (item.nodeId) statuses[item.nodeId] = item.type.endsWith('started') || item.type.endsWith('progress') || item.type.endsWith('heartbeat') ? 'RUNNING' : item.type.endsWith('cached') || item.type.endsWith('recovered') ? 'CACHED' : item.type.endsWith('succeeded') ? 'SUCCEEDED' : item.type.endsWith('failed') ? 'FAILED' : item.type.endsWith('blocked') ? 'BLOCKED' : 'IDLE';
      if (project) nodes = toFlowNodes(fromFlow(nodes, edges), Object.fromEntries(nodes.map((node) => [node.id, node.position])), statuses);
      if (event.type.startsWith('execution.') && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(event.status)) {
        eventSource?.close(); running = false; execution = await api<Execution>(`/studio/executions/${id}`);
        if (execution.status === 'FAILED') {
          const failedNode = execution.nodes.find((node) => node.status === 'FAILED');
          error = failedNode ? `${failedNode.node_type.replaceAll('_',' ')} failed: ${failedNode.error ?? 'No provider detail was returned.'}` : execution.failure_reason ?? 'Workflow failed.';
        }
        variantAssets = execution.nodes.flatMap((node) => node.output.assets ?? []).filter((asset) => asset.content_type.startsWith('image/'));
        if (project) await loadProject(project.id);
        if (execution.status === 'SUCCEEDED' && finalVideoAsset) showOutput = true;
        else if (variantAssets.length) showVariants = true;
      }
    }, () => { if (running) notice = 'Reconnecting to execution stream…'; });
  }

  async function cancelRun() { if (execution) await api(`/studio/executions/${execution.id}/cancel`, { method: 'POST' }); }
  async function pinVariant(index: number) {
    if (!project) return;
    const selector = nodes.find((item) => item.data.type === 'asset_selector');
    const video = nodes.find((item) => item.data.type === 'video_generation');
    if (!selector || !video) { error = 'Add an Asset Selector and Video Generation node before animating a look.'; return; }
    const videoConfig = video.data.config as Record<string, unknown>;
    const provider = providerMatrix.video.find((item) => item.vendor === String(videoConfig.vendor ?? ''));
    const duration = Number(videoConfig.duration_sec ?? 0);
    const normalizedDuration = provider?.duration_grid?.length && !provider.duration_grid.includes(duration) ? provider.duration_grid[0] : duration;
    nodes = nodes.map((item) => item.id === selector.id ? { ...item, data: { ...item.data, config: { ...(item.data.config as Record<string, unknown>), selected_index: index } } } : item.id === video.id && normalizedDuration !== duration ? { ...item, data: { ...item.data, config: { ...videoConfig, duration_sec: normalizedDuration } } } : item);
    remember(); showVariants = false;
    const revision = await saveRevision(`Pin image variant ${index + 1} for animation`);
    if (!revision) return;
    execution = null; resumeEstimate = null;
    estimate = await api<Estimate>(`/studio/projects/${project.id}/estimate`, { method: 'POST', body: JSON.stringify({ revision_id: revision.id, target_node_ids: [video.id], force_rerun: false }) });
    notice = `Variant ${index + 1} pinned. Review the animation cost, then confirm the targeted run.`;
  }
  async function importReference(event: Event) {
    if (!project) return; const input = event.currentTarget as HTMLInputElement; const file = input.files?.[0]; if (!file) return;
    const form = new FormData(); form.set('upload', file); const asset = await api<StudioAsset>(`/studio/projects/${project.id}/assets`, { method: 'POST', body: form });
    const item = catalog.find((entry) => entry.type === 'reference_asset'); if (item) { addNode(item); updateSelected('asset_id', asset.id); updateSelected('name', asset.name); }
    input.value = '';
  }

  function placeAnnotation(event: MouseEvent, asset: StudioAsset) {
    const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
    const y = Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height));
    annotationAsset = asset;
    annotationGeometry = annotationKind === 'point' ? { x, y } : { x: Math.max(0, x - .075), y: Math.max(0, y - .075), width: Math.min(.15, 1 - x + .075), height: Math.min(.15, 1 - y + .075) };
  }
  async function saveAnnotation() {
    if (!project || !annotationAsset || !annotationGeometry || !annotationBody.trim()) return;
    await api(`/studio/projects/${project.id}/annotations`, { method: 'POST', body: JSON.stringify({ asset_id: annotationAsset.id, kind: annotationKind, geometry: annotationGeometry, body: annotationBody.trim() }) });
    annotationBody = ''; annotationGeometry = null; notice = 'Feedback saved and available to the agent';
  }

  onMount(() => { startStudio(); return () => eventSource?.close(); });
</script>

<svelte:head><title>Thikra Studio</title></svelte:head>

{#if loading}
  <main class="connection-screen"><LoaderCircle class="spin" size={28} /><h1>Opening Thikra Studio</h1><p>Connecting to the local creative runtime…</p></main>
{:else if !online}
  <main class="connection-screen"><CloudOff size={34} /><h1>Creative engine needs attention</h1><p>{error}</p>{#if runtimeInfo?.logPath}<code>{runtimeInfo.logPath}</code>{/if}<div class="startup-actions"><button class="primary" onclick={restartEngine}>Restart engine</button>{#if isTauri()}<button class="subtle" onclick={()=>invoke('open_runtime_logs')}>Open logs</button><button class="subtle" onclick={copyDiagnostic}>Copy diagnostic</button>{:else}<button class="primary" onclick={startStudio}>Try again</button>{/if}</div></main>
{:else}
  <div class="studio-shell">
    <header class="titlebar">
      <div class="brand"><span class="brand-mark"><Sparkles size={16} /></span><strong>Thikra Studio</strong><em>LOCAL</em></div>
      <button class="project-switch" onclick={openSettings}><FolderOpen size={15} /><span>{project?.name}</span><ChevronDown size={13} /></button>
      <nav class="workspace-tabs" aria-label="Workspace"><button class:active={workspaceMode==='generate'} onclick={()=>workspaceMode='generate'}>Generate</button><button class:active={workspaceMode==='edit'} onclick={()=>workspaceMode='edit'}>Edit</button></nav>
      <div class="title-actions">
        <button class="icon-btn" title="Undo" onclick={undo} disabled={historyIndex <= 0}><Undo2 size={16} /></button>
        <button class="icon-btn" title="Redo" onclick={redo} disabled={historyIndex >= history.length - 1}><Redo2 size={16} /></button>
        <button class="subtle" onclick={() => saveRevision()} disabled={saving}>{#if saving}<LoaderCircle class="spin" size={15} />{:else}<Save size={15} />{/if}Save revision</button>
        <button class="icon-btn" title="Toggle inspector" onclick={() => inspectorOpen = !inspectorOpen}><PanelRightClose size={16} /></button>
      </div>
    </header>

    {#if workspaceMode === 'edit' && project}<EditorWorkspace projectId={project.id} projectName={project.name} {providerMatrix} onAssetsChanged={()=>loadProjectAssets(project!.id)} />{/if}

    <aside class="left-rail">
      <button class="new-node" onclick={() => showLibrary = !showLibrary}><Plus size={16} /> Add node</button>
      <button onclick={() => fileInput.click()}><ImagePlus size={16} /> Reference</button>
      {#if variantAssets.length}<button onclick={() => showVariants = true}><ImagePlus size={16} /> Generated looks <b>{variantAssets.length}</b></button>{/if}
      {#if finalVideoAsset}<button class="result-button" onclick={() => showOutput = true}><Film size={16} /> Final video <b>READY</b></button>{/if}
      <button onclick={openSettings}><Settings size={16} /> Workspace settings</button>
      <input bind:this={fileInput} class="hidden-input" type="file" accept="image/png,image/jpeg,image/webp" onchange={importReference} />
      <nav>
        <span>Project</span>
        <button class:active={!selectedNodeId}><Command size={15} /> Workflow</button>
        <button><History size={15} /> Revisions <b>{project?.current_revision_number}</b></button>
        <button><CircleDollarSign size={15} /> Budget <b>{project?.currency} {((project?.remaining_minor ?? 0) / 100).toFixed(2)}</b></button>
        <span>Recent activity</span>
        {#each activity.slice(-5).reverse() as event}<button class="activity-row"><i data-kind={event.type.split('.')[1]}></i><small>{event.message}</small></button>{/each}
      </nav>
      <div class="budget-card"><div><span>Local project cap</span><strong>{project?.currency} {((project?.budget_cap_minor ?? 0) / 100).toFixed(2)}</strong></div><div class="meter"><i style={`width:${budgetPercent}%`}></i></div><small>{project?.currency} {((project?.spent_minor ?? 0) / 100).toFixed(2)} estimated usage</small></div>
    </aside>

    <section class="canvas-stage">
      {#if showLibrary}
        <div class="node-library"><header><div><strong>Add a capability</strong><small>Curated, typed workflow nodes</small></div><button class="icon-btn" onclick={() => showLibrary = false}><X size={15} /></button></header>
          {#each categories as category}<h3>{category}</h3><div class="library-grid">{#each catalog.filter((item) => item.category === category) as item}<button onclick={() => addNode(item)}><span>{item.label}</span><small>{item.description}</small></button>{/each}</div>{/each}
        </div>
      {/if}
      <SvelteFlowProvider>
        <SvelteFlow bind:nodes bind:edges {nodeTypes} fitView minZoom={0.2} maxZoom={1.8} snapGrid={[16,16]} isValidConnection={(connection)=>validateConnection(connection,nodes,edges)} onconnect={connect} onnodeclick={selectNode}>
          <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} patternColor="#323744" />
          <Controls position="bottom-left" />
          <MiniMap position="bottom-right" pannable zoomable nodeColor="#5b6072" maskColor="rgba(8,10,15,.72)" />
        </SvelteFlow>
      </SvelteFlowProvider>
      <div class="canvas-hint"><Command size={13} /> Drag to move · scroll to zoom · connect matching ports</div>
    </section>

    {#if inspectorOpen}<aside class="right-panel">
      <div class="panel-tabs"><button class:active={!!selectedNode}>Inspector</button><button class:active={!selectedNode}><Bot size={14} /> Agent</button></div>
      {#if selectedNode}
        <section class="inspector"><div class="eyebrow">{String(selectedNode.data.type).replaceAll('_',' ')}</div><input class="node-title" value={String(selectedNode.data.label)} onchange={(event) => { const label=(event.currentTarget as HTMLInputElement).value; nodes=nodes.map((item)=>item.id===selectedNodeId?{...item,data:{...item.data,label}}:item); remember(); }} />
          <p class="description">Tune the semantic inputs. Moving the card only changes layout; these fields create a new executable revision.</p>
          {#if selectedNode.data.type === 'creative_brief'}<label>Creative direction<textarea value={String((selectedNode.data.config as Record<string,unknown>).text ?? '')} oninput={(event)=>updateSelected('text',(event.currentTarget as HTMLTextAreaElement).value)}></textarea></label>{/if}
          {#if selectedNode.data.type === 'image_generation' || selectedNode.data.type === 'video_generation'}
            <label>Prompt guidance<textarea value={String((selectedNode.data.config as Record<string,unknown>).prompt_guidance ?? '')} oninput={(event)=>updateSelected('prompt_guidance',(event.currentTarget as HTMLTextAreaElement).value)}></textarea></label>
            <label>Variants <input type="range" min="1" max="4" value={Number((selectedNode.data.config as Record<string,unknown>).variants ?? 1)} oninput={(event)=>updateSelected('variants',Number((event.currentTarget as HTMLInputElement).value))} /><span class="range-value">{Number((selectedNode.data.config as Record<string,unknown>).variants ?? 1)}</span></label>
          {/if}
          {#if selectedNode.data.type === 'narration'}<label>Narration text<textarea value={String((selectedNode.data.config as Record<string,unknown>).text ?? '')} oninput={(event)=>updateSelected('text',(event.currentTarget as HTMLTextAreaElement).value)}></textarea></label>{/if}
          {#if selectedNode.data.type === 'music'}<label>Music direction<textarea value={String((selectedNode.data.config as Record<string,unknown>).prompt_guidance ?? '')} oninput={(event)=>updateSelected('prompt_guidance',(event.currentTarget as HTMLTextAreaElement).value)}></textarea></label>{/if}
          {#if selectedSlot}
            {#if configuredProviders.length}
              <label>Provider<select value={configuredVendor} onchange={(event)=>updateProvider((event.currentTarget as HTMLSelectElement).value)}><option value="" disabled>Choose a configured provider</option>{#each configuredProviders as provider}<option value={provider.vendor}>{provider.vendor} · {provider.credential_source}</option>{/each}</select></label>
              <label>Model<select value={configuredModel} disabled={!configuredProvider} onchange={(event)=>updateSelected('model',(event.currentTarget as HTMLSelectElement).value)}><option value="" disabled>Choose a model</option>{#each configuredProvider?.suggested_models ?? [] as model}<option value={model}>{model}{model===configuredProvider?.default_model ? ' · default' : ''}</option>{/each}</select></label>
              {#if selectedNode.data.type === 'video_generation'}
                {#if configuredProvider?.duration_grid?.length}<label>Duration<select value={Number((selectedNode.data.config as Record<string,unknown>).duration_sec ?? configuredProvider.duration_grid[0])} onchange={(event)=>updateSelected('duration_sec',Number((event.currentTarget as HTMLSelectElement).value))}>{#each configuredProvider.duration_grid as seconds}<option value={seconds}>{seconds} seconds</option>{/each}</select></label>
                {:else}<label>Duration in seconds<input type="number" min="1" max="30" value={Number((selectedNode.data.config as Record<string,unknown>).duration_sec ?? 5)} oninput={(event)=>updateSelected('duration_sec',Number((event.currentTarget as HTMLInputElement).value))}/></label>{/if}
              {/if}
              {#if configuredVendor && !configuredProvider}<p class="provider-warning">The saved provider is no longer connected. Choose one of the configured providers above.</p>{/if}
            {:else}
              <div class="provider-empty"><KeyRound size={16}/><span><strong>No provider connected for this node</strong><small>Add a compatible key in Workspace settings.</small></span><button class="subtle" onclick={openSettings}>Open settings</button></div>
            {/if}
          {/if}
          {#if selectedNode.data.type === 'asset_selector'}<label>Pinned variant<input type="number" min="1" max="4" value={Number((selectedNode.data.config as Record<string,unknown>).selected_index ?? 0)+1} oninput={(event)=>updateSelected('selected_index',Number((event.currentTarget as HTMLInputElement).value)-1)} /></label>{/if}
          <div class="inspector-actions"><button class="danger" onclick={removeSelected}>Remove node</button><button class="primary" onclick={() => saveRevision(`Edit ${String(selectedNode.data.label)}`)}>Save changes</button></div>
        </section>
      {:else}
        <section class="agent-panel"><div class="agent-avatar"><Bot size={20} /></div><h2>Direct the workflow</h2><p>Describe the look or revision you want. I’ll propose visible graph changes for you to approve.</p>
          <textarea bind:value={agentPrompt} placeholder="Make the product feel tactile and cinematic, with controlled violet highlights…"></textarea><button class="agent-send" onclick={askAgent} disabled={!agentPrompt.trim()}><Sparkles size={15} /> Propose changes</button>
          {#if proposal}<div class="proposal"><span class="proposal-label">PROPOSED PATCH</span><p>{proposal.rationale}</p>{#each proposal.operations as operation}<label class="operation"><input type="checkbox" checked={selectedOperations.has(operation.id)} onchange={()=>toggleOperation(operation.id)} /><span><strong>{operation.summary}</strong><small>{operation.type.replace('_',' ')}</small></span></label>{/each}<div class="cost-impact">Estimated impact <b>{project?.currency} {(proposal.estimated_cost_impact_minor/100).toFixed(2)}</b></div><button class="primary wide" onclick={applyAgentProposal} disabled={!selectedOperations.size}>Apply selected changes</button></div>{/if}
        </section>
      {/if}
    </aside>{/if}

    <footer class="run-tray">
      <div class="run-state"><span class:live={running}>{#if running}<LoaderCircle class="spin" size={16} />{:else}<Play size={16} />{/if}</span><div><strong>{running ? 'Workflow is running' : canResume ? 'Resume from the failure' : estimate ? 'Cost reviewed — ready to run' : 'Ready to explore'}</strong><small>{running ? latestActivity?.message ?? 'Starting nodes…' : canResume ? 'Succeeded nodes and durable provider outputs will be reused' : `${nodes.length} nodes · revision ${project?.current_revision_number}`}</small>{#if running && latestActivity?.progress != null}<div class="node-progress"><i style={`width:${Math.round(latestActivity.progress*100)}%`}></i></div>{/if}</div></div>
      {#if resumeEstimate}<div class="estimate"><small>Remaining work</small><strong>{project?.currency} {(resumeEstimate.estimated_cost_minor/100).toFixed(2)}</strong><span class:bad={!resumeEstimate.within_budget}>{resumeEstimate.recoverable_node_ids?.length ?? 0} provider output(s) recovered</span></div>{:else if estimate}<div class="estimate"><small>Estimated run</small><strong>{project?.currency} {(estimate.estimated_cost_minor/100).toFixed(2)}</strong><span class:bad={!estimate.within_budget}>{estimate.within_budget ? 'within project cap' : 'over project cap'}</span></div>{/if}
      <div class="run-actions">{#if running}<button class="danger" onclick={cancelRun}>Cancel run</button>{:else if canResume && resumeEstimate}<button class="primary run-button" onclick={resumeWorkflow} disabled={!resumeEstimate.within_budget}><Play size={15} /> Confirm & resume</button>{:else if canResume}<button class="primary run-button" onclick={reviewResume}><CircleDollarSign size={15} /> Review remaining cost</button>{:else if estimate}<button class="primary run-button" onclick={runWorkflow} disabled={!estimate.within_budget}><Play size={15} /> Confirm & run</button>{:else}<button class="primary run-button" onclick={getEstimate}><CircleDollarSign size={15} /> Review cost</button>{/if}</div>
    </footer>
  </div>
{/if}

{#if error}<div class="toast error"><X size={15} />{error}<button onclick={()=>error=''}><X size={13} /></button></div>{/if}
{#if notice}<div class="toast"><Sparkles size={15} />{notice}<button onclick={()=>notice=''}><X size={13} /></button></div>{/if}

{#if showOutput && finalVideoAsset}<div class="modal-backdrop" role="presentation"><section class="output-modal"><header><div><span class="eyebrow">FINAL OUTPUT</span><h2>Your video is ready</h2><p>This result remains attached to the project and can be reopened from the left sidebar.</p></div><button class="icon-btn" onclick={()=>showOutput=false}><X size={18}/></button></header><video src={assetUrl(finalVideoAsset.id)} controls playsinline preload="metadata"><track kind="captions" /></video><footer><div><strong>{finalVideoAsset.name}</strong><small>MP4 · {((finalVideoAsset.size ?? 0)/1024/1024).toFixed(2)} MB</small></div><a class="primary" href={assetUrl(finalVideoAsset.id)} target="_blank" rel="noopener noreferrer"><Film size={15}/> Open video</a></footer></section></div>{/if}

{#if showVariants}<div class="modal-backdrop" role="presentation"><section class="variant-modal"><header><div><span class="eyebrow">LOOK REVIEW</span><h2>Choose the frame worth building on</h2><p>Generated images stay in this project. Pinning saves a revision and prepares a targeted animation cost.</p></div><button class="icon-btn" onclick={()=>showVariants=false}><X size={18}/></button></header><div class="annotation-tools"><select bind:value={annotationKind}><option value="point">Point feedback</option><option value="rectangle">Rectangle feedback</option></select><input bind:value={annotationBody} placeholder="What should the agent change here?"/><button class="subtle" onclick={saveAnnotation} disabled={!annotationGeometry || !annotationBody.trim()}><Check size={14}/>Save feedback</button></div><div class="variant-grid">{#each variantAssets as asset,index}<article class="variant-card"><button class="image-review" aria-label={`Annotate variant ${index+1}`} onclick={(event)=>placeAnnotation(event,asset)}><img src={assetUrl(asset.id)} alt={asset.name}/>{#if annotationAsset?.id===asset.id && annotationGeometry}<i class:rectangle={annotationKind==='rectangle'} style={`left:${annotationGeometry.x*100}%;top:${annotationGeometry.y*100}%;width:${(annotationGeometry.width ?? 0)*100}%;height:${(annotationGeometry.height ?? 0)*100}%`}></i>{/if}</button><div><span>Variant {index+1}</span><small>{asset.name}</small><button onclick={()=>pinVariant(index)}>Pin & prepare animation</button></div></article>{/each}</div></section></div>{/if}

{#if showSettings}
  <div class="modal-backdrop" role="presentation"><section class="settings-modal"><header><div><span class="eyebrow">LOCAL WORKSPACE</span><h2>Projects & connections</h2><p>Provider and storage secrets stay in Windows Credential Manager and are never returned.</p></div><button class="icon-btn" onclick={()=>showSettings=false}><X size={18}/></button></header><div class="settings-columns"><section><h3>Projects</h3>{#each projects as item}<div class="project-row" class:active={item.id===project?.id}><input value={item.name} onchange={(event)=>item.id===project?.id && renameProject((event.currentTarget as HTMLInputElement).value)} readonly={item.id!==project?.id}/><button class="subtle" onclick={()=>{loadProject(item.id);showSettings=false}}>Open</button><button class="icon-btn danger-icon" title="Delete project" onclick={()=>removeProject(item)}><Trash2 size={14}/></button></div>{/each}<div class="create-row"><input bind:value={newProjectName} placeholder="New project name"/><button class="primary" onclick={createStudioProject}><Plus size={14}/>Create</button></div></section><section><h3>Provider credentials</h3><div class="connection-list">{#each connections as connection}<div><span><i class:connected={connection.configured}></i><b>{connection.vendor}</b><small>{connection.source}</small></span>{#if connection.source==='personal'}<button onclick={()=>clearProviderSecret(connection.vendor)}>Clear personal key</button>{/if}</div>{/each}</div><div class="credential-form"><label>Provider<select bind:value={providerVendor}>{#each connections as connection}<option value={connection.vendor}>{connection.vendor}</option>{/each}</select></label><label>Personal key<input type="password" bind:value={providerSecret} autocomplete="off" placeholder="Stored securely after save"/></label><button class="primary" onclick={saveProviderSecret} disabled={!providerSecret.trim()}><KeyRound size={14}/>Store key</button></div><h3>Project storage</h3><p class="description">Local storage is always available. Connect B2 only for durable cloud copies and provider-readable image references.</p><div class="connection-list"><div><span><i class:connected={storageConnection?.configured}></i><b>{storageConnection?.configured ? storageConnection.bucket_name : 'Local only'}</b><small>{storageConnection?.configured ? `${storageConnection.region} · ${storageConnection.key_id_hint}` : 'No cloud credentials required'}</small></span>{#if storageConnection?.source==='personal'}<button onclick={clearStorageConnection}>Clear B2</button>{/if}</div></div><div class="credential-form"><label>Region<input bind:value={b2Region} placeholder="us-west-004"/></label><label>Bucket<input bind:value={b2BucketName} placeholder="thikra-studio"/></label><label>Key ID<input type="password" bind:value={b2KeyId} autocomplete="off"/></label><label>Application key<input type="password" bind:value={b2ApplicationKey} autocomplete="off"/></label><button class="primary" onclick={saveStorageConnection} disabled={!b2Region||!b2BucketName||!b2KeyId||!b2ApplicationKey}><KeyRound size={14}/>Connect B2</button></div></section></div></section></div>
{/if}
