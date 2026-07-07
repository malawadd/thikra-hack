"""Centralised settings. Loaded from the monorepo-root `.env`.

Standardised B2_* env var names per parent CLAUDE.md §3.
All provider keys + model overrides live here.
"""

from pathlib import Path

from pydantic_settings import BaseSettings

# Monorepo-root .env (services/api/app/config.py → climb 3 levels)
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    # --- Backblaze B2 (parent-standard names; do not rename) ---
    # No `b2_endpoint`: `S3StorageBackend.for_backblaze()` derives the endpoint
    # from `region`. Keeping the field would mislead readers into thinking the
    # sample consumes it.
    b2_region: str = ""
    b2_key_id: str = ""
    b2_application_key: str = ""
    b2_bucket_name: str = ""

    # --- Provider keys ---
    # OpenAI hosts the storyboard chat (`genblaze_openai.chat()` — function,
    # not a Provider class, so Stage A is not a Pipeline). Google hosts
    # the keyframe image generation via Imagen. NVIDIA still carries
    # Stage B2 TTS; GMICloud handles the music score AND is the
    # designated fallback video provider when Decart is unavailable.
    openai_api_key: str = ""
    google_api_key: str = ""
    decart_api_key: str = ""
    nvidia_api_key: str = ""
    gmi_api_key: str = ""
    # Kitchen-sink providers (newly released adapters). Each key gates one
    # vendor in the switchboard; a missing key greys that vendor out in the UI
    # (`/providers` reports `key_available`). The constructor kwarg differs by
    # vendor (api_token / api_secret / auth_token / api_key) — the catalog
    # factory passes the right one; these field names are OUR env contract.
    replicate_api_token: str = ""   # multi-modal: image + video + music
    runway_api_secret: str = ""     # video (Runway Gen)
    luma_api_key: str = ""          # video (Luma Dream Machine)
    elevenlabs_api_key: str = ""    # TTS
    lmnt_api_key: str = ""          # TTS
    hume_api_key: str = ""          # TTS (Hume Octave)

    # --- Model defaults (override via env) ---
    # Stage A — storyboard chat via `genblaze_openai.chat()`. gpt-4.1-nano
    # is OpenAI's fastest live text model that still supports structured
    # outputs (`response_format=`), which Stage A relies on. Latency is
    # ~30-50% lower than gpt-4.1-mini on this prompt shape; schema fidelity
    # is unchanged because nano is part of the same gpt-4.1 family.
    chat_model: str = "gpt-4.1-nano"
    # Stages B0/B1 — Imagen image generation (Imagen 4 is the latest gen on the
    # Gemini API; 3.0 is retired). `imagen-4.0-generate-001` is the flagship;
    # `-ultra-` is max quality (slower/pricier), `-fast-` the cheapest. The
    # flagship balances quality and the per-scene fan-out cost; flip via env.
    image_model: str = "imagen-4.0-generate-001"
    # Stage B2 — image-to-video. `video_provider` selects which Genblaze
    # provider drives the per-scene clips: `gmicloud` (default, Kling
    # Image2Video) or `decart`. Default is GMICloud because Decart RETIRED
    # image-to-video — its current Lucy models are video-to-video only, so
    # they can't animate a keyframe. If the selected provider's key is missing
    # at boot we swap to the other and log it; the run still completes.
    video_provider: str = "gmicloud"
    # Decart video model — only used if `video_provider=decart`. NOTE: Decart
    # no longer offers image-to-video (lucy-* are video-to-video now), so this
    # path can't drive the keyframe→clip step; kept for the fallback resolver.
    video_model: str = "lucy-2.1"
    # GMICloud image-to-video model (the working i2v path). Kling V2.1 Master
    # takes the keyframe (routed from step inputs) + a motion prompt.
    gmi_video_model: str = "Kling-Image2Video-V2.1-Master"
    # Stage B2 — NVIDIA TTS. The `nvidia/` namespace is required; a bare
    # `magpie-tts-multilingual` 404s the NIM genai endpoint (the provider's
    # voice family only matches `^nvidia/...` slugs).
    tts_model: str = "nvidia/magpie-tts-multilingual"
    # Stage B2 — GMICloud music. Canonical lowercase slug from GMI's current
    # catalog; the retired `MiniMax-Music-1` 404s the upstream probe (DEAD)
    # and the PascalCase form triggers a registry canonical-slug rewrite log.
    music_model: str = "minimax-music-2.5"

    # --- Observability + caching ---
    otel_endpoint: str = ""
    step_cache_dir: str = "./.cache/explainers"
    # Root log level: DEBUG floods stdout with per-step prompts, SSE frame
    # outflow, B2 ops, and Genblaze tracer chatter — flip to it when
    # diagnosing a stuck pipeline. INFO is the sane default.
    log_level: str = "INFO"

    # --- API surface ---
    # No `api_port`: uvicorn takes `--port` from its CLI; carrying a dead
    # setting just invites drift between env, code, and the run command.
    api_cors_origins: str = "http://localhost:3000,http://localhost:3001"

    model_config = {"env_file": str(_ROOT_ENV), "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


settings = Settings()
