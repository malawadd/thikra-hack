"use client";

import { Button } from "@/components/ui/button";
import { playbackUrl } from "@/lib/api-client";
import type { Asset } from "@/types/pipeline";

export function FinalVideo({ asset }: { asset: Asset }) {
  // `asset.url` is a durable B2 URL (credential-free). Route through the
  // proxy's /assets/{key} → 302 → presigned URL so the browser can play it.
  const src = playbackUrl(asset.url);
  return (
    <div className="space-y-3">
      <video src={src} controls className="w-full rounded-md border bg-black" />
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span className="mono truncate">
          sha256: {asset.sha256?.slice(0, 16) ?? "—"}…
        </span>
        <Button asChild variant="outline" size="sm">
          <a href={src} download>
            Download MP4
          </a>
        </Button>
      </div>
    </div>
  );
}
