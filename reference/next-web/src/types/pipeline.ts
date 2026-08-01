// Wire shapes we read off the FastAPI proxy. These mirror genblaze-core
// stream events / Run / Step / Asset (Pydantic-serialised) closely enough
// for the UI; we don't reconstruct the full library type graph.
//
// IMPORTANT: `StepCompletedEvent.step` is marked `exclude=True` in
// genblaze-core's Pydantic model, so the JSON wire NEVER carries the
// step's assets. We instead receive a synthetic `scene.asset` SSE frame
// emitted by the backend for every step that finishes with an asset.

// Per-scene UI slots, filled as Stage B1/B2 stream `step.completed` events
// land. The live per-scene rendering lives in `PipelineCanvas`'s MediaTile;
// this is the shared slot shape the canvas and `studio-page` consume.
//
// Stage B2 step ordering (mirrored from docs/features/media-generation.md):
//   [video_0, tts_0, video_1, tts_1, ..., video_{N-1}, tts_{N-1}, music]
// So scene i's video lives at B2 step_index 2i and narration at 2i+1.
export type SceneSlots = {
  keyframeUrl?: string;
  clipUrl?: string;
  narrationUrl?: string;
  // Live best-effort failure flags (set from `step.failed`/failed
  // `step.completed` events) so a tile can show its fallback state before the
  // compose-time notice lands. Video falls back to the keyframe still;
  // narration to silence.
  videoFailed?: boolean;
  narrationFailed?: boolean;
};

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
  | {
      kind: "compose.complete";
      asset: Asset;
      spec: import("./storyboard").StoryboardSpec;
      run_id: string;
      // Durable B2 URL of the Stage B2 Manifest JSON — provenance for the
      // final MP4 (pipeline name, parent_run_id, per-step assets, canonical
      // hash). Optional because older backends may not have shipped it.
      manifest_uri?: string | null;
    }
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
