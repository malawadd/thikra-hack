"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/status-pill";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { useHealth } from "@/lib/queries";

const PROVIDER_LABEL: Record<string, string> = {
  openai_key_present:     "OpenAI",
  replicate_key_present:  "Replicate",
  google_key_present:     "Google",
  nvidia_key_present:     "NVIDIA",
  decart_key_present:     "Decart",
  gmi_key_present:        "GMICloud",
  runway_key_present:     "Runway",
  luma_key_present:       "Luma",
  elevenlabs_key_present: "ElevenLabs",
  lmnt_key_present:       "LMNT",
  hume_key_present:       "Hume",
};

export default function SettingsPage() {
  const { data: health, isLoading, error, refetch } = useHealth();

  return (
    <div className="space-y-8 max-w-3xl">
      <div className="animate-fade-in border-b border-border pb-5">
        <h1 className="page-title">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1.5">
          Read-only view of what the backend currently sees. Edit values in{" "}
          <span className="font-mono text-foreground/80">
            services/api/.env
          </span>{" "}
          and restart the API to change anything.
        </p>
      </div>

      {isLoading && (
        <div className="space-y-3 animate-fade-in-up stagger-2">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      )}

      {error && (
        <ErrorState
          error={error}
          title="Couldn't load backend health"
          description={error.message}
          onRetry={() => refetch()}
        />
      )}

      {health && (
        <>
          <Card className="animate-fade-in-up stagger-2">
            <CardHeader className="border-b border-border py-4 px-5">
              <CardTitle className="card-title">Backblaze B2</CardTitle>
            </CardHeader>
            <CardContent className="p-5 flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium">Bucket reachability</p>
                <p className="text-xs text-muted-foreground">
                  Backend probes the configured bucket on each{" "}
                  <span className="font-mono">/health</span> call.
                </p>
              </div>
              <StatusPill
                tone={health.b2_connected ? "green" : "red"}
                dot={!health.b2_connected}
              >
                {health.b2_connected ? "connected" : "unreachable"}
              </StatusPill>
            </CardContent>
          </Card>

          <Card className="animate-fade-in-up stagger-3">
            <CardHeader className="border-b border-border py-4 px-5">
              <CardTitle className="card-title">Provider keys</CardTitle>
            </CardHeader>
            <CardContent className="p-5">
              <ul className="divide-y rounded-md border">
                {Object.entries(health.providers).map(([key, present]) => (
                  <li
                    key={key}
                    className="flex items-center justify-between gap-3 px-3 py-2.5"
                  >
                    <span className="text-sm font-medium">
                      {PROVIDER_LABEL[key] ?? key}
                    </span>
                    <StatusPill tone={present ? "green" : "amber"}>
                      {present ? "configured" : "missing"}
                    </StatusPill>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted-foreground">
                Keys are detected by presence — value validity is checked by
                the first provider call when you run a pipeline.
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
