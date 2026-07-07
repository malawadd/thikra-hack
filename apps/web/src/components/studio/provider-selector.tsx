"use client";

import { useEffect, useState } from "react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatusPill } from "@/components/ui/status-pill";
import { useProviders } from "@/lib/queries";
import { MODALITIES, vendorLabel, type Modality, type Selection } from "@/lib/api-client";

// Key that records whether the user has seen the switchboard once, so we open
// it on the first visit (it's the sample's headline feature) but respect their
// collapsed choice on every visit after.
const SEEN_KEY = "gb.providers.seen";

// Human labels + one-line "what this drives" hints, in pipeline order.
const MODALITY_META: Record<Modality, { label: string; hint: string }> = {
  chat: { label: "Script", hint: "storyboard (OpenAI only — needs structured output)" },
  image: { label: "Keyframes", hint: "text → image, one per scene" },
  video: { label: "Motion", hint: "image → video clip per scene" },
  tts: { label: "Narration", hint: "text → speech" },
  music: { label: "Score", hint: "instrumental bed" },
};

/**
 * The provider switchboard UI. One vendor + model picker per modality, fed by
 * `GET /providers`. Vendors whose API key isn't configured are disabled (with
 * a "needs key" tag); leaving the model blank uses the vendor's curated
 * default. Collapsed by default — the simplest-path defaults already work.
 */
export function ProviderSelector({
  selection,
  onChange,
  disabled,
}: {
  selection: Selection;
  onChange: (next: Selection) => void;
  disabled?: boolean;
}) {
  const { data: matrix, isLoading, error } = useProviders();
  const [open, setOpen] = useState(false);

  // Reveal the switchboard once on a first visit. Initial render stays
  // collapsed on both server and client (no hydration mismatch); after mount
  // we check localStorage and, if unseen, open on the next frame — deferring
  // the state update out of the effect body so it doesn't cascade renders.
  // Wrapped in try/catch because localStorage throws in private-mode Safari.
  useEffect(() => {
    let firstVisit = false;
    try {
      firstVisit = !localStorage.getItem(SEEN_KEY);
      if (firstVisit) localStorage.setItem(SEEN_KEY, "1");
    } catch {
      // No persistent storage — leave it collapsed.
    }
    if (!firstVisit) return;
    const id = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const setVendor = (m: Modality, vendor: string) =>
    // Reset model to the new vendor's default (null) — a slug from the old
    // vendor is meaningless for the new one.
    onChange({ ...selection, [m]: { vendor, model: null } });
  const setModel = (m: Modality, model: string) =>
    onChange({ ...selection, [m]: { ...selection[m], model: model || null } });

  // Collapsed-header legend mapping each modality to its chosen vendor, e.g.
  // "Script OpenAI · Keyframes Replicate · …" — doubles as the at-a-glance
  // "what's running" summary so the switchboard reads as the differentiator.
  const summary = MODALITIES
    .map((m) => `${MODALITY_META[m].label} ${vendorLabel(selection[m].vendor)}`)
    .join(" · ");

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="rounded-lg border border-border bg-card/40"
    >
      <CollapsibleTrigger className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left">
        <div className="min-w-0">
          <div className="text-sm font-medium">Providers</div>
          <div className="truncate text-xs text-muted-foreground font-mono">{summary}</div>
        </div>
        <StatusPill tone="neutral">{open ? "Hide" : "Customize"}</StatusPill>
      </CollapsibleTrigger>

      <CollapsibleContent className="border-t border-border px-4 py-4">
        {isLoading && <p className="text-xs text-muted-foreground">Loading providers…</p>}
        {error && (
          <p className="text-xs text-red-500">
            Couldn&apos;t load providers ({error.message}). Defaults still apply.
          </p>
        )}
        {matrix && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Mix and match any provider per modality. Blank model = the vendor&apos;s
              default. Greyed-out vendors need an API key on the backend. Untested
              combinations may fail at runtime — the run degrades gracefully (a
              failed clip falls back to its keyframe still).
            </p>
            {MODALITIES.map((m) => {
              const opts = matrix[m] ?? [];
              const choice = selection[m];
              const current = opts.find((o) => o.vendor === choice.vendor);
              const listId = `models-${m}`;
              return (
                <div
                  key={m}
                  // Stacks (label / vendor / model) on narrow screens; becomes
                  // a 3-column row at sm+ where there's room for it.
                  className="grid grid-cols-1 gap-2 sm:grid-cols-[7rem_1fr_1fr] sm:items-center sm:gap-3"
                >
                  <div>
                    <div className="text-sm font-medium">{MODALITY_META[m].label}</div>
                    <div className="text-[11px] text-muted-foreground leading-tight">
                      {MODALITY_META[m].hint}
                    </div>
                  </div>

                  <Select
                    value={choice.vendor}
                    onValueChange={(v) => setVendor(m, v)}
                    disabled={disabled || opts.length <= 1}
                  >
                    <SelectTrigger className="h-9">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {opts.map((o) => (
                        <SelectItem key={o.vendor} value={o.vendor} disabled={!o.key_available}>
                          {vendorLabel(o.vendor)}
                          {!o.key_available && " · needs key"}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  <div>
                    <Input
                      className="h-9 font-mono text-xs"
                      list={listId}
                      placeholder={current?.default_model ?? "default"}
                      value={choice.model ?? ""}
                      disabled={disabled}
                      onChange={(e) => setModel(m, e.target.value)}
                    />
                    <datalist id={listId}>
                      {(current?.suggested_models ?? []).map((s) => (
                        <option key={s} value={s} />
                      ))}
                    </datalist>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
