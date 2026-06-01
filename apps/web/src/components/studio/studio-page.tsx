"use client";

import { useState } from "react";
import { toast } from "sonner";
import { StatusPill, type PillTone } from "@/components/ui/status-pill";
import { type SceneSlots } from "@/components/genblaze/scene-strip";
import { PipelineCanvas, type CanvasPhase } from "@/components/studio/pipeline-canvas";
import { InspectorDrawer } from "@/components/studio/inspector-drawer";
import { ReadinessNotice } from "@/components/studio/readiness-notice";
import { RunErrorPanel, type RunError } from "@/components/studio/run-error-panel";
import { streamSse } from "@/lib/sse-client";
import { API_BASE, ApiError, playbackUrl } from "@/lib/api-client";
import { useCreateStoryboard } from "@/lib/queries";
import type { Asset, SseFrame } from "@/types/pipeline";
import type { StoryboardSpec } from "@/types/storyboard";

function statusFor(phase: CanvasPhase): { tone: PillTone; label: string; dot?: boolean } {
  switch (phase) {
    case "idle":       return { tone: "neutral", label: "Idle" };
    case "planning":   return { tone: "active",  label: "Planning storyboard", dot: true };
    case "refining":   return { tone: "amber",   label: "Awaiting approval" };
    case "generating": return { tone: "active",  label: "Generating media", dot: true };
    case "done":       return { tone: "green",   label: "Complete" };
    case "error":      return { tone: "red",     label: "Failed" };
  }
}

export function StudioPage() {
  const [phase, setPhase] = useState<CanvasPhase>("idle");
  const [spec, setSpec] = useState<StoryboardSpec | null>(null);
  const [seed, setSeed] = useState("");
  const [frames, setFrames] = useState<SseFrame[]>([]);
  const [sceneSlots, setSceneSlots] = useState<SceneSlots[]>([]);
  const [referenceUrl, setReferenceUrl] = useState<string | null>(null);
  const [musicUrl, setMusicUrl] = useState<string | null>(null);
  const [musicFailed, setMusicFailed] = useState(false);
  const [finalAsset, setFinalAsset] = useState<Asset | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  // The current run's fatal error (null when healthy). Drives RunErrorPanel.
  // Kept separate from `phase` so partial progress stays rendered behind it.
  const [runError, setRunError] = useState<RunError | null>(null);

  const createStoryboardMutation = useCreateStoryboard();

  const pushFrame = (frame: SseFrame) => setFrames((prev) => [...prev, frame]);

  // Harvest per-scene assets from `scene.asset` SSE frames. Backend
  // synthesises these because `StepCompletedEvent.step` is `exclude=True`
  // in the Genblaze JSON wire payload. `media_type` is authoritative.
  //
  // Stage layout the backend emits, mirrored here:
  //   B0.reference → one image (step_index 0) — style reference.
  //   B1.keyframes → N images (step_index 0..N-1) — per-scene keyframes.
  //   B2.media     → (video, tts) × N + music — music is at step_index 2N.
  const harvestSlot = (frame: SseFrame, sceneCount: number) => {
    if (frame.kind !== "scene.asset") return;
    const { stage, step_index, asset_url, media_type = "" } = frame;
    const url = playbackUrl(asset_url);
    if (stage === "B0.reference" && media_type.startsWith("image/")) {
      setReferenceUrl(url);
      return;
    }
    setSceneSlots((prev) => {
      const next = prev.length === sceneCount
        ? [...prev]
        : Array.from({ length: sceneCount }, () => ({}));
      if (stage === "B1.keyframes" && media_type.startsWith("image/")) {
        if (step_index < sceneCount) next[step_index] = { ...next[step_index], keyframeUrl: url };
      } else if (stage === "B2.media") {
        const sceneIdx = Math.floor(step_index / 2);
        if (sceneIdx >= sceneCount) {
          // Trailing music step lives past the (video, tts)×N block.
          if (media_type.startsWith("audio/")) setMusicUrl(url);
          return next;
        }
        if (media_type.startsWith("video/")) {
          next[sceneIdx] = { ...next[sceneIdx], clipUrl: url };
        } else if (media_type.startsWith("audio/")) {
          next[sceneIdx] = { ...next[sceneIdx], narrationUrl: url };
        }
      }
      return next;
    });
  };

  // Live (pre-compose) failure feedback. A failed step arrives inside a
  // `stream` frame — either a `step.failed` event or a `step.completed` with
  // `step_status==="failed"`. We flag the matching B2 scene slot / music so a
  // spinning tile flips to "failed → fallback" immediately, rather than only
  // when the authoritative compose-time `notice` lands. Same 2i/2i+1/last
  // layout as `harvestSlot`.
  const markFailedStep = (frame: SseFrame, sceneCount: number) => {
    if (frame.kind !== "stream" || frame.stage !== "B2.media") return;
    const ev = frame.event as { type?: string; step_index?: number; step_status?: string };
    const failed = ev.type === "step.failed" || (ev.type === "step.completed" && ev.step_status === "failed");
    if (!failed || typeof ev.step_index !== "number") return;
    const sceneIdx = Math.floor(ev.step_index / 2);
    if (sceneIdx >= sceneCount) { setMusicFailed(true); return; }
    setSceneSlots((prev) => {
      const next = prev.length === sceneCount
        ? [...prev]
        : Array.from({ length: sceneCount }, () => ({}));
      const key = ev.step_index! % 2 === 0 ? "videoFailed" : "narrationFailed";
      next[sceneIdx] = { ...next[sceneIdx], [key]: true };
      return next;
    });
  };

  const resetAll = () => {
    setPhase("idle");
    setSpec(null);
    setSeed("");
    setFrames([]);
    setSceneSlots([]);
    setReferenceUrl(null);
    setMusicUrl(null);
    setMusicFailed(false);
    setFinalAsset(null);
    setRunId(null);
    setRunError(null);
  };

  const handleSubmit = async (prompt: string) => {
    setSeed(prompt);
    setPhase("planning");
    setFrames([]);
    setSceneSlots([]);
    setReferenceUrl(null);
    setMusicUrl(null);
    setMusicFailed(false);
    setFinalAsset(null);
    setRunId(null);
    setRunError(null);
    pushFrame({ kind: "stage.start", stage: "A.storyboard" });
    pushFrame({
      kind: "stream", stage: "A.storyboard",
      event: { type: "step.started", step_index: 0, model: "gpt-4.1-nano", timestamp: new Date().toISOString() },
    });
    try {
      const sb = await createStoryboardMutation.mutateAsync(prompt);
      pushFrame({
        kind: "stream", stage: "A.storyboard",
        event: { type: "step.completed", step_index: 0, model: "gpt-4.1-nano", timestamp: new Date().toISOString() },
      });
      pushFrame({ kind: "stage.complete", stage: "A.storyboard" });
      setSpec(sb.spec);
      setPhase("refining");
    } catch (e) {
      // The backend returns a classified body (app/errors.py); ApiError carries
      // its hint + retryable. Surface them instead of a raw 500.
      const apiErr = e instanceof ApiError ? e : null;
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`Storyboard failed: ${msg}`);
      pushFrame({ kind: "error", stage: "A.storyboard", message: msg });
      setRunError({
        stage: "Storyboard",
        message: msg,
        hint: apiErr?.hint,
        retryable: apiErr?.isRetryable ?? true,
      });
      setPhase("error");
    }
  };

  const startMedia = async () => {
    if (!spec) return;
    setPhase("generating");
    setSceneSlots(Array.from({ length: spec.scenes.length }, () => ({})));
    setReferenceUrl(null);
    setMusicUrl(null);
    setMusicFailed(false);
    setFinalAsset(null);
    setRunError(null);
    try {
      for await (const frame of streamSse(`${API_BASE}/runs/media/stream`, { prompt: seed, spec })) {
        pushFrame(frame);
        harvestSlot(frame, spec.scenes.length);
        markFailedStep(frame, spec.scenes.length);
        if (frame.kind === "compose.complete") {
          setFinalAsset(frame.asset);
          setRunId(frame.run_id);
          setPhase("done");
          toast.success("Final MP4 written to B2");
        }
        if (frame.kind === "notice") {
          // Best-effort degradation (narration/music) — warn, don't fail.
          toast.warning(frame.message);
        }
        if (frame.kind === "error") {
          toast.error(`${frame.stage}: ${frame.message}`);
          setRunError({
            stage: frame.stage,
            message: frame.message,
            hint: frame.hint,
            retryable: frame.retryable ?? true,
          });
          setPhase("error");
        }
      }
    } catch (e) {
      // Transport-level failure (the connection dropped mid-stream). Partial
      // tiles stay on screen; offer a retry.
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`Stream failed: ${msg}`);
      setRunError({
        stage: "Streaming",
        message: msg,
        hint: "The connection to the API dropped mid-stream.",
        retryable: true,
      });
      setPhase("error");
    }
  };

  // Recovery actions for RunErrorPanel. With a spec we re-run the media
  // pipeline from the start (Stage A succeeded); without one (Stage A itself
  // failed) we re-run the storyboard from the seed prompt.
  const retryRun = () => {
    setRunError(null);
    if (spec) void startMedia();
    else if (seed) void handleSubmit(seed);
  };
  const editStoryboard = () => {
    setRunError(null);
    setPhase("refining");
  };

  const generating = phase === "planning" || phase === "generating";
  const status = statusFor(phase);

  return (
    // pb-[60px] reserves space for the collapsed inspector drawer at
    // the bottom of the viewport so content is never under it.
    <div className="space-y-8 pb-[60px]">
      <div className="animate-fade-in border-b border-border pb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="page-title">Genblaze Media Studio</h1>
          <p className="text-sm text-muted-foreground mt-1.5">
            One prompt → narrated, scored, captioned MP4. Each module below
            lights up as Genblaze drives the next stage of the pipeline —
            OpenAI scripts + paints, Decart animates, NVIDIA narrates,
            GMICloud scores, ffmpeg composes. Every asset lands in
            Backblaze B2 via{" "}
            <span className="font-mono text-foreground/80">genblaze</span>.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill tone={status.tone} dot={status.dot}>{status.label}</StatusPill>
          {runId && (
            <span className="status-pill font-mono" title={`Run ID: ${runId}`}>
              run · {runId.slice(0, 8)}
            </span>
          )}
        </div>
      </div>

      {/* Pre-flight advisory (missing keys / ffmpeg) — non-blocking. */}
      <ReadinessNotice />

      {/* Persistent failure panel — stays up with partial progress visible
          behind it, unlike the ephemeral toast. */}
      {runError && (
        <RunErrorPanel
          error={runError}
          onRetry={retryRun}
          onEdit={spec ? editStoryboard : undefined}
          onStartOver={resetAll}
        />
      )}

      {/* The whole experience is one horizontal canvas. Tiles slide in
          left-to-right (`tile-in` keyframe) as each upstream stage delivers. */}
      <PipelineCanvas
        phase={phase}
        seed={seed}
        spec={spec}
        setSpec={setSpec}
        slots={sceneSlots}
        referenceUrl={referenceUrl}
        musicUrl={musicUrl}
        musicFailed={musicFailed}
        finalAsset={finalAsset}
        runId={runId}
        generating={generating}
        onSubmit={handleSubmit}
        onRestart={resetAll}
        onStartMedia={startMedia}
      />

      {/* Inspector lives in a bottom-docked collapsible drawer — never
          covers the canvas. Click the strip to expand. */}
      <InspectorDrawer frames={frames} isLive={generating} />
    </div>
  );
}
