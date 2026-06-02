"use client";

import { AlertTriangle } from "lucide-react";
import { useHealth } from "@/lib/queries";
import { AlertBanner } from "@/components/ui/alert-banner";

/**
 * Shows a top-of-app warning when the API is up but B2 itself is
 * misconfigured (`b2_connected: false`) — the case where individual fetches
 * succeed (returning empty/stale data) and the per-component ErrorState would
 * never fire.
 *
 * Consumes the SHARED `useHealth()` query (same key as `ReadinessNotice`), so
 * the app makes ONE `/health` poll regardless of how many components watch it
 * — each `/health` does a live blocking B2 probe in the API threadpool, so a
 * duplicate poller would double that load for nothing. When the API is down,
 * `data` is `undefined` and the banner stays silent (the per-component
 * `ErrorState` explains the outage instead).
 */
export function HealthBanner() {
  const { data } = useHealth();

  if (!data || data.b2_connected) return null;

  // Full-bleed strip at the top of the app — same alert language as the
  // in-content panels, just with the rounding/side-borders stripped.
  return (
    <AlertBanner
      tone="warning"
      icon={AlertTriangle}
      title="B2 not connected."
      className="rounded-none border-x-0 border-t-0 py-2"
    >
      The API is running but can&apos;t reach Backblaze. Check your{" "}
      <code className="font-mono text-[11px]">.env</code> credentials and bucket
      region, then restart the API.
    </AlertBanner>
  );
}
