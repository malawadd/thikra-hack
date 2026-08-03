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
export interface StudioAsset { id: string; name: string; asset_type?: string; content_type: string; content_url?: string; size?: number; created_at?: string; }
export interface StudioEvent { eventId: string; executionId: string; revisionId: string; nodeId: string | null; type: string; status: string; message: string; progress?: number | null; data: Record<string, unknown>; estimatedCostMinor: number; }
export interface ProviderConnection { vendor: string; configured: boolean; source: 'personal' | 'environment' | 'none'; }
export interface ProviderOption { vendor: string; default_model: string; suggested_models: string[]; modality: string; key_available: boolean; credential_source: 'personal' | 'environment' | 'none'; supports_seed: boolean; supports_reference_input: boolean; duration_grid: number[] | null; }
export type ProviderMatrix = Record<'chat' | 'image' | 'video' | 'tts' | 'music', ProviderOption[]>;
