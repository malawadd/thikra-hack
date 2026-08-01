// Run durability — persist the studio's in-flight run to sessionStorage so an
// accidental reload restores the canvas instead of dropping to a blank idle
// state (and forfeiting a multi-minute, paid run).
//
// sessionStorage (not localStorage) is deliberate: a run belongs to the tab
// that started it. It should survive a refresh but NOT resurrect in a brand-new
// window days later. That's the exact lifetime we want for "don't lose my
// in-progress run to a stray Cmd-R".
//
// The media pipeline streams from a single request, so a reload mid-stream
// can't resume the stream itself — `StudioPage` restores the partial canvas and
// flips an interrupted run to a recoverable error (finished assets are already
// durable in B2). This module only owns read/write/clear of the snapshot.

import type { CanvasPhase } from "@/components/studio/pipeline-canvas";
import type { Selection } from "@/lib/api-client";
import type { Asset, SceneSlots } from "@/types/pipeline";
import type { StoryboardSpec } from "@/types/storyboard";

// Bump the version suffix whenever `PersistedRun` changes shape, so a snapshot
// written by an older build is ignored rather than deserialised into garbage.
const KEY = "gb.run.v1";

/** The slice of studio run state worth surviving a reload. The raw SSE
 *  `frames` log is intentionally excluded — it's a live-only inspector tail;
 *  the canvas is reconstructed from the durable slots/URLs below. */
export interface PersistedRun {
  phase: CanvasPhase;
  seed: string;
  spec: StoryboardSpec | null;
  sceneSlots: SceneSlots[];
  referenceUrl: string | null;
  musicUrl: string | null;
  musicFailed: boolean;
  finalAsset: Asset | null;
  runId: string | null;
  manifestUri: string | null;
  selection: Selection;
}

/** Persist the current run snapshot. Best-effort: a failed write (quota, or
 *  private-mode Safari where sessionStorage throws) just means no restore. */
export function saveRun(state: PersistedRun): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // Durability is best-effort; swallow storage errors.
  }
}

/** Read a persisted run, or null if none / unreadable / corrupt. */
export function loadRun(): PersistedRun | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as PersistedRun) : null;
  } catch {
    return null;
  }
}

/** Drop the snapshot — called when a run resets to idle. */
export function clearRun(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
