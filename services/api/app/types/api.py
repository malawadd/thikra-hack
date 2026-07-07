"""Request DTOs. Response bodies are Genblaze models (Run / Step / Asset / Manifest)."""

from pydantic import BaseModel, Field

from app.types.storyboard import StoryboardSpec

# --- Provider selection (the switchboard) -----------------------------------
# Per-modality vendor + model choice. Vendor strings are validated against the
# provider catalog in the endpoint (not here) so this layer stays free of any
# repo/genblaze import. `model=None` means "use the catalog's default_model for
# that vendor". The defaults below are the SIMPLEST PATH (fewest API keys):
# Replicate (one token) drives image/video/music; OpenAI drives chat + TTS.


class ProviderChoice(BaseModel):
    vendor: str
    model: str | None = None  # None → catalog default_model for this vendor


class Selection(BaseModel):
    chat: ProviderChoice = ProviderChoice(vendor="openai")
    image: ProviderChoice = ProviderChoice(vendor="replicate")
    video: ProviderChoice = ProviderChoice(vendor="replicate")
    tts: ProviderChoice = ProviderChoice(vendor="openai")
    music: ProviderChoice = ProviderChoice(vendor="replicate")

# Seed-prompt bounds, shared by both request DTOs so the storyboard endpoint
# and the media stream (which re-sends the same seed) accept identical input.
# 2000 chars leaves room for a detailed, art-directed brief — the earlier 500
# cap 422'd realistic prompts before the handler ever ran — while still
# bounding abuse. Keep the frontend Textarea's `maxLength` in lockstep.
_PROMPT_MIN, _PROMPT_MAX = 4, 2000


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=_PROMPT_MIN, max_length=_PROMPT_MAX)


class MediaRequest(BaseModel):
    """Stream endpoint input.

    `prompt` is the seed; `spec` is an optional client-refined storyboard.
    When `spec` is omitted the streaming endpoint regenerates it server-side
    via `generate_storyboard()` (a single `chat()` call, ~3s).
    """

    prompt: str = Field(min_length=_PROMPT_MIN, max_length=_PROMPT_MAX)
    spec: StoryboardSpec | None = None
    # Per-modality provider selection. Omitted → the simplest-path defaults.
    selection: Selection = Field(default_factory=Selection)
