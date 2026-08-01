"use client";

import { useEffect, useMemo, useRef } from "react";
import { Activity } from "lucide-react";
import { StatusPill, type PillTone } from "@/components/ui/status-pill";
import { lookupModel } from "@/lib/models";
import { humanizeDuration } from "@/lib/utils";
import type { SseFrame } from "@/types/pipeline";

type StepState = {
  index: number;
  model?: string;
  status: "pending" | "processing" | "succeeded" | "failed";
  startedAt?: string;
  completedAt?: string;
  progress?: number;
  error?: string;
};

// Stages this sample emits, in execution order. Matches the backend's
// `stage` field on every SSE frame.
const STAGES: { id: string; title: string; placeholder: string }[] = [
  { id: "A.storyboard", title: "Stage A · Storyboard", placeholder: "Storyboard is generated once the prompt is submitted." },
  { id: "B0.reference", title: "Stage B0 · Style reference", placeholder: "One reference image renders before per-scene keyframes." },
  { id: "B1.keyframes", title: "Stage B1 · Keyframes", placeholder: "Image fan-out runs after the reference image lands." },
  { id: "B2.media",     title: "Stage B2 · Video + TTS + Music", placeholder: "Image-to-video, narration, and music run in parallel." },
  { id: "C.compose",    title: "Stage C · Composition", placeholder: "ffmpeg concat + mix + caption burn-in runs locally." },
];

const EVENT_LABEL: Record<string, string> = {
  "pipeline.started":   "Pipeline started",
  "pipeline.completed": "Pipeline completed",
  "pipeline.failed":    "Pipeline failed",
  "step.started":       "Step started",
  "step.progress":      "Step progress",
  "step.completed":     "Step completed",
  "step.failed":        "Step failed",
};

function statusTone(status: StepState["status"]): PillTone {
  switch (status) {
    case "processing": return "active";
    case "succeeded":  return "green";
    case "failed":     return "red";
    case "pending":    return "neutral";
  }
}

// Reduce SSE stream events scoped to one stage into a step-indexed state map.
function buildSteps(frames: SseFrame[], stageId: string): StepState[] {
  const map = new Map<number, StepState>();
  for (const f of frames) {
    if (f.kind !== "stream" || f.stage !== stageId) continue;
    const ev = f.event;
    const idx = (ev as { step_index?: number }).step_index;
    if (idx === undefined) continue;
    const prior = map.get(idx) ?? { index: idx, status: "pending" as const };
    const next: StepState = { ...prior };
    const t = ev.type;
    if (t === "step.started") {
      next.status = "processing";
      next.startedAt = (ev as { timestamp?: string }).timestamp;
      next.model = (ev as { model?: string }).model ?? next.model;
    } else if (t === "step.progress") {
      next.status = "processing";
      const pct = (ev as { progress_pct?: number }).progress_pct;
      if (typeof pct === "number") next.progress = pct;
    } else if (t === "step.completed") {
      next.status = "succeeded";
      next.completedAt = (ev as { timestamp?: string }).timestamp;
    } else if (t === "step.failed") {
      next.status = "failed";
      next.completedAt = (ev as { timestamp?: string }).timestamp;
      next.error = (ev as { error?: string }).error ?? undefined;
    }
    map.set(idx, next);
  }
  return [...map.values()].sort((a, b) => a.index - b.index);
}

// Map a stage to a top-level status pill based on its own frames.
function stageStatus(frames: SseFrame[], stageId: string): {
  tone: PillTone; label: string; dot?: boolean;
} {
  const stageFrames = frames.filter((f) => "stage" in f && f.stage === stageId);
  if (stageFrames.length === 0) return { tone: "neutral", label: "Pending" };
  const hasError = stageFrames.some((f) => f.kind === "error");
  if (hasError) return { tone: "red", label: "Failed" };
  const complete = stageFrames.some((f) => f.kind === "stage.complete");
  if (complete) return { tone: "green", label: "Done" };
  return { tone: "active", label: "Running", dot: true };
}

export function PipelineInspector({
  frames,
  isLive,
}: {
  frames: SseFrame[];
  isLive: boolean;
}) {
  // Memoise per-stage step lists so the table doesn't re-reduce on every render.
  const stageSteps = useMemo(
    () => Object.fromEntries(STAGES.map((s) => [s.id, buildSteps(frames, s.id)])),
    [frames],
  );

  // Tail event log — last 20 stream-type events across all stages.
  const tail = useMemo(
    () =>
      frames
        .filter((f): f is Extract<SseFrame, { kind: "stream" }> => f.kind === "stream")
        .slice(-20),
    [frames],
  );

  // Auto-scroll the event log to the latest entry without scrolling the page.
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [tail.length]);

  return (
    <div className="rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <h3 className="card-title">Pipeline inspector</h3>
          <span className="text-xs text-muted-foreground hidden sm:inline">
            live SSE stream from <span className="font-mono">Pipeline.astream()</span>
          </span>
        </div>
        {isLive ? (
          <StatusPill tone="active" dot>Live</StatusPill>
        ) : (
          <StatusPill tone="neutral">Idle</StatusPill>
        )}
      </div>

      {frames.length === 0 && (
        <p className="px-4 py-4 text-xs text-muted-foreground">
          Pipeline events stream here once a run starts. Each Genblaze
          provider call emits step.started / step.progress / step.completed
          which are reduced into the per-stage tables below.
        </p>
      )}

      {frames.length > 0 && (
        <div className="space-y-5 p-4">
          {STAGES.map((stage) => {
            const steps = stageSteps[stage.id] ?? [];
            const status = stageStatus(frames, stage.id);
            return (
              <section key={stage.id} className="space-y-2 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                    {stage.title}
                  </p>
                  <StatusPill tone={status.tone} dot={status.dot}>
                    {status.label}
                  </StatusPill>
                </div>

                {steps.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">{stage.placeholder}</p>
                ) : (
                  steps.map((s) => {
                    const info = lookupModel(s.model);
                    const dur = humanizeDuration(s.startedAt, s.completedAt);
                    return (
                      <div
                        key={s.index}
                        className="inspector-step text-xs space-y-0.5"
                        data-status={s.status}
                      >
                        <span className="step-chip">{s.index}</span>
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium truncate" title={info.label}>
                            {info.label}
                          </span>
                          <StatusPill tone={statusTone(s.status)} dot={s.status === "processing"}>
                            {s.status}
                          </StatusPill>
                        </div>
                        <div className="text-muted-foreground flex items-center gap-2 flex-wrap">
                          <span>{info.provider}</span>
                          {s.status === "processing" && typeof s.progress === "number" && (
                            <>
                              <span>·</span>
                              <span>{Math.round(s.progress)}%</span>
                            </>
                          )}
                          {dur && (
                            <>
                              <span>·</span>
                              <span>{dur}</span>
                            </>
                          )}
                        </div>
                        {s.error && (
                          <p className="text-[11px] text-destructive font-mono break-all">{s.error}</p>
                        )}
                      </div>
                    );
                  })
                )}
              </section>
            );
          })}

          <div className="space-y-2 min-w-0">
            <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
              Event stream
            </p>
            <div
              ref={logRef}
              className="space-y-0.5 max-h-48 overflow-y-auto rounded-md bg-muted/40 p-2 font-mono text-[11px]"
            >
              {tail.map((f, i) => {
                const idx = (f.event as { step_index?: number }).step_index;
                const label = EVENT_LABEL[f.event.type] ?? f.event.type;
                return (
                  <div key={i} className="text-muted-foreground">
                    <span className="opacity-60">[{f.stage}]</span>{" "}
                    <span className="text-foreground/80">{label}</span>
                    {idx !== undefined && <span className="opacity-60"> · step {idx}</span>}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
