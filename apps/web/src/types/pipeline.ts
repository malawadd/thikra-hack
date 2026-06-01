// Wire shapes we read off the FastAPI proxy. These mirror genblaze-core
// stream events / Run / Step / Asset (Pydantic-serialised) closely enough
// for the UI; we don't reconstruct the full library type graph.
//
// IMPORTANT: `StepCompletedEvent.step` is marked `exclude=True` in
// genblaze-core's Pydantic model, so the JSON wire NEVER carries the
// step's assets. We instead receive a synthetic `scene.asset` SSE frame
// emitted by the backend for every step that finishes with an asset.

export type Asset = {
  asset_id?: string;
  url: string;
  media_type?: string;
  sha256?: string;
  size_bytes?: number;
  duration?: number;
};

export type StepInfo = {
  step_id: string;
  step_index: number;
  total_steps: number;
  provider: string;
  model: string;
  step_status?: string;
};

export type SseFrame =
  | { kind: "stage.start"; stage: string }
  | { kind: "stage.complete"; stage: string }
  | { kind: "stream"; stage: string; event: Record<string, unknown> & { type: string } }
  | {
      kind: "scene.asset";
      stage: string;
      step_index: number;
      asset_url: string;
      media_type?: string;
    }
  | { kind: "compose.complete"; asset: Asset; spec: import("./storyboard").StoryboardSpec; run_id: string }
  // Best-effort degradation (e.g. narration/music unavailable). Warning, not
  // a failure — the run still completes with a final MP4.
  | { kind: "notice"; stage: string; message: string }
  // Fatal failure. `code`/`retryable`/`hint` come from the backend classifier
  // (app/errors.py) so the UI can show an actionable message + the right
  // recovery. Older frames may omit them, so they're optional.
  | {
      kind: "error";
      stage: string;
      message: string;
      code?: string;
      retryable?: boolean;
      hint?: string;
    };
