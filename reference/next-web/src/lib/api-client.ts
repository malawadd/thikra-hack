// Thin REST client over the FastAPI backend. Mirrors the
// vibe-coding-starter-kit's api-client.ts shape (so the TanStack Query
// hooks + ApiError patterns carry over cleanly), but the endpoints are
// the ones OUR backend actually exposes.
//
// The backend host comes from NEXT_PUBLIC_API_URL — set it in
// apps/web/.env.local (defaults to http://localhost:43192 for dev).
// CORS is configured on the FastAPI side via API_CORS_ORIGINS.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:43192";

/** One key under explainers/<run-id>/... in B2. */
export interface FileMetadata {
  key: string;
  size: number;
  last_modified?: string | null;
  /** Run id derived from the key — `explainers/<run-id>/...`. */
  run_id?: string;
  /** Path-like file name without the run-id prefix. */
  display_name?: string;
}

export interface HealthResponse {
  status: "healthy" | "degraded";
  b2_connected: boolean;
  /** Whether the `ffmpeg` binary is on the API host's PATH (Stage C compose). */
  ffmpeg_present: boolean;
  // Per-vendor key presence as `<vendor>_key_present` booleans. The original
  // five are always present; the kitchen-sink vendors are optional so older
  // backends don't break the type.
  providers: {
    openai_key_present: boolean;
    nvidia_key_present: boolean;
    decart_key_present: boolean;
    gmi_key_present: boolean;
    replicate_key_present?: boolean;
    google_key_present?: boolean;
    runway_key_present?: boolean;
    luma_key_present?: boolean;
    elevenlabs_key_present?: boolean;
    lmnt_key_present?: boolean;
    hume_key_present?: boolean;
  };
}

// --- Provider switchboard ---------------------------------------------------

/** The 5 switchboard modalities a run picks a provider for. */
export type Modality = "chat" | "image" | "video" | "tts" | "music";

/** One vendor option for a modality (from `GET /providers`). */
export interface ProviderOption {
  vendor: string;
  default_model: string;
  suggested_models: string[];
  modality: string;
  /** Whether this vendor's API key is configured on the backend. */
  key_available: boolean;
}

export type ProvidersMatrix = Record<Modality, ProviderOption[]>;

/** A per-modality choice. `model` omitted/null → the vendor's default. */
export interface ProviderChoice {
  vendor: string;
  model?: string | null;
}

export type Selection = Record<Modality, ProviderChoice>;

/**
 * The out-of-box "simplest path" — fewest API keys (Replicate + OpenAI).
 *
 * These vendor strings are the one place the frontend hard-couples to the
 * backend catalog keys in `services/api/app/repo/provider_catalog.py`. Each
 * must exist in `matrix[modality]`, or its `<Select>` renders empty and
 * `resolveModel` falls back to the placeholder — keep them in sync.
 */
export const DEFAULT_SELECTION: Selection = {
  chat: { vendor: "openai" },
  image: { vendor: "replicate" },
  video: { vendor: "replicate" },
  tts: { vendor: "openai" },
  music: { vendor: "replicate" },
};

export const MODALITIES: Modality[] = ["chat", "image", "video", "tts", "music"];

/** Display labels for the lowercase vendor keys the catalog uses. Naive
 *  capitalization would yield "Openai" / "Gmicloud"; keep the canonical
 *  casing here and fall back to the raw key for any future vendor. */
export const VENDOR_LABELS: Record<string, string> = {
  openai: "OpenAI",
  replicate: "Replicate",
  google: "Google",
  nvidia: "NVIDIA",
  decart: "Decart",
  gmicloud: "GMICloud",
  runway: "Runway",
  luma: "Luma",
  elevenlabs: "ElevenLabs",
  lmnt: "LMNT",
  hume: "Hume",
};

export const vendorLabel = (vendor: string): string => VENDOR_LABELS[vendor] ?? vendor;

/**
 * The model that will actually run for a modality: the explicit per-run choice
 * if set, else the selected vendor's catalog default. `matrix` may be undefined
 * while `GET /providers` is in flight — fall back to the raw choice or a
 * neutral placeholder so a tile never renders a stale hardcoded model.
 */
export function resolveModel(
  selection: Selection,
  matrix: ProvidersMatrix | undefined,
  m: Modality,
): string {
  const choice = selection[m];
  if (choice.model) return choice.model;
  const opt = matrix?.[m]?.find((o) => o.vendor === choice.vendor);
  return opt?.default_model ?? "default";
}

export interface StoryboardScene {
  image_prompt: string;
  motion_prompt: string;
  narration: string;
  caption: string;
  duration_sec: number;
}

export interface StoryboardSpec {
  title: string;
  style_prompt: string;
  music_prompt: string;
  total_duration_sec: number;
  scenes: StoryboardScene[];
}

export interface StoryboardResponse {
  spec: StoryboardSpec;
  storyboard_key: string;
}

/** HTTP error with status code for caller-side branching.
 *
 * When the backend returns a *classified* error body (Stage A —
 * `detail: {code, message, hint, retryable}` from `app/errors.py`), those
 * fields are surfaced so the UI can show an actionable hint + the right
 * recovery rather than a bare status code. `retryable` prefers the backend's
 * verdict and falls back to the status-based heuristic.
 */
export class ApiError extends Error {
  readonly code?: string;
  readonly hint?: string;
  private readonly _retryable?: boolean;

  constructor(
    message: string,
    public readonly status: number,
    classified?: { code?: string; hint?: string; retryable?: boolean },
  ) {
    super(message);
    this.name = "ApiError";
    this.code = classified?.code;
    this.hint = classified?.hint;
    this._retryable = classified?.retryable;
  }

  get isRetryable(): boolean {
    if (typeof this._retryable === "boolean") return this._retryable;
    return [408, 429, 500, 502, 503, 504].includes(this.status);
  }
  get isNotFound(): boolean { return this.status === 404; }
  get isConflict(): boolean { return this.status === 409; }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError("Network error — check your connection", 0);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // FastAPI puts our payload under `detail`. It's either a classified object
    // {code,message,hint,retryable}, a 422 validation array of {loc,msg,type},
    // or a plain string.
    const detail = body?.detail;
    // Surface the first 422 validation error with its field — e.g. "prompt:
    // String should have at most 2000 characters" — not a bare "API error: 422".
    if (Array.isArray(detail)) {
      const first = detail[0];
      const field = Array.isArray(first?.loc) ? first.loc[first.loc.length - 1] : undefined;
      const msg = first?.msg ? `${field ? `${field}: ` : ""}${first.msg}` : `API error: ${res.status}`;
      throw new ApiError(msg, res.status);
    }
    if (detail && typeof detail === "object") {
      throw new ApiError(detail.message || `API error: ${res.status}`, res.status, detail);
    }
    throw new ApiError(detail || `API error: ${res.status}`, res.status);
  }
  return res.json();
}

// --- Endpoints ---

export async function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

/** The provider switchboard catalog — drives the per-modality pickers. */
export async function getProviders(): Promise<ProvidersMatrix> {
  const data = await apiFetch<{ providers: ProvidersMatrix }>("/providers");
  return data.providers;
}

/** List every artifact written to B2 under `explainers/`. */
export async function getFiles(): Promise<FileMetadata[]> {
  const data = await apiFetch<{
    prefix: string;
    entries: FileMetadata[];
  }>("/files");
  // Derive `run_id` and `display_name` for the file-tree builder.
  return (data.entries ?? []).map((e) => {
    const m = e.key.match(/^explainers\/([^/]+)\/(.*)$/);
    return {
      ...e,
      run_id: m ? m[1] : undefined,
      display_name: m ? m[2] : e.key,
    };
  });
}

/** Stage A — one-shot OpenAI chat() call returning the storyboard JSON. */
export async function createStoryboard(prompt: string): Promise<StoryboardResponse> {
  return apiFetch<StoryboardResponse>("/runs/storyboard", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
}

/** Durable URL → playback URL.
 *
 * Backend `/assets/{key:path}` 302-redirects to a short-lived presigned URL.
 * For path-style B2 URLs (https://s3.<region>.backblazeb2.com/<bucket>/<key>),
 * strip the bucket segment to extract the object key. Returns the input as-is
 * if it doesn't look like a B2 URL.
 */
export function playbackUrl(durableOrKey: string): string {
  if (!durableOrKey.startsWith("http")) {
    return `${API_BASE}/assets/${durableOrKey}`;
  }
  try {
    const u = new URL(durableOrKey);
    const trimmed = u.pathname.replace(/^\//, "");
    const slash = trimmed.indexOf("/");
    const key = slash === -1 ? trimmed : trimmed.slice(slash + 1);
    return `${API_BASE}/assets/${key}`;
  } catch {
    return durableOrKey;
  }
}
