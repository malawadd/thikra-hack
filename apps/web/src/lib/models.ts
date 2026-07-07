// Friendly labels for a few common model ids the inspector renders per step.
//
// This is a switchboard: ANY provider/model can drive any modality per run, so
// a hardcoded model→vendor table can't be authoritative. We keep only stable
// display niceties here and let `lookupModel` fall back to the raw id +
// namespace for everything else — never assert a vendor we can't be sure of.

export type ModelInfo = { label: string; provider: string };

// Optional friendly labels. Vendor is intentionally omitted; the fallback
// derives a namespace hint from the id (e.g. "meta/musicgen" → "meta").
const MODEL_LABELS: Record<string, string> = {
  "gpt-image-1": "gpt-image-1",
  "gpt-image-2": "gpt-image-2",
};

export function lookupModel(model?: string): ModelInfo {
  if (!model) return { label: "—", provider: "—" };
  const label = MODEL_LABELS[model] ?? model;
  // A slash-namespaced id (Replicate/HF style) exposes its owner; a bare id
  // doesn't, so leave the provider blank rather than echoing the model.
  const provider = model.includes("/") ? model.split("/")[0] : "—";
  return { label, provider };
}
