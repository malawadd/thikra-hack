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
  ArrowRight, FileText, Sparkles, Film, Music as MusicIcon,
  Pencil, ChevronUp, Maximize2, FileJson,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { StatusPill } from "@/components/ui/status-pill";
import { GeneratingLoader } from "@/components/ui/generating-loader";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { PromptForm } from "@/components/genblaze/prompt-form";
import { cn } from "@/lib/utils";
import { playbackUrl } from "@/lib/api-client";
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
  manifestUri: string | null;
  generating: boolean;
  onSubmit: (prompt: string) => void;
  onRestart: () => void;
  onStartMedia: () => void;
}

// Centralised "what runs where" — the source-of-truth metadata strip
// that every tile renders under its content. Mirrors services/api/app/
// repo/pipelines.py's stage layout.
const META = {
  seed:    { model: "—",                            provider: "—" },
  script:  { model: "gpt-4.1-nano",                 provider: "OpenAI chat() · response_format" },
  ref:     { model: "imagen-4.0-generate-001", provider: "Google · ImagenProvider" },
  scenes:  { model: "imagen-4.0-generate-001", provider: "Google · ImagenProvider" },
  video:   { model: "Kling-Image2Video-V2.1-Master", provider: "GMICloud · GMICloudVideoProvider" },
  tts:     { model: "nvidia/magpie-tts-multilingual", provider: "NVIDIA · NvidiaAudioProvider" },
  music:   { model: "minimax-music-2.5",            provider: "GMICloud · GMICloudAudioProvider" },
  compose: { model: "ffmpeg",                       provider: "local subprocess (composer.py)" },
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

  // Tracks the last time the user touched the canvas (wheel, drag, touch).
  // The auto-snap respects this — if the user is actively scrolling, we
  // don't yank them back to the active tile. This is what makes manual
  // left/right scrolling actually usable while the run is in flight.
  const lastUserScrollAt = useRef(0);
  const isProgrammaticScroll = useRef(false);

  // Wire the canvas's manual-scroll affordances: wheel events convert
  // vertical scroll to horizontal scroll (mouse users without trackpad
  // gestures can still navigate), and any user gesture marks
  // `lastUserScrollAt` so the auto-snap below stays out of the way.
  //
  // CAPTURE PHASE is critical here. In bubble phase the wheel event has
  // already reached descendants — if a tile has its own `overflow-y-auto`
  // (e.g. ScriptTile's scene list) OR if any ancestor like `<main>` can
  // scroll vertically, the browser scrolls THAT element first, so the
  // user sees vertical scroll instead of horizontal canvas pan. Capture
  // phase fires BEFORE the descendant/ancestor scrolls, so preventDefault
  // here actually stops the wrong scroll from happening.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const mark = () => { lastUserScrollAt.current = Date.now(); };

    const onScroll = () => {
      if (isProgrammaticScroll.current) return;
      mark();
    };

    const onWheel = (e: WheelEvent) => {
      mark();
      // Horizontal-dominant gestures (trackpad swipe) pass through to
      // the canvas's native horizontal scroll — don't touch them.
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      // No horizontal range = nothing to do; let the page scroll normally.
      if (el.scrollWidth <= el.clientWidth + 1) return;
      // Otherwise: claim the vertical wheel for the canvas. preventDefault
      // (in capture phase) stops the page AND any inner tile from scrolling
      // vertically; we manually map deltaY → scrollLeft so the pipeline
      // pans horizontally regardless of where the cursor is.
      e.preventDefault();
      el.scrollLeft += e.deltaY;
    };

    // capture: true → fire BEFORE any descendant or default scroll.
    el.addEventListener("wheel", onWheel, { passive: false, capture: true });
    el.addEventListener("touchstart", mark, { passive: true });
    el.addEventListener("pointerdown", mark, { passive: true });
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("wheel", onWheel, { capture: true } as EventListenerOptions);
      el.removeEventListener("touchstart", mark);
      el.removeEventListener("pointerdown", mark);
      el.removeEventListener("scroll", onScroll);
    };
  }, []);

  // Center the active tile horizontally INSIDE the canvas whenever it
  // changes. Uses `scrollTo` on the canvas itself (not `scrollIntoView`
  // on the child) so the surrounding page never scrolls — fixes the
  // "first module gets covered" bug where scrollIntoView would tug the
  // viewport vertically and bring the sidebar back into view over the
  // Seed tile. Skips the snap if the user has scrolled in the last 4s
  // so manual exploration isn't fought.
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    if (Date.now() - lastUserScrollAt.current < 4000) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    requestAnimationFrame(() => {
      const el = root.querySelector<HTMLElement>(`[data-tile-key="${activeTile}"]`);
      if (!el || !root) return;
      // Compute the scrollLeft that centres `el` inside `root` without
      // affecting any ancestor scroll position.
      const targetLeft = el.offsetLeft - (root.clientWidth - el.offsetWidth) / 2;
      const max = Math.max(0, root.scrollWidth - root.clientWidth);
      const left = Math.max(0, Math.min(targetLeft, max));
      isProgrammaticScroll.current = true;
      root.scrollTo({ left, behavior: reduced ? "auto" : "smooth" });
      // Clear the flag once the smooth scroll has had time to settle.
      // 500ms covers a typical smooth-scroll duration; if a manual scroll
      // races in during this window, it still beats programmatic intent
      // because we don't reset on a per-event basis.
      window.setTimeout(() => { isProgrammaticScroll.current = false; }, 500);
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
      {/* items-start (not items-stretch) so tall tiles like the Storyboard
          don't force shorter tiles (Composition, Seed-after-submit) to
          stretch with empty space. Each tile sits at its natural height. */}
      <ol className="flex items-start gap-3 min-w-max pr-6">
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
        {showComp && <CompositionTile asset={p.finalAsset!} runId={p.runId} manifestUri={p.manifestUri} />}
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
  /** Extra class on the outer element — e.g. `tile-celebrate` on Composition. */
  className?: string;
  /** Stable stage identifier used by the canvas's auto-centering scroll. */
  tileKey?: TileKey;
  /** Render as `<li>` (default, for top-level pipeline items) or `<div>`
   *  (for nested sub-cards inside a media stack — avoids invalid <li>-in-<li>). */
  as?: "li" | "div";
  /** Override the explicit width — useful for sub-cards that inherit
   *  width from their parent container. */
  fullWidth?: boolean;
}

function TileShell({
  title, icon: Icon, status, width = "narrow", children, badge, meta, className, tileKey,
  as = "li", fullWidth = false,
}: TileShellProps) {
  const w = fullWidth
    ? "w-full"
    : width === "extra"
      ? "w-[480px]"
      : width === "wide"
        ? "w-[360px]"
        : "w-[300px]";
  // Provider string in META is "Vendor · ClassName" (e.g. "OpenAI · DalleProvider").
  // Take just the vendor for the compact header chip; the full string lives
  // in the title attribute (hover tooltip) so it's still discoverable.
  const providerShort = meta?.provider.split(" · ")[0];
  const Outer = (as === "div" ? "div" : "li") as React.ElementType;
  return (
    <Outer
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
    </Outer>
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
        {/* Scene keyframes — vertical stack of "thumbnail on top, caption
            below". The thumbnail is the dominant element (full column
            width, h-24); the caption sits underneath in a compact strip.
            No internal scrollbar — the tile grows to fit (the canvas row
            uses `items-start` so other tiles don't stretch to match). */}
        <div className="flex flex-col gap-1.5 min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Scenes ({keyframeCount}/{spec.scenes.length})
          </p>
          <ol className="flex flex-col gap-2 flex-1">
            {spec.scenes.map((scene, i) => (
              <li
                key={i}
                className="rounded-md border bg-muted/30 overflow-hidden shrink-0"
              >
                {/* Thumbnail — the focal element. Full column width,
                    fixed height so the image stays the visual lead. */}
                <div className="relative h-24 w-full bg-muted">
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
                  <span className="absolute top-1 left-1 text-[10px] font-mono font-semibold text-white bg-black/60 rounded px-1.5 py-0.5">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="absolute top-1 right-1 text-[10px] font-mono text-white bg-black/60 rounded px-1.5 py-0.5">
                    {scene.duration_sec}s
                  </span>
                </div>
                {/* Caption strip — sits BELOW the thumbnail (not overlaid). */}
                <div className="px-2 py-1.5">
                  <p className="text-[11px] font-medium leading-tight line-clamp-1">
                    {scene.caption || "(no caption)"}
                  </p>
                </div>
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
  musicFailed: boolean;
  done: boolean;
}) {
  // Stage B2 runs three providers in parallel: image-to-video (Decart),
  // narration TTS (NVIDIA), and the music score (GMICloud). Each gets its
  // own card so the user sees the per-provider state at a glance + can
  // tell at a glance which one is the slowest / which one fell back.
  // The cards stack vertically inside a single horizontal-canvas slot.
  return (
    <li
      data-tile-key="media"
      className="tile-in shrink-0 w-[480px] flex flex-col gap-3"
    >
      <VideoStackCard spec={spec} slots={slots} done={done} />
      <TtsStackCard spec={spec} slots={slots} done={done} />
      <MusicStackCard
        spec={spec}
        musicUrl={musicUrl}
        musicFailed={musicFailed}
        done={done}
      />
    </li>
  );
}

function VideoStackCard({
  spec, slots, done,
}: { spec: StoryboardSpec; slots: SceneSlots[]; done: boolean }) {
  const clipsReady = slots.filter((s) => s.clipUrl).length;
  const total = spec.scenes.length;
  const allReady = clipsReady === total;
  const anyFailed = slots.some((s) => s.videoFailed);
  return (
    <TileShell
      as="div"
      fullWidth
      title="Video"
      icon={Film}
      status={allReady ? "ready" : anyFailed && done ? "failed" : "active"}
      meta={META.video}
      badge={
        allReady
          ? <StatusPill tone="green">ready</StatusPill>
          : done
            ? <StatusPill tone="neutral">{clipsReady}/{total} clips</StatusPill>
            : <StatusPill tone="active" dot>{clipsReady}/{total} clips</StatusPill>
      }
    >
      <ol className="grid grid-cols-3 gap-1.5">
        {spec.scenes.map((scene, i) => {
          const slot = slots[i] ?? {};
          const settled = done || !!slot.videoFailed;
          return (
            <li key={i} className="rounded-md border bg-muted/20 p-1 space-y-0.5">
              <div className="aspect-video overflow-hidden rounded bg-muted">
                {slot.clipUrl ? (
                  <SceneClip src={slot.clipUrl} />
                ) : slot.keyframeUrl ? (
                  <div className="relative h-full w-full">
                    <KeyframeStill src={slot.keyframeUrl} ghosted={!settled} />
                    {!settled && (
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
              <p className="text-[9px] text-muted-foreground line-clamp-1">{scene.caption}</p>
            </li>
          );
        })}
      </ol>
    </TileShell>
  );
}

function TtsStackCard({
  spec, slots, done,
}: { spec: StoryboardSpec; slots: SceneSlots[]; done: boolean }) {
  const ready = slots.filter((s) => s.narrationUrl).length;
  const total = spec.scenes.length;
  const allReady = ready === total;
  return (
    <TileShell
      as="div"
      fullWidth
      title="TTS narration"
      icon={MusicIcon}
      status={allReady ? "ready" : "active"}
      meta={META.tts}
      badge={
        allReady
          ? <StatusPill tone="green">ready</StatusPill>
          : done
            ? <StatusPill tone="neutral">{ready}/{total} narrations</StatusPill>
            : <StatusPill tone="active" dot>{ready}/{total} narrations</StatusPill>
      }
    >
      <ol className="grid grid-cols-3 gap-1.5">
        {spec.scenes.map((scene, i) => {
          const slot = slots[i] ?? {};
          const settled = done || !!slot.narrationFailed;
          return (
            <li key={i} className="rounded-md border bg-muted/20 p-1 space-y-1">
              <p className="text-[9px] font-mono text-muted-foreground tabular-nums">
                {String(i + 1).padStart(2, "0")} · {scene.duration_sec}s
              </p>
              {slot.narrationUrl ? (
                <PlayableAudio src={slot.narrationUrl} className="w-full h-6 animate-pop-in" />
              ) : settled ? (
                <div className="h-6 flex items-center">
                  <span className="text-[9px] italic text-muted-foreground/60">
                    narration unavailable
                  </span>
                </div>
              ) : (
                <div className="h-6 flex items-center justify-center rounded bg-muted/50">
                  <GeneratingLoader size="sm" variant="stars" />
                </div>
              )}
              <p className="text-[9px] text-muted-foreground line-clamp-2 leading-snug">
                {scene.narration}
              </p>
            </li>
          );
        })}
      </ol>
    </TileShell>
  );
}

function MusicStackCard({
  spec, musicUrl, musicFailed, done,
}: {
  spec: StoryboardSpec;
  musicUrl: string | null;
  musicFailed: boolean;
  done: boolean;
}) {
  const settled = done || musicFailed;
  return (
    <TileShell
      as="div"
      fullWidth
      title="Score"
      icon={MusicIcon}
      status={musicUrl ? "ready" : settled ? "failed" : "active"}
      meta={META.music}
      badge={
        musicUrl
          ? <StatusPill tone="green">ready</StatusPill>
          : settled
            ? <StatusPill tone="neutral">unavailable</StatusPill>
            : <StatusPill tone="active" dot>composing</StatusPill>
      }
    >
      <p className="text-[10px] text-muted-foreground line-clamp-1 mb-1.5">
        {spec.music_prompt}
      </p>
      {musicUrl ? (
        <PlayableAudio src={musicUrl} className="w-full h-7 animate-pop-in" />
      ) : settled ? (
        <div className="flex items-center justify-center h-9 rounded bg-muted/40">
          <span className="text-[10px] italic text-muted-foreground/60">
            score unavailable
          </span>
        </div>
      ) : (
        <div className="flex items-center justify-center h-9 rounded bg-muted/40">
          <GeneratingLoader size="sm" variant="stars" />
        </div>
      )}
    </TileShell>
  );
}

// ---------- 5. Composition (Final MP4) -------------------------------------

function CompositionTile({
  asset, runId, manifestUri,
}: {
  asset: Asset;
  runId: string | null;
  manifestUri: string | null;
}) {
  // One-shot guard: if the composed MP4 won't load (e.g. a stale URL), show a
  // placeholder pointing at /files rather than a black broken-video box.
  const { errored, onError } = useLoadError();
  const videoSrc = playbackUrl(asset.url);
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
          // Route the durable B2 URL through `/assets/{key}` like every other
          // media element — a bare durable URL 403s on a private bucket.
          <video
            src={videoSrc}
            onError={onError}
            controls
            className="aspect-video w-full rounded-md border bg-black animate-pop-in"
          />
        )}

        {/* Action row — Fullscreen + Manifest dialogs. Both open shadcn
            Dialogs that overlay the page so the rest of the canvas + state
            stays available behind them. */}
        <div className="flex items-center gap-1.5 pt-1">
          <FullscreenVideoDialog src={videoSrc} />
          <ManifestDialog manifestUri={manifestUri} runId={runId} />
        </div>

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

function FullscreenVideoDialog({ src }: { src: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 px-2 gap-1 text-[11px]">
          <Maximize2 className="h-3 w-3" />
          Fullscreen
        </Button>
      </DialogTrigger>
      <DialogContent
        // 95vw cap keeps a thin gutter on ultra-wide displays so the
        // backdrop click-area remains discoverable.
        className="!max-w-[95vw] !w-[95vw] p-0 overflow-hidden bg-black border-border"
        showCloseButton
      >
        <DialogTitle className="sr-only">Final explainer — fullscreen playback</DialogTitle>
        <DialogDescription className="sr-only">
          Maximised view of the composed MP4. Press Escape to close.
        </DialogDescription>
        <video
          src={src}
          controls
          autoPlay
          className="w-full max-h-[88vh] object-contain bg-black"
        />
      </DialogContent>
    </Dialog>
  );
}

function ManifestDialog({
  manifestUri, runId,
}: { manifestUri: string | null; runId: string | null }) {
  const [open, setOpen] = useState(false);
  const [json, setJson] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Fetch lazily on first open — manifest JSON is small but we don't need
  // to hit B2 until the user actually wants it. The whole side-effect is
  // gated inside an async IIFE so we don't trigger React's
  // set-state-in-effect lint (synchronous setState during render).
  useEffect(() => {
    if (!open || json !== null || error !== null) return;
    let cancelled = false;
    (async () => {
      if (!manifestUri) {
        if (!cancelled) setError("Manifest URI was not provided by the backend for this run.");
        return;
      }
      setLoading(true);
      try {
        // `inline=1`: proxy the JSON through the API (same-origin) instead of
        // 302-ing into B2's presigned URL, which fetch() can't read (no CORS).
        const r = await fetch(`${playbackUrl(manifestUri)}?inline=1`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const text = await r.text();
        if (cancelled) return;
        // Pretty-print if it parses, otherwise show raw.
        try { setJson(JSON.stringify(JSON.parse(text), null, 2)); }
        catch { setJson(text); }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open, manifestUri, json, error]);

  const onCopy = () => {
    if (!json) return;
    navigator.clipboard?.writeText(json);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-7 px-2 gap-1 text-[11px]"
          disabled={!manifestUri}
          title={manifestUri ? "View Stage B2 Manifest JSON" : "No manifest available"}
        >
          <FileJson className="h-3 w-3" />
          Manifest
        </Button>
      </DialogTrigger>
      <DialogContent className="!max-w-3xl p-0 overflow-hidden" showCloseButton>
        <DialogHeader className="border-b border-border px-5 py-3.5">
          <DialogTitle className="flex items-center gap-2 text-base">
            <FileJson className="h-4 w-4 text-muted-foreground" />
            Run manifest
            {runId && (
              <span className="status-pill font-mono text-[10px]">
                {runId.slice(0, 8)}
              </span>
            )}
          </DialogTitle>
          <DialogDescription className="text-xs">
            Genblaze writes one Manifest per pipeline run. The JSON below
            captures the pipeline name, the parent_run_id (B0 → B1 → B2
            lineage), every step&apos;s model + provider + asset URL, and the
            canonical hash used for verification.
          </DialogDescription>
        </DialogHeader>
        <div className="p-5 space-y-3 max-h-[70vh] overflow-y-auto">
          {loading && (
            <div className="flex h-32 items-center justify-center">
              <GeneratingLoader size="md" variant="stars" label="Loading manifest" />
            </div>
          )}
          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              Couldn&apos;t load the manifest: <span className="font-mono">{error}</span>
            </div>
          )}
          {json && (
            <>
              <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground font-mono">
                <span>{json.split("\n").length} lines · {json.length.toLocaleString()} chars</span>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={onCopy}
                    className="px-1.5 py-0.5 rounded border border-border hover:bg-accent/40 transition-colors"
                  >
                    Copy
                  </button>
                  {manifestUri && (
                    <a
                      href={playbackUrl(manifestUri)}
                      target="_blank"
                      rel="noreferrer"
                      className="px-1.5 py-0.5 rounded border border-border hover:bg-accent/40 transition-colors"
                    >
                      Raw ↗
                    </a>
                  )}
                </div>
              </div>
              <pre className="text-[11px] font-mono leading-snug whitespace-pre-wrap break-all rounded-md border border-border bg-muted/40 p-3 max-h-[55vh] overflow-y-auto">
                {json}
              </pre>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
