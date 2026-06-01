"use client";

import { AlertTriangle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE } from "@/lib/api-client";
import { AlertBanner } from "@/components/ui/alert-banner";

interface HealthResponse {
  status: "healthy" | "degraded";
  b2_connected: boolean;
}

// 5s timeout — long enough for a sleepy dev API to respond, short enough
// that a hung backend doesn't keep the banner ambiguous for 30 seconds.
async function fetchHealth(): Promise<HealthResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, {
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) return null;
    return (await res.json()) as HealthResponse;
  } catch {
    // API is down. The per-component <ErrorState> will explain that;
    // returning null here keeps the banner silent rather than stacking
    // two error surfaces.
    return null;
  }
}

/**
 * Shows a top-of-app warning when the API is up but B2 itself is
 * misconfigured (`b2_connected: false`) — the case where individual
 * fetches succeed (returning empty/stale data) and the per-component
 * ErrorState would never fire. Polls every 60s and on window focus.
 */
export function HealthBanner() {
  const { data } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });

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
