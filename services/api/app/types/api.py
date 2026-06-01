"""Request DTOs. Response bodies are Genblaze models (Run / Step / Asset / Manifest)."""

from pydantic import BaseModel, Field

from app.types.storyboard import StoryboardSpec


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=500)


class MediaRequest(BaseModel):
    """Stream endpoint input.

    `prompt` is the seed; `spec` is an optional client-refined storyboard.
    When `spec` is omitted the streaming endpoint regenerates it server-side
    via `generate_storyboard()` (a single `chat()` call, ~3s).
    """

    prompt: str = Field(min_length=4, max_length=500)
    spec: StoryboardSpec | None = None
