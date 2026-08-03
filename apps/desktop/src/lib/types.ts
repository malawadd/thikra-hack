export type NodeType = 'creative_brief' | 'reference_asset' | 'look_director' | 'image_generation' | 'asset_selector' | 'video_generation' | 'narration' | 'music' | 'composition' | 'verification' | 'export' | 'note' | 'group';
export type NodeStatus = 'IDLE' | 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'BLOCKED' | 'CANCELLED' | 'CACHED';
export interface WorkflowNode { id: string; type: NodeType; label: string; config: Record<string, unknown>; }
export interface WorkflowEdge { id: string; source: string; source_port: string; target: string; target_port: string; }
export interface WorkflowGraph { schema_version: 1; nodes: WorkflowNode[]; edges: WorkflowEdge[]; }
export interface Revision { id: string; number: number; graph: WorkflowGraph; content_hash: string; summary: string; source: string; }
export interface Project { id: string; name: string; description: string; currency: string; budget_cap_minor: number; spent_minor: number; remaining_minor: number; current_revision_id: string; current_revision_number: number; revision: Revision; layout: Record<string, { x: number; y: number }>; viewport: { x: number; y: number; zoom: number }; }
export interface CatalogNode { type: NodeType; label: string; category: string; description: string; }
export interface ProposalOperation { id: string; type: string; node_id?: string; node?: WorkflowNode; edge?: WorkflowEdge; config_patch?: Record<string, unknown>; depends_on: string[]; summary: string; }
export interface Proposal { id: string; base_revision_id: string; rationale: string; estimated_cost_impact_minor: number; operations: ProposalOperation[]; }
export interface Estimate { revision_id: string; estimate_hash: string; estimated_cost_minor: number; remaining_minor: number; within_budget: boolean; target_node_ids: string[]; line_items: { node_id: string; node_type: string; amount_minor: number }[]; resume_from_execution_id?: string; recoverable_node_ids?: string[]; reused_node_ids?: string[]; }
export interface NodeRun { node_id: string; node_type: NodeType; status: NodeStatus; output: { kind?: string; assets?: StudioAsset[]; asset?: StudioAsset; demo_url?: string; status?: string }; error?: string; estimated_cost_minor: number; charged_minor: number; }
export interface Execution { id: string; revision_id: string; status: string; estimated_cost_minor: number; cancel_requested: boolean; failure_reason?: string; resumed_from_execution_id?: string; nodes: NodeRun[]; }
export interface StudioAsset { id: string; name: string; asset_type?: string; content_type: string; content_url?: string; thumbnail_url?: string; proxy_url?: string; size?: number; width?: number; height?: number; duration_ms?: number; frame_rate?: string; has_audio?: boolean; source_kind?: 'IMPORTED'|'GENERATED'|'RENDERED'; analysis_status?: 'PENDING'|'RUNNING'|'READY'|'FAILED'; created_at?: string; }
export interface StudioEvent { eventId: string; executionId: string; revisionId: string; nodeId: string | null; type: string; status: string; message: string; progress?: number | null; data: Record<string, unknown>; estimatedCostMinor: number; }
export interface ProviderConnection { vendor: string; configured: boolean; source: 'personal' | 'environment' | 'none'; }
export interface ProviderOption { vendor: string; default_model: string; suggested_models: string[]; modality: string; key_available: boolean; credential_source: 'personal' | 'environment' | 'none'; supports_seed: boolean; supports_reference_input: boolean; supports_text_only: boolean; duration_grid: number[] | null; }
export type ProviderMatrix = Record<'chat' | 'image' | 'video' | 'tts' | 'music', ProviderOption[]>;

export type TrackKind = 'visual' | 'text' | 'caption' | 'audio';
export type ClipKind = 'video' | 'image' | 'text' | 'caption' | 'audio';
export type SequencePreset = 'landscape_720'|'landscape_1080'|'portrait_720'|'portrait_1080'|'square_720'|'square_1080';
export interface SequenceTrack { id:string; name:string; kind:TrackKind; order:number; locked:boolean; hidden:boolean; muted:boolean; }
export interface VisualTransform { fit:'fit'|'fill'; position_x:number; position_y:number; scale:number; rotation:number; opacity:number; fade_in_ms:number; fade_out_ms:number; ken_burns:boolean; }
export interface AudioSettings { gain_db:number; muted:boolean; fade_in_ms:number; fade_out_ms:number; role:'source'|'narration'|'music'|'other'; }
export interface TextSettings { content:string; font_family:'Noto Sans'|'Noto Sans Arabic'|'Noto Serif'; font_size:number; font_weight:400|500|600|700|800; color:string; background:string; align:'left'|'center'|'right'; position_x:number; position_y:number; }
export interface SequenceClip { id:string; track_id:string; kind:ClipKind; name:string; asset_id?:string; start_ms:number; duration_ms:number; source_in_ms:number; transition_in:'cut'|'dissolve'|'fade_black'; transition_out:'cut'|'dissolve'|'fade_black'; transition_duration_ms:number; transform:VisualTransform; audio:AudioSettings; text?:TextSettings; }
export interface SequenceDocument { schema_version:1|2; preset:SequencePreset; background:string; tracks:SequenceTrack[]; clips:SequenceClip[]; duck_music_under_narration:boolean; captions_stale:boolean; }
export interface SequenceRevision { id:string; sequence_id:string; number:number; parent_revision_id?:string; document:SequenceDocument; content_hash:string; summary:string; source:string; created_at:string; }
export interface StudioSequence { id:string; project_id:string; name:string; current_revision_id:string; current_revision_number:number; revision?:SequenceRevision; view_state:{playhead_ms:number;zoom:number;selection:string[]}; }
export interface StudioRender { id:string; sequence_id:string; revision_id:string; preset:SequencePreset; status:'QUEUED'|'RUNNING'|'SUCCEEDED'|'FAILED'|'CANCELLED'; progress:number; output_asset_id?:string; srt_asset_id?:string; cancel_requested:boolean; error?:string; }
