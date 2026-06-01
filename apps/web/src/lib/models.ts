// Friendly labels for the model ids the pipeline can use. Keep this list
// in sync with services/api/app/config.py defaults. The PipelineInspector
// renders {label, provider} per step; an unknown model falls back to its
// raw id.

export type ModelInfo = { label: string; provider: string };

const MODELS: Record<string, ModelInfo> = {
  // OpenAI
  "gpt-4.1-mini":  { label: "GPT-4.1 mini",  provider: "OpenAI" },
  "gpt-image-1":   { label: "gpt-image-1",   provider: "OpenAI" },
  "gpt-image-2":   { label: "gpt-image-2",   provider: "OpenAI" },
  // Decart
  "lucy-pro":      { label: "Lucy Pro",      provider: "Decart" },
  // NVIDIA
  "nvidia/magpie-tts-multilingual": { label: "Magpie TTS", provider: "NVIDIA" },
  // GMICloud
  "minimax-music-2.5":  { label: "MiniMax Music 2.5", provider: "GMICloud" },
};

export function lookupModel(model?: string): ModelInfo {
  if (!model) return { label: "—", provider: "—" };
  return MODELS[model] ?? { label: model, provider: model.split("/")[0] ?? "—" };
}
