"""Storyboard schema — driven by `chat(..., response_format=StoryboardSpec)`.

`genblaze_openai.chat()` accepts a Pydantic class directly for
`response_format` and coerces it internally. The chat call returns a
JSON object whose shape matches this model; `pipelines.generate_storyboard`
validates it with `StoryboardSpec.model_validate_json(...)`.
"""

from pydantic import BaseModel, ConfigDict, Field

# OpenAI's structured-output API rejects schemas that omit
# `additionalProperties: false` on every object. Pydantic's default is to
# permit extras and emit no constraint; `extra="forbid"` flips both:
# rejects unknown fields at validation time AND emits
# `"additionalProperties": false` in the JSON schema. Required for
# `chat(..., response_format=StoryboardSpec)`.
_STRICT = ConfigDict(extra="forbid")


class Scene(BaseModel):
    """One ~10-second beat of the explainer."""

    model_config = _STRICT

    image_prompt: str = Field(description="A single descriptive sentence for the keyframe image.")
    motion_prompt: str = Field(description="How that keyframe should animate (camera + subject motion).")
    narration: str = Field(description="The narration spoken over this scene (1-2 sentences).")
    caption: str = Field(description="A short on-screen caption (<= 60 chars).")
    duration_sec: float = Field(ge=4.0, le=12.0, description="Seconds this scene should last.")


class StoryboardSpec(BaseModel):
    """The structured plan returned by Stage A."""

    model_config = _STRICT

    title: str = Field(description="A short title for the explainer.")
    style_prompt: str = Field(
        description=(
            "A single descriptive sentence locking the visual style for every "
            "scene — palette, illustration style, lighting, mood. Used both "
            "to render a Stage B0 reference image AND prefixed onto every "
            "Stage B1 per-scene image prompt so all keyframes share a look."
        ),
    )
    music_prompt: str = Field(description="Mood + genre instruction for the background score.")
    total_duration_sec: float = Field(ge=20.0, le=120.0)
    scenes: list[Scene] = Field(min_length=4, max_length=6)
