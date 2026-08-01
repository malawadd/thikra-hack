"use client";

// Bottom-docked Pipeline Inspector. Default collapsed to a 44px live
// status strip; click to expand to a 480px panel showing the full
// per-stage event tree.
//
// Why a drawer instead of the previous top-right pinned panel: the panel
// covered text and tiles in the horizontal pipeline canvas. Docking at
// the bottom keeps the canvas pristine; the drawer overlays nothing
// until the user explicitly expands it, and the canvas gets `pb-[60px]`
// so the collapsed strip is never on top of content either.

import { useState } from "react";
import { ChevronUp, ChevronDown, Activity } from "lucide-react";
import { cn } from "@/lib/utils";
import { StatusPill } from "@/components/ui/status-pill";
import { PipelineInspector } from "@/components/studio/pipeline-inspector";
import type { SseFrame } from "@/types/pipeline";

export function InspectorDrawer({
  frames, isLive,
}: { frames: SseFrame[]; isLive: boolean }) {
  const [open, setOpen] = useState(false);
  // Latest informative event for the collapsed strip — last stream event
  // wins, falling back to most recent stage marker.
  const last = [...frames].reverse().find((f) => f.kind === "stream" || f.kind === "stage.start" || f.kind === "stage.complete" || f.kind === "error");

  return (
    <div
      className={cn(
        // `fixed` lifts the drawer above the SidebarProvider's flex layout.
        // `inset-x-0` spans full viewport; on lg+ the sidebar overlaps the
        // left edge — that's intentional, the drawer stretches under it.
        "fixed inset-x-0 bottom-0 z-40 bg-card border-t border-border shadow-[0_-8px_24px_-12px_rgba(0,0,0,0.25)]",
        "transition-[height] duration-300 ease-out",
        open ? "h-[min(480px,60vh)]" : "h-[44px]",
      )}
      role="region"
      aria-label="Pipeline inspector"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-2.5 hover:bg-accent/40 transition-colors"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-semibold">Pipeline inspector</span>
          {isLive
            ? <StatusPill tone="active" dot>LIVE</StatusPill>
            : <StatusPill tone="neutral">idle</StatusPill>}
          <span className="text-xs text-muted-foreground hidden sm:inline">
            {frames.length} events
          </span>
          {!open && last && (
            <span className="text-xs text-muted-foreground truncate hidden md:inline">
              · last: {summariseFrame(last)}
            </span>
          )}
        </div>
        {open
          ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
          : <ChevronUp className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && (
        <div className="h-[calc(100%-44px)] overflow-y-auto animate-fade-in">
          <PipelineInspector frames={frames} isLive={isLive} />
        </div>
      )}
    </div>
  );
}

function summariseFrame(f: SseFrame): string {
  switch (f.kind) {
    case "stream":
      return `[${f.stage}] ${f.event.type ?? "event"}`;
    case "stage.start":
      return `[${f.stage}] stage starting`;
    case "stage.complete":
      return `[${f.stage}] stage complete`;
    case "error":
      return `[${f.stage}] error — ${f.message.slice(0, 60)}`;
    default:
      return "(unknown)";
  }
}
