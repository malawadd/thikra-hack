"use client";

// Per-scene preview strip. As Stage B1 + B2 stream `step.completed` events,
// each scene fills in three slots: keyframe image, video clip, narration WAV.
// Plan §2 calls this out as its own component (separate from the raw
// PipelineProgress event log) so users get a tangible per-scene view of
// the run as it lands.
//
// Stage B2 step ordering (mirrored from docs/features/media-generation.md):
//   [video_0, tts_0, video_1, tts_1, ..., video_{N-1}, tts_{N-1}, music]
// So scene i's video lives at B2 step_index 2i and narration at 2i+1.

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Scene } from "@/types/storyboard";

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

export function SceneStrip({ scenes, slots }: { scenes: Scene[]; slots: SceneSlots[] }) {
  if (scenes.length === 0) return null;
  return (
    <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {scenes.map((scene, i) => {
        const slot = slots[i] ?? {};
        return (
          <li key={i}>
            <Card>
              <CardContent className="space-y-3 p-3">
                <header className="flex items-center justify-between gap-2">
                  <span className="card-title text-sm">Scene {i + 1}</span>
                  <Badge variant="outline">{scene.duration_sec}s</Badge>
                </header>
                <p className="line-clamp-2 text-xs text-muted-foreground">{scene.caption}</p>
                <SlotPreview slot={slot} />
              </CardContent>
            </Card>
          </li>
        );
      })}
    </ol>
  );
}

function SlotPreview({ slot }: { slot: SceneSlots }) {
  // Prefer the animated clip over the still keyframe once Stage B2 lands.
  // Both slots remain useful: the keyframe is the visual anchor while
  // Decart is still rendering the clip.
  return (
    <div className="space-y-2">
      <div className="aspect-video w-full overflow-hidden rounded-md border bg-muted">
        {slot.clipUrl ? (
          <video
            src={slot.clipUrl}
            controls
            muted
            playsInline
            preload="metadata"
            className="h-full w-full object-cover"
          />
        ) : slot.keyframeUrl ? (
          // Plain <img> — keyframe is a presigned B2 URL, not statically
          // optimizable by `next/image`.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={slot.keyframeUrl} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
            waiting for keyframe…
          </div>
        )}
      </div>
      {slot.narrationUrl ? (
        <audio src={slot.narrationUrl} controls preload="none" className="w-full" />
      ) : (
        <p className="text-xs text-muted-foreground">narration pending</p>
      )}
    </div>
  );
}
