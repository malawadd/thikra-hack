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
    # not a Provider class, so Stage A is not a Pipeline). NVIDIA still
    # carries Stage B2 TTS via `NvidiaAudioProvider`.
    openai_api_key: str = ""
    decart_api_key: str = ""
    nvidia_api_key: str = ""
    gmi_api_key: str = ""

    # --- Model defaults (override via env) ---
    # Stage A — storyboard chat via `genblaze_openai.chat()`. gpt-4.1-nano
    # is OpenAI's fastest live text model that still supports structured
    # outputs (`response_format=`), which Stage A relies on. Latency is
    # ~30-50% lower than gpt-4.1-mini on this prompt shape; schema fidelity
    # is unchanged because nano is part of the same gpt-4.1 family.
    chat_model: str = "gpt-4.1-nano"
    # Stage B1 — keyframe. gpt-image-1 is the current OpenAI live model id
    # (gpt-image-2 is the documented upgrade target; flip via env when shipped).
    image_model: str = "gpt-image-1"
    # Stage B2 — Decart image-to-video.
    video_model: str = "lucy-pro"
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
