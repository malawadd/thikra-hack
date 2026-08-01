"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { StatusPill, type PillTone } from "@/components/ui/status-pill";
import { PipelineCanvas, type CanvasPhase } from "@/components/studio/pipeline-canvas";
import { InspectorDrawer } from "@/components/studio/inspector-drawer";
import { ReadinessNotice } from "@/components/studio/readiness-notice";
import { ProviderSelector } from "@/components/studio/provider-selector";
import { RunErrorPanel, type RunError } from "@/components/studio/run-error-panel";
import { streamSse } from "@/lib/sse-client";
import { API_BASE, ApiError, DEFAULT_SELECTION, playbackUrl, resolveModel, type Selection } from "@/lib/api-client";
import { useCreateStoryboard, useProviders } from "@/lib/queries";
import { clearRun, loadRun, saveRun } from "@/lib/run-store";
import type { Asset, SceneSlots, SseFrame } from "@/types/pipeline";
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
  // Durable B2 URL of the Stage B2 Manifest — populated alongside the
  // final MP4 so the Composition tile can open it for inspection.
  const [manifestUri, setManifestUri] = useState<string | null>(null);
  // The current run's fatal error (null when healthy). Drives RunErrorPanel.
  // Kept separate from `phase` so partial progress stays rendered behind it.
  const [runError, setRunError] = useState<RunError | null>(null);
  // Per-modality provider selection (the switchboard). Defaults to the
  // simplest path (Replicate + OpenAI); threaded into the media stream body.
  const [selection, setSelection] = useState<Selection>(DEFAULT_SELECTION);

  const createStoryboardMutation = useCreateStoryboard();
  // The provider catalog drives the per-tile "what's running" metadata so the
  // canvas reflects the live selection instead of a hardcoded default.
  const { data: providerMatrix } = useProviders();

  // A run is "live" during storyboard planning and media generation — both
  // gate the beforeunload guard and disable the provider switchboard.
  const generating = phase === "planning" || phase === "generating";

  // Guards the initial-mount restore from clobbering the saved snapshot before
  // we've read it (see the persist effect below).
  const [restored, setRestored] = useState(false);

  // Restore a run that survived a reload. The media pipeline streams from a
  // single request, so a reload mid-stream can't resume the stream — we restore
  // the partial canvas and flip an interrupted run to a recoverable error
  // (its finished assets are already durable in B2). Runs once on mount.
  useEffect(() => {
    // Restore must run post-mount, not from a render-time useState initializer:
    // loadRun() reads sessionStorage (undefined during SSR), so seeding initial
    // state from it would desync server/client HTML and trip a hydration
    // mismatch. React batches these setters into a single re-render, so the
    // set-state-in-effect lint warning is a false positive here.
    /* eslint-disable react-hooks/set-state-in-effect */
    const saved = loadRun();
    if (saved) {
      setSeed(saved.seed);
      setSpec(saved.spec);
      setSceneSlots(saved.sceneSlots);
      setReferenceUrl(saved.referenceUrl);
      setMusicUrl(saved.musicUrl);
      setMusicFailed(saved.musicFailed);
      setFinalAsset(saved.finalAsset);
      setRunId(saved.runId);
      setManifestUri(saved.manifestUri);
      setSelection(saved.selection);
      if (saved.phase === "planning" || saved.phase === "generating") {
        setPhase("error");
        setRunError({
          stage: "Interrupted",
          message: "This run was interrupted before it finished.",
          hint: "The pipeline streams in a single request that a page reload can't resume. Any completed assets were still saved to B2 — check Files. Retrying re-runs from the start and re-incurs provider cost.",
          retryable: true,
        });
      } else {
        setPhase(saved.phase);
      }
    }
    setRestored(true);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);

  // Persist the run so an accidental reload restores the canvas instead of
  // dropping to idle. Gated on `restored` so the mount pass can't overwrite the
  // snapshot before it's read; idle clears the slot.
  useEffect(() => {
    if (!restored) return;
    if (phase === "idle") {
      clearRun();
      return;
    }
    saveRun({
      phase, seed, spec, sceneSlots, referenceUrl, musicUrl,
      musicFailed, finalAsset, runId, manifestUri, selection,
    });
  }, [restored, phase, seed, spec, sceneSlots, referenceUrl, musicUrl,
      musicFailed, finalAsset, runId, manifestUri, selection]);

  // Warn before a reload/close throws away an in-flight, paid run. Only armed
  // while generating; the persisted snapshot handles the accidental case.
  useEffect(() => {
    if (!generating) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [generating]);

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
    setManifestUri(null);
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
    setManifestUri(null);
    setRunError(null);
    // Mirror the model the Script tile shows (the chat modality's resolved
    // model) so the synthesized Stage A frames never contradict the canvas.
    const chatModel = resolveModel(selection, providerMatrix, "chat");
    pushFrame({ kind: "stage.start", stage: "A.storyboard" });
    pushFrame({
      kind: "stream", stage: "A.storyboard",
      event: { type: "step.started", step_index: 0, model: chatModel, timestamp: new Date().toISOString() },
    });
    try {
      const sb = await createStoryboardMutation.mutateAsync(prompt);
      pushFrame({
        kind: "stream", stage: "A.storyboard",
        event: { type: "step.completed", step_index: 0, model: chatModel, timestamp: new Date().toISOString() },
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
      for await (const frame of streamSse(`${API_BASE}/runs/media/stream`, { prompt: seed, spec, selection })) {
        pushFrame(frame);
        harvestSlot(frame, spec.scenes.length);
        markFailedStep(frame, spec.scenes.length);
        if (frame.kind === "compose.complete") {
          setFinalAsset(frame.asset);
          setRunId(frame.run_id);
          setManifestUri(frame.manifest_uri ?? null);
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

  const status = statusFor(phase);

  return (
    // pb-[60px] reserves space for the collapsed inspector drawer at
    // the bottom of the viewport so content is never under it.
    // `min-w-0` keeps the studio-page column from inheriting the
    // canvas's intrinsic min-w-max width — that prevents the page
    // header from horizontally scrolling along with the canvas tiles.
    <div className="space-y-8 pb-[60px] min-w-0">
      <div className="animate-fade-in border-b border-border pb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="page-title">Genblaze Media Studio</h1>
          <p className="text-sm text-muted-foreground mt-1.5">
            One prompt → narrated, scored, captioned MP4 — a kitchen-sink test
            of the Genblaze SDK. Pick <em>any</em> provider per modality in the
            Providers panel (script, keyframes, motion, narration, score); the
            pipeline resolves each from the catalog and ffmpeg composes. The
            default path needs just two keys (OpenAI + Replicate). Every asset
            lands in Backblaze B2 via{" "}
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

      {/* Pre-flight advisory (missing keys / ffmpeg) — non-blocking. Scoped to
          the currently-selected providers. */}
      <ReadinessNotice selection={selection} />

      {/* The switchboard — pick any provider per modality. Collapsed by
          default since the simplest-path defaults already produce a video. */}
      <ProviderSelector
        selection={selection}
        onChange={setSelection}
        disabled={generating}
      />

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
        selection={selection}
        matrix={providerMatrix}
        seed={seed}
        spec={spec}
        setSpec={setSpec}
        slots={sceneSlots}
        referenceUrl={referenceUrl}
        musicUrl={musicUrl}
        musicFailed={musicFailed}
        finalAsset={finalAsset}
        runId={runId}
        manifestUri={manifestUri}
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
