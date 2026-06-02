// Per-scene UI slots, filled as Stage B1/B2 stream `step.completed` events.
// The live per-scene rendering lives in `PipelineCanvas`'s MediaTile; this
// module is just the shared slot type that the canvas and `studio-page`
// consume.
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
