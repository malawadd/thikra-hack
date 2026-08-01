export type ApiRecord = Record<string, any>;

export interface RunSummary {
  id: string;
  campaign_name: string;
  status: string;
  current_stage: string;
  budget_cap_minor: number;
  authorized_minor: number;
  spent_minor: number;
  retry_reserved_minor: number;
  remaining_minor: number;
  currency: string;
  retry_count: number;
  maximum_retries: number;
  accepted: boolean | null;
  human_escalation: boolean;
  provider_selection: Record<string, { vendor: string; model: string }>;
  payment_state: string;
  created_at: string;
  latest_event: string;
}

export interface AssetRecord {
  id: string;
  run_id: string;
  scene_id: string | null;
  type: string;
  provider: string;
  model: string;
  object_key: string;
  content_type: string;
  size: number;
  sha256: string;
  approval_state: string;
  cost_minor: number;
  preview_url: string;
  download_url: string;
  parent_asset_ids: string[];
  child_asset_ids: string[];
  created_at: string;
}

export interface LiveEvent {
  eventId: string;
  runId: string;
  type: string;
  timestamp: string;
  stage: string;
  progress: number;
  message: string;
  data: Record<string, unknown>;
}
