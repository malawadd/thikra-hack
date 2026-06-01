"use client";

// Horizontal pipeline canvas — one left-to-right row of tiles. The Seed
// prompt is the FIRST tile (interactive in idle, summary chip after
// submit). Each subsequent stage appears as its own tile with a slide-in
// from the right (`tile-in`) the first time data lands.
//
// Tile sequence:
//   [Seed] → [Script] → [Storyboard] → [Video + TTS + Music] → [Composition]
//
// Each tile carries a model + provider line so the user can see exactly
// which library surface is doing what. Loading placeholders use the
// starter-kit's `<GeneratingLoader>` (flames + stars variants) rather
// than text — it reads as deliberate motion.
//
// Containment note: `w-full min-w-0 overflow-x-auto` on the scroll
// container, `min-w-max` on the inner ol. That combination scrolls
// HORIZONTALLY INSIDE the canvas without pushing the page wide — the
// outer page header and the inspector drawer stay anchored.

import { useEffect, useRef, useState } from "react";
import {
  ArrowRight, FileText, Image as ImageIcon, Sparkles, Film, Music as MusicIcon,
  Pencil, ChevronUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { StatusPill } from "@/components/ui/status-pill";
import { GeneratingLoader } from "@/components/ui/generating-loader";
import { PromptForm } from "@/components/genblaze/prompt-form";
import { cn } from "@/lib/utils";
import type { Asset } from "@/types/pipeline";
import type { Scene, StoryboardSpec } from "@/types/storyboard";
import type { SceneSlots } from "@/components/genblaze/scene-strip";

export type CanvasPhase =
  | "idle" | "planning" | "refining" | "generating" | "done" | "error";

interface PipelineCanvasProps {
  phase: CanvasPhase;
  seed: string;
  spec: StoryboardSpec | null;
  setSpec: (s: StoryboardSpec) => void;
  slots: SceneSlots[];
  referenceUrl: string | null;
  musicUrl: string | null;
  musicFailed: boolean;
  finalAsset: Asset | null;
  runId: string | null;
  generating: boolean;
  onSubmit: (prompt: string) => void;
  onRestart: () => void;
  onStartMedia: () => void;
}

// Centralised "what runs where" — the source-of-truth metadata strip
// that every tile renders under its content. Mirrors services/api/app/
// repo/pipelines.py's stage layout.
const META = {
  seed:    { model: "—",                      provider: "—" },
  script:  { model: "gpt-4.1-nano",           provider: "OpenAI chat() · response_format" },
  ref:     { model: "gpt-image-1",            provider: "OpenAI · DalleProvider" },
  scenes:  { model: "gpt-image-1",            provider: "OpenAI · DalleProvider" },
  video:   { model: "lucy-pro",               provider: "Decart · DecartVideoProvider" },
  tts:     { model: "nvidia/magpie-tts-multilingual", provider: "NVIDIA · NvidiaAudioProvider" },
  music:   { model: "minimax-music-2.5",      provider: "GMICloud · GMICloudAudioProvider" },
  compose: { model: "ffmpeg",                 provider: "local subprocess (composer.py)" },
} as const;

type TileKey = "seed" | "script" | "storyboard" | "media" | "composition";

export function PipelineCanvas(p: PipelineCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  // Visibility ladder — each tile lights up when the upstream stage
  // delivers its first signal.
  //   - Script: visible during `planning` too (so the loader is on-screen
  //     while Stage A is running, not just after the spec lands).
  //   - Storyboard: opens once we have a spec, stays through the run.
  //   - Media: gated behind the Storyboard fully landing — we wait for
  //     every keyframe to be ready before exposing this tile, so the
  //     "Video + TTS + Music" stage doesn't appear before the upstream
  //     visuals do. Done / error keep it mounted regardless.
  //   - Composition: appears the moment the final MP4 lands.
  const allKeyframesReady = !!p.spec
    && p.slots.length === p.spec.scenes.length
    && p.slots.every((s) => !!s.keyframeUrl);
  const showScript = p.spec !== null || p.phase === "planning";
  const showStoryboard = p.spec !== null && ["refining", "generating", "done", "error"].includes(p.phase);
  const showMedia =
    p.phase === "done" || p.phase === "error" ||
    (p.phase === "generating" && allKeyframesReady);
  const showComp = p.finalAsset !== null;

  // The "currently processing" tile — what the user's attention should
  // ride on. Drives the auto-center-scroll target. `done` parks on the
  // composition tile (the final reward); `error` parks on whichever stage
  // was running when it failed so the user lands on the relevant context.
  const activeTile: TileKey = (() => {
    if (p.phase === "done") return "composition";
    if (p.phase === "idle") return "seed";
    if (p.phase === "planning" || p.phase === "refining") return "script";
    // generating / error — descend through the visible stages and stop on
    // the first that is still in flight (or the latest mounted one).
    if (p.finalAsset) return "composition";
    if (showMedia) return "media";
    if (showStoryboard) return "storyboard";
    if (showScript) return "script";
    return "seed";
  })();

  // Center the active tile horizontally inside the canvas whenever it
  // changes. `inline: "center"` aligns the element to the centre of the
  // scroll viewport without disturbing manual scrolling between
  // transitions — the user can drag/scroll freely; the auto-snap only
  // fires when the pipeline advances to a new stage. Reduced-motion users
  // jump instantly instead of animating.
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    requestAnimationFrame(() => {
      const el = root.querySelector<HTMLElement>(`[data-tile-key="${activeTile}"]`);
      if (!el) return;
      el.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: "nearest",
        inline: "center",
      });
    });
  }, [activeTile]);

  return (
    // w-full + min-w-0 + overflow-x-auto: clip ANY horizontal overflow
    // INSIDE the canvas. The outer page (header, status pill, drawer)
    // stays put even when the tile row is wider than the viewport.
    <div
      ref={containerRef}
      className="w-full min-w-0 overflow-x-auto overscroll-x-contain pb-2"
    >
      <ol className="flex items-stretch gap-3 min-w-max pr-6">
        <SeedTile p={p} />
        {showScript && <Arrow active={p.phase === "planning"} />}
        {showScript && (
          p.spec ? (
            <ScriptTile
              spec={p.spec}
              phase={p.phase}
              generating={p.generating}
              onStartMedia={p.onStartMedia}
              setSpec={p.setSpec}
            />
          ) : (
            <ScriptPlaceholderTile />
          )
        )}
        {showStoryboard && <Arrow active={p.phase === "generating" && !allKeyframesReady} />}
        {showStoryboard && <StoryboardTile
          spec={p.spec!}
          referenceUrl={p.referenceUrl}
          slots={p.slots}
        />}
        {showMedia && <Arrow active={p.phase === "generating"} />}
        {showMedia && <MediaTile
          spec={p.spec!}
          slots={p.slots}
          musicUrl={p.musicUrl}
          musicFailed={p.musicFailed}
          done={p.phase === "done"}
        />}
        {showComp && <Arrow />}
        {showComp && <CompositionTile asset={p.finalAsset!} runId={p.runId} />}
      </ol>
    </div>
  );
}

// ---------- shared shell ----------------------------------------------------

interface TileShellProps {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  status: "idle" | "active" | "ready" | "failed";
  width?: "narrow" | "wide" | "extra";
  children: React.ReactNode;
  badge?: React.ReactNode;
  /** Model + provider footer — every tile gets one for traceability. */
  meta?: { model: string; provider: string };
  /** Extra class on the outer `<li>` — e.g. `tile-celebrate` on Composition. */
  className?: string;
  /** Stable stage identifier used by the canvas's auto-centering scroll. */
  tileKey?: TileKey;
}

function TileShell({ title, icon: Icon, status, width = "narrow", children, badge, meta, className, tileKey }: TileShellProps) {
  const w = width === "extra" ? "w-[480px]" : width === "wide" ? "w-[360px]" : "w-[300px]";
  // Provider string in META is "Vendor · ClassName" (e.g. "OpenAI · DalleProvider").
  // Take just the vendor for the compact header chip; the full string lives
  // in the title attribute (hover tooltip) so it's still discoverable.
  const providerShort = meta?.provider.split(" · ")[0];
  return (
    <li
      className={cn(
        "tile-in shrink-0 flex flex-col rounded-xl border border-border bg-card overflow-hidden transition-colors duration-300",
        w,
        className,
      )}
      data-status={status}
      data-tile-key={tileKey}
    >
      {/* Header — model + provider chip (swapped from footer). The model is
          the most useful at-a-glance answer to "what's running this step?";
          status pill moved to the footer per the swap. */}
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="card-title text-sm truncate" title={title}>{title}</span>
        </div>
        {meta && (
          <div
            className="flex items-center gap-1.5 text-[10px] shrink-0 min-w-0"
            title={`${meta.model} via ${meta.provider}`}
          >
            <span className="font-mono text-foreground/80 truncate">{meta.model}</span>
            {providerShort && (
              <>
                <span className="text-muted-foreground/40">·</span>
                <span className="text-muted-foreground truncate">{providerShort}</span>
              </>
            )}
          </div>
        )}
      </div>
      <div className="flex-1 p-3 min-h-0">{children}</div>
      {/* Footer — status pill (swapped from header). */}
      {badge && (
        <div className="border-t border-border bg-muted/30 px-3 py-1.5 flex items-center">
          {badge}
        </div>
      )}
    </li>
  );
}

function Arrow({ active = false }: { active?: boolean }) {
  // When `active`, the arrow pulses to suggest data flowing left → right
  // between the two surrounding tiles. Otherwise it's a static chevron.
  return (
    <li
      aria-hidden
      className={cn(
        "self-center shrink-0 tile-in transition-colors duration-300",
        active ? "text-primary arrow-flow" : "text-muted-foreground/40",
      )}
    >
      <ArrowRight className="h-5 w-5" />
    </li>
  );
}

// Skeleton tile shown while Stage A (chat) is in flight and no spec has
// arrived yet. Keeps the canvas balanced (script slot is always there
// the instant the user clicks Generate) and gives the user a clear
// "yes, something is happening" signal.
function ScriptPlaceholderTile() {
  return (
    <TileShell
      title="Script"
      icon={FileText}
      status="active"
      width="wide"
      tileKey="script"
      meta={META.script}
      badge={<StatusPill tone="active" dot>planning</StatusPill>}
    >
      <div className="flex h-full min-h-[260px] items-center justify-center py-8">
        <GeneratingLoader size="md" variant="flames" label="Writing script" />
      </div>
    </TileShell>
  );
}

// Centered loader for empty media cells.
function CellLoader({ variant = "flames", label }: { variant?: "flames" | "stars"; label?: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center">
      <GeneratingLoader size="md" variant={variant} label={label} />
    </div>
  );
}

// ---------- 1. Seed --------------------------------------------------------

function SeedTile({ p }: { p: PipelineCanvasProps }) {
  if (p.phase === "idle") {
    return (
      <TileShell
        title="Seed prompt"
        icon={Pencil}
        status="active"
        width="wide"
        tileKey="seed"
        badge={<StatusPill tone="amber">awaiting input</StatusPill>}
        meta={META.seed}
      >
        <PromptForm onSubmit={p.onSubmit} disabled={p.generating} />
      </TileShell>
    );
  }
  return (
    <TileShell
      title="Seed prompt"
      icon={Pencil}
      status="ready"
      width="narrow"
      tileKey="seed"
      badge={<StatusPill tone="green">submitted</StatusPill>}
      meta={META.seed}
    >
      <p className="text-sm font-medium leading-snug line-clamp-4">{p.seed}</p>
      <button
        type="button"
        onClick={p.onRestart}
        className="mt-3 text-xs font-medium text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
      >
        ← Start over with a new prompt
      </button>
    </TileShell>
  );
}

// ---------- 2. Script (Stage A storyboard JSON) ----------------------------
//
// UX redesign vs. the previous nested-accordion approach:
//   - Title / Visual style / Music prompt live at the top of the tile.
//     Read-only after approval; in `refining` they become Input/Textarea
//     inline — no separate "review" panel, no nested accordion.
//   - Scenes are always-visible compact rows. In `refining` clicking a
//     row expands its full field set inline (caption / narration / image /
//     motion / duration), without opening modals or jumping focus.
//   - "Generate media →" sits at the bottom of the tile body and stays
//     visible throughout refining; no scrolling through accordions to find
//     the CTA.

function ScriptTile({
  spec, phase, generating, onStartMedia, setSpec,
}: {
  spec: StoryboardSpec;
  phase: CanvasPhase;
  generating: boolean;
  onStartMedia: () => void;
  setSpec: (s: StoryboardSpec) => void;
}) {
  const planning = phase === "planning";
  const refining = phase === "refining";
  const status = planning ? "active" : "ready";
  // Tracks which scene row is open for inline editing. Only one row at a
  // time so the tile doesn't grow unbounded.
  const [expandedScene, setExpandedScene] = useState<number | null>(null);

  const updateScene = (i: number, patch: Partial<Scene>) => {
    setSpec({
      ...spec,
      scenes: spec.scenes.map((s, idx) => (idx === i ? { ...s, ...patch } : s)),
    });
  };

  return (
    <TileShell
      title="Script"
      icon={FileText}
      status={status}
      width="wide"
      tileKey="script"
      meta={META.script}
      badge={
        planning
          ? <StatusPill tone="active" dot>planning</StatusPill>
          : refining
            ? <StatusPill tone="amber">review &amp; refine</StatusPill>
            : <StatusPill tone="green">approved</StatusPill>
      }
    >
      {planning ? (
        <div className="flex h-full items-center justify-center py-8">
          <GeneratingLoader size="md" variant="flames" label="Writing script" />
        </div>
      ) : (
        <div className="space-y-3 max-h-[520px] overflow-y-auto pr-1 -mr-1">
          {/* Title — inline-editable in refining. */}
          <Field
            label="Title"
            value={spec.title}
            onChange={(v) => setSpec({ ...spec, title: v })}
            editable={refining && !generating}
            displaySize="sm"
          />

          {/* Visual style — inline-editable in refining. */}
          <Field
            label="Visual style"
            value={spec.style_prompt}
            onChange={(v) => setSpec({ ...spec, style_prompt: v })}
            editable={refining && !generating}
            multiline
            rows={2}
            displaySize="xs"
          />

          {/* Music prompt — editing only matters in refining; hide otherwise
              to keep the tile compact. */}
          {refining && (
            <Field
              label="Music"
              value={spec.music_prompt}
              onChange={(v) => setSpec({ ...spec, music_prompt: v })}
              editable={!generating}
              displaySize="xs"
            />
          )}

          {/* Scenes — always visible compact rows; expand in place to edit. */}
          <div className="space-y-1.5 pt-1">
            <div className="flex items-center justify-between gap-2">
              <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
                Scenes
              </Label>
              <span className="text-[10px] text-muted-foreground font-mono tabular-nums">
                {spec.scenes.length} · {spec.total_duration_sec}s
              </span>
            </div>
            <ol className="space-y-1.5">
              {spec.scenes.map((scene, i) => (
                <SceneRow
                  key={i}
                  index={i}
                  scene={scene}
                  editable={refining && !generating}
                  expanded={expandedScene === i}
                  onToggle={() => setExpandedScene((cur) => (cur === i ? null : i))}
                  onChange={(patch) => updateScene(i, patch)}
                />
              ))}
            </ol>
          </div>

          {refining && (
            <div className="pt-1 border-t border-border/60">
              <Button
                onClick={onStartMedia}
                size="sm"
                className="h-9 w-full"
                disabled={generating}
              >
                Generate media →
              </Button>
            </div>
          )}
        </div>
      )}
    </TileShell>
  );
}

// Read-display-or-edit field used by ScriptTile for title/style/music.
// `displaySize` controls the rendered text size when read-only; the
// editable form (Input/Textarea) uses a fixed compact size regardless.
function Field({
  label, value, onChange, editable, multiline, rows = 2, displaySize = "xs",
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  editable: boolean;
  multiline?: boolean;
  rows?: number;
  displaySize?: "xs" | "sm";
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      {editable ? (
        multiline ? (
          <Textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            rows={rows}
            className="text-xs leading-snug"
          />
        ) : (
          <Input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="h-8 text-sm"
          />
        )
      ) : (
        <p
          className={cn(
            "leading-snug",
            displaySize === "sm" ? "text-sm font-medium" : "text-xs text-foreground/90",
            multiline && "line-clamp-3",
          )}
        >
          {value || <span className="text-muted-foreground italic">(empty)</span>}
        </p>
      )}
    </div>
  );
}

// Compact scene row with click-to-expand inline editor. When `editable`
// is false the row is purely display (read-only after approval).
function SceneRow({
  index, scene, editable, expanded, onToggle, onChange,
}: {
  index: number;
  scene: Scene;
  editable: boolean;
  expanded: boolean;
  onToggle: () => void;
  onChange: (patch: Partial<Scene>) => void;
}) {
  const isOpen = expanded && editable;
  return (
    <li className="rounded-md border border-border bg-card/50 overflow-hidden">
      <button
        type="button"
        onClick={editable ? onToggle : undefined}
        className={cn(
          "w-full text-left px-2.5 py-2 flex items-start gap-2.5",
          editable && "hover:bg-accent/30 transition-colors cursor-pointer",
          !editable && "cursor-default",
        )}
        aria-expanded={isOpen}
        aria-disabled={!editable}
      >
        <span className="text-[10px] font-mono font-semibold text-muted-foreground shrink-0 pt-0.5 tabular-nums w-5">
          {String(index + 1).padStart(2, "0")}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-[12px] font-semibold leading-tight truncate">
            {scene.caption || "(no caption)"}
          </p>
          <p className="text-[10px] text-muted-foreground line-clamp-2 leading-snug mt-0.5">
            {scene.narration}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0 pt-0.5">
          <span className="text-[10px] font-mono text-muted-foreground tabular-nums">
            {scene.duration_sec}s
          </span>
          {editable && (
            isOpen
              ? <ChevronUp className="h-3 w-3 text-muted-foreground" />
              : <Pencil className="h-3 w-3 text-muted-foreground" />
          )}
        </div>
      </button>
      {isOpen && (
        <div className="border-t border-border px-2.5 py-2 space-y-2 animate-fade-in bg-muted/20">
          <Field label="Caption" value={scene.caption} editable
            onChange={(v) => onChange({ caption: v })} />
          <Field label="Narration" value={scene.narration} editable multiline rows={2}
            onChange={(v) => onChange({ narration: v })} />
          <Field label="Image prompt" value={scene.image_prompt} editable multiline rows={2}
            onChange={(v) => onChange({ image_prompt: v })} />
          <Field label="Motion prompt" value={scene.motion_prompt} editable multiline rows={2}
            onChange={(v) => onChange({ motion_prompt: v })} />
          <div className="grid grid-cols-2 gap-2 items-end">
            <Field label="Duration (sec)" value={String(scene.duration_sec)} editable
              onChange={(v) => {
                const n = Number(v);
                if (Number.isFinite(n) && n >= 0) onChange({ duration_sec: n });
              }} />
          </div>
        </div>
      )}
    </li>
  );
}

// ---------- 3. Storyboard (Reference + Scenes) -----------------------------

function StoryboardTile({
  spec, referenceUrl, slots,
}: {
  spec: StoryboardSpec;
  referenceUrl: string | null;
  slots: SceneSlots[];
}) {
  const refReady = !!referenceUrl;
  const keyframeCount = slots.filter((s) => s.keyframeUrl).length;
  const allKeyframesReady = keyframeCount === spec.scenes.length && spec.scenes.length > 0;
  const status = allKeyframesReady ? "ready" : "active";
  return (
    <TileShell
      title="Storyboard"
      icon={Sparkles}
      status={status}
      width="extra"
      tileKey="storyboard"
      badge={
        allKeyframesReady
          ? <StatusPill tone="green">ready</StatusPill>
          : <StatusPill tone="active" dot>painting</StatusPill>
      }
      meta={META.scenes}
    >
      <div className="grid grid-cols-[140px_1fr] gap-3 h-full">
        {/* Reference image — left column */}
        <div className="flex flex-col gap-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Reference</p>
          <div className="aspect-square w-full overflow-hidden rounded-md border bg-muted">
            {referenceUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={referenceUrl}
                alt="Style reference"
                className="h-full w-full object-cover animate-pop-in"
              />
            ) : (
              <CellLoader variant="stars" />
            )}
          </div>
          <Badge variant="outline" className="text-[10px] self-start">
            {refReady ? "B0 ✓" : "B0 …"}
          </Badge>
          <p className="text-[9px] text-muted-foreground leading-tight">
            {META.ref.provider}
          </p>
        </div>
        {/* Scene keyframes — right column, 2-col grid */}
        <div className="flex flex-col gap-1.5 min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Scenes ({keyframeCount}/{spec.scenes.length})
          </p>
          <ol className="grid grid-cols-2 gap-1.5 flex-1 content-start">
            {spec.scenes.map((scene, i) => (
              <li key={i} className="aspect-video overflow-hidden rounded-md border bg-muted relative">
                {slots[i]?.keyframeUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={slots[i].keyframeUrl}
                    alt=""
                    className="h-full w-full object-cover animate-pop-in"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center">
                    <GeneratingLoader size="sm" variant="flames" />
                  </div>
                )}
                <span className="absolute bottom-1 right-1 text-[9px] font-mono text-white bg-black/50 rounded px-1">
                  {scene.duration_sec}s
                </span>
                <span className="absolute top-1 left-1 text-[9px] font-mono text-white bg-black/50 rounded px-1">
                  {i + 1}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </TileShell>
  );
}

// ---------- 4. Media (Video + TTS + Music) ---------------------------------

// One-shot load-error guard for media elements. A presigned-URL expiry or a
// deleted/lifecycle-expired asset makes the <video>/<audio>/<img> fail; we swap
// to a placeholder ONCE rather than retrying (a cache-buster re-fetch would
// loop forever on a genuine 404). `errored` latches true on the first onError.
function useLoadError() {
  const [errored, setErrored] = useState(false);
  return { errored, onError: () => setErrored(true) };
}

function MediaUnavailable({ label }: { label: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center bg-muted/50">
      <span className="text-[9px] italic text-muted-foreground/60">{label}</span>
    </div>
  );
}

function SceneClip({ src }: { src: string }) {
  const { errored, onError } = useLoadError();
  if (errored) return <MediaUnavailable label="clip unavailable" />;
  return (
    <video
      src={src}
      onError={onError}
      muted playsInline preload="metadata" controls
      className="h-full w-full object-cover animate-pop-in"
    />
  );
}

function KeyframeStill({ src, ghosted }: { src: string; ghosted: boolean }) {
  const { errored, onError } = useLoadError();
  if (errored) return <MediaUnavailable label="visual unavailable" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      onError={onError}
      className={cn("h-full w-full object-cover", ghosted ? "opacity-40" : "animate-pop-in")}
    />
  );
}

function PlayableAudio({ src, className }: { src: string; className?: string }) {
  const { errored, onError } = useLoadError();
  if (errored) {
    return <span className="text-[9px] italic text-muted-foreground/60">audio unavailable</span>;
  }
  return <audio src={src} onError={onError} controls preload="none" className={className} />;
}

function MediaTile({
  spec, slots, musicUrl, musicFailed, done,
}: {
  spec: StoryboardSpec;
  slots: SceneSlots[];
  musicUrl: string | null;
  // Live failure flag for the music step — flips the score to "unavailable"
  // the moment the step fails, not only at `done`.
  musicFailed: boolean;
  // `done` = the whole run finished. Narration + music are best-effort, so
  // readiness keys off the essential video clips; once `done` (or a step has
  // failed live), an empty audio/video slot renders its fallback state rather
  // than a perpetual loader.
  done: boolean;
}) {
  const clipsReady = slots.filter((s) => s.clipUrl).length;
  const total = spec.scenes.length;
  const allDone = clipsReady === total;
  return (
    <TileShell
      title="Video + TTS + Music"
      icon={Film}
      status={allDone ? "ready" : "active"}
      width="extra"
      tileKey="media"
      badge={
        allDone
          ? <StatusPill tone="green">ready</StatusPill>
          : <StatusPill tone="active" dot>{clipsReady}/{total} clips</StatusPill>
      }
      meta={META.video}
    >
      <div className="space-y-2 h-full overflow-y-auto pr-1 max-h-[460px]">
        {/* Per-scene clip + narration row */}
        <ol className="grid grid-cols-3 gap-1.5">
          {spec.scenes.map((scene, i) => {
            const slot = slots[i] ?? {};
            // The video clip has settled (fell back to the keyframe still) when
            // the run is done OR this scene's video step failed live.
            const videoSettled = done || !!slot.videoFailed;
            const narrationSettled = done || !!slot.narrationFailed;
            return (
              <li key={i} className="rounded-md border bg-muted/30 p-1.5 space-y-1">
                <div className="aspect-video overflow-hidden rounded bg-muted">
                  {slot.clipUrl ? (
                    <SceneClip src={slot.clipUrl} />
                  ) : slot.keyframeUrl ? (
                    <div className="relative h-full w-full">
                      {/* While Decart renders, the keyframe is ghosted under a
                          loader. Once the clip has settled with no video, the
                          step fell back to this keyframe still — show it at full
                          opacity as the scene's final visual (no loader). */}
                      <KeyframeStill src={slot.keyframeUrl} ghosted={!videoSettled} />
                      {!videoSettled && (
                        <div className="absolute inset-0 flex items-center justify-center">
                          <GeneratingLoader size="sm" variant="flames" />
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex h-full items-center justify-center">
                      <GeneratingLoader size="sm" variant="flames" />
                    </div>
                  )}
                </div>
                {slot.narrationUrl ? (
                  <PlayableAudio src={slot.narrationUrl} className="w-full h-6" />
                ) : narrationSettled ? (
                  // Best-effort narration fell back — state it, don't spin.
                  <div className="flex items-center h-6">
                    <span className="text-[9px] italic text-muted-foreground/60">narration unavailable</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-1.5 h-6">
                    <GeneratingLoader size="sm" variant="stars" />
                    <span className="text-[9px] italic text-muted-foreground/80">TTS pending</span>
                  </div>
                )}
                <p className="text-[9px] text-muted-foreground line-clamp-1">{scene.caption}</p>
              </li>
            );
          })}
        </ol>

        {/* Per-step metadata — three slugs side by side. */}
        <div className="grid grid-cols-3 gap-1.5 text-[9px] text-muted-foreground">
          <div className="font-mono truncate" title={META.video.model}>video · {META.video.model}</div>
          <div className="font-mono truncate" title={META.tts.model}>TTS · {META.tts.model}</div>
          <div className="font-mono truncate" title={META.music.model}>score · {META.music.model}</div>
        </div>

        {/* Music track */}
        <div className="rounded-md border bg-muted/30 p-2 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              <MusicIcon className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-xs font-medium">Score</span>
            </div>
            {musicUrl ? (
              <StatusPill tone="green">ready</StatusPill>
            ) : done || musicFailed ? (
              <StatusPill tone="neutral">unavailable</StatusPill>
            ) : (
              <StatusPill tone="active" dot>composing</StatusPill>
            )}
          </div>
          <p className="text-[10px] text-muted-foreground line-clamp-1">{spec.music_prompt}</p>
          {musicUrl ? (
            <PlayableAudio src={musicUrl} className="w-full h-6 animate-pop-in" />
          ) : done || musicFailed ? (
            // Best-effort score fell back — final MP4 is silent on music.
            <div className="flex items-center justify-center h-8 rounded bg-muted/50">
              <span className="text-[10px] italic text-muted-foreground/60">score unavailable</span>
            </div>
          ) : (
            <div className="flex items-center justify-center h-8 rounded bg-muted/50">
              <GeneratingLoader size="sm" variant="stars" />
            </div>
          )}
        </div>
      </div>
    </TileShell>
  );
}

// ---------- 5. Composition (Final MP4) -------------------------------------

function CompositionTile({ asset, runId }: { asset: Asset; runId: string | null }) {
  // One-shot guard: if the composed MP4 won't load (e.g. a stale URL), show a
  // placeholder pointing at /files rather than a black broken-video box.
  const { errored, onError } = useLoadError();
  return (
    <TileShell
      title="Composition"
      icon={Film}
      status="ready"
      width="wide"
      tileKey="composition"
      badge={<StatusPill tone="green">complete</StatusPill>}
      meta={META.compose}
      className="tile-celebrate"
    >
      <div className="space-y-2">
        {errored ? (
          <div className="flex aspect-video w-full items-center justify-center rounded-md border bg-muted/50">
            <span className="text-[11px] italic text-muted-foreground/70">
              Couldn&apos;t load the video — see <a href="/files" className="underline">files</a>.
            </span>
          </div>
        ) : (
          <video
            src={asset.url}
            onError={onError}
            controls
            className="aspect-video w-full rounded-md border bg-black animate-pop-in"
          />
        )}
        <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
          <span className="font-mono truncate" title={runId ?? ""}>
            run · {runId?.slice(0, 8) ?? "—"}
          </span>
          <a href="/files" className="underline hover:text-foreground">All artifacts →</a>
        </div>
      </div>
    </TileShell>
  );
}

// Suppress unused-icon lint — kept around for future tile additions.
export const _icons = { ImageIcon };
