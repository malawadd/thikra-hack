"use client";

import { AlertTriangle } from "lucide-react";
import { AlertBanner } from "@/components/ui/alert-banner";
import { useHealth } from "@/lib/queries";

/**
 * Pre-flight advisory — surfaces missing provider keys / ffmpeg BEFORE a run so
 * the user knows what will degrade (or fail) ahead of spending minutes of paid
 * generation. Advisory only: it never disables the CTA — `/health` is polled
 * on an interval (so it can be ~60s stale), and the backend already fails Stage
 * A cheaply on a missing OpenAI key while B2 falls back gracefully on missing
 * audio/video keys. B2-not-connected is covered by the global `HealthBanner`.
 */
export function ReadinessNotice() {
  const { data } = useHealth();
  if (!data) return null;

  const p = data.providers;
  const issues: string[] = [];
  if (!p.openai_key_present)
    issues.push("OpenAI key missing — the storyboard and keyframes can't be generated.");
  if (!data.ffmpeg_present)
    issues.push("ffmpeg isn't installed on the API host — the final MP4 can't be composed (source assets are still saved to B2).");
  if (!p.decart_key_present)
    issues.push("Decart key missing — video falls back to the keyframe stills.");
  if (!p.nvidia_key_present)
    issues.push("NVIDIA key missing — scenes will have no narration.");
  if (!p.gmi_key_present)
    issues.push("GMICloud key missing — the video will have no music.");

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
