import { cn } from "@/lib/utils";

// Tone-driven rounded pill. Drives both stage-status (idle/active/done/failed)
// and per-model provider tags. Visuals live in globals.css under
// `.status-pill[data-tone="..."]`.
export type PillTone =
  | "neutral"
  | "active"
  | "blue"
  | "cyan"
  | "violet"
  | "amber"
  | "green"
  | "red";

interface StatusPillProps {
  tone?: PillTone;
  /** Show a leading dot (pulsing when tone="active"). */
  dot?: boolean;
  className?: string;
  children: React.ReactNode;
}

export function StatusPill({
  tone = "neutral",
  dot = false,
  className,
  children,
}: StatusPillProps) {
  return (
    <span
      className={cn("status-pill", className)}
      data-tone={tone === "neutral" ? undefined : tone}
    >
      {dot && <span className="pill-dot" aria-hidden />}
      {children}
    </span>
  );
}
