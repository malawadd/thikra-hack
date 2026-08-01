"use client";

import { AlertTriangle } from "lucide-react";
import { AlertBanner } from "@/components/ui/alert-banner";
import { useHealth, useProviders } from "@/lib/queries";
import { MODALITIES, vendorLabel, type Modality, type Selection } from "@/lib/api-client";

// Per-modality consequence when the SELECTED vendor's key is missing. Chat +
// image are essential (the run fails for $0 at preflight); video/tts/music are
// best-effort (the composer degrades).
const CONSEQUENCE: Record<Modality, string> = {
  chat: "the storyboard can't be generated",
  image: "keyframes can't be generated",
  video: "clips fall back to the keyframe stills",
  tts: "scenes will have no narration",
  music: "the video will have no score",
};

/**
 * Pre-flight advisory — surfaces missing keys for the CURRENTLY SELECTED
 * providers (+ ffmpeg) BEFORE a run, so the user knows what will degrade or
 * fail ahead of spending minutes of paid generation. Advisory only: it never
 * disables the CTA. B2-not-connected is covered by the global `HealthBanner`.
 */
export function ReadinessNotice({ selection }: { selection: Selection }) {
  const { data: health } = useHealth();
  const { data: matrix } = useProviders();
  if (!health) return null;

  const issues: string[] = [];
  // Warn per modality if the chosen vendor isn't configured.
  if (matrix) {
    for (const m of MODALITIES) {
      const vendor = selection[m].vendor;
      const opt = matrix[m]?.find((o) => o.vendor === vendor);
      if (opt && !opt.key_available) {
        issues.push(`${vendorLabel(vendor)} key missing — ${CONSEQUENCE[m]}.`);
      }
    }
  }
  if (!health.ffmpeg_present)
    issues.push("ffmpeg isn't installed on the API host — the final MP4 can't be composed (source assets are still saved to B2).");

  if (issues.length === 0) return null;

  return (
    <AlertBanner tone="warning" icon={AlertTriangle} title="Heads up before you generate">
      <ul className="list-disc space-y-0.5 pl-4">
        {issues.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </AlertBanner>
  );
}
