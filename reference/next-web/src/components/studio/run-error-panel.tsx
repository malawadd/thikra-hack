"use client";

import { AlertTriangle, Pencil, RefreshCw, RotateCcw } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { AlertBanner } from "@/components/ui/alert-banner";

/** A failed run, as the studio page tracks it (from the SSE `error` frame). */
export interface RunError {
  stage: string;
  message: string;
  hint?: string;
  retryable?: boolean;
}

/**
 * Persistent failure panel — the counterpart to the ephemeral toast. Stays on
 * screen (with the partial storyboard + media still visible behind it) and
 * offers recovery: Retry (transient failures only), Edit storyboard, or Start
 * over. Retry re-runs the media pipeline from the start, so its copy says so —
 * a silent re-bill would be a cost trap.
 */
export function RunErrorPanel({
  error,
  onRetry,
  onEdit,
  onStartOver,
}: {
  error: RunError;
  onRetry: () => void;
  /** Omitted when there's no storyboard to edit (e.g. Stage A itself failed). */
  onEdit?: () => void;
  onStartOver: () => void;
}) {
  return (
    <AlertBanner
      tone="error"
      icon={AlertTriangle}
      title={`${error.stage} failed — ${error.message}`}
      actions={
        <>
          {error.retryable && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              <RefreshCw className="h-3.5 w-3.5" /> Retry
            </Button>
          )}
          {onEdit && (
            <Button size="sm" variant="outline" onClick={onEdit}>
              <Pencil className="h-3.5 w-3.5" /> Edit storyboard
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onStartOver}>
            <RotateCcw className="h-3.5 w-3.5" /> Start over
          </Button>
        </>
      }
    >
      {error.hint && <p>{error.hint}</p>}
      <p className="mt-1">
        {error.retryable
          ? "Retry re-runs from the start and re-incurs provider cost."
          : "Any assets generated so far are saved in B2."}{" "}
        <Link href="/files" className="underline underline-offset-2">
          View files
        </Link>
      </p>
    </AlertBanner>
  );
}
