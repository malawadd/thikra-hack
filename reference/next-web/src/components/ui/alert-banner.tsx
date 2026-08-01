"use client";

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// Shared alert surface for the run-error panel, the pre-flight readiness
// notice, and the B2 health banner. One primitive, three usages — keeps the
// `role="alert"` semantics + `--attention` / `--destructive` token language
// in a single place instead of three hand-styled divs.
export type AlertTone = "error" | "warning";

const TONE: Record<AlertTone, { bg: string; border: string; fg: string }> = {
  warning: {
    bg: "var(--attention-subtle)",
    border: "color-mix(in oklab, var(--attention) 30%, var(--border))",
    fg: "var(--attention)",
  },
  error: {
    bg: "color-mix(in oklab, var(--destructive) 12%, var(--background))",
    border: "color-mix(in oklab, var(--destructive) 35%, var(--border))",
    fg: "var(--destructive)",
  },
};

export function AlertBanner({
  tone,
  icon: Icon,
  title,
  children,
  actions,
  className,
}: {
  tone: AlertTone;
  icon: LucideIcon;
  title: ReactNode;
  /** Supporting copy under the title; rendered in muted foreground. */
  children?: ReactNode;
  /** Right-aligned action buttons/links. */
  actions?: ReactNode;
  /** Layout override — e.g. a full-bleed strip (`rounded-none border-x-0`). */
  className?: string;
}) {
  const c = TONE[tone];
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-wrap items-start gap-x-3 gap-y-2 rounded-lg border px-4 py-3 text-xs",
        className,
      )}
      // Tone colors are CSS-var driven (light/dark aware); the title + icon
      // inherit `fg`, body copy overrides to muted-foreground.
      style={{ background: c.bg, borderColor: c.border, color: c.fg }}
    >
      <Icon className="h-4 w-4 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0 space-y-1">
        <p className="font-semibold">{title}</p>
        {children && (
          <div className="text-foreground/70 leading-relaxed">{children}</div>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}
