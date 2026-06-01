# Prompt → Storyboard

Stage A turns the user's one-line seed into a structured `StoryboardSpec`
JSON object. The chat call enforces the schema upstream via
`response_format=StoryboardSpec`, so the backend's only job is
`StoryboardSpec.model_validate_json(...)`.

## The idiom

```python
# services/api/app/repo/pipelines.py
from genblaze_openai import chat
from app.types.storyboard import StoryboardSpec

response = chat(
    settings.chat_model,                       # e.g. "gpt-4.1-mini"
    prompt=_STORYBOARD_INSTRUCTION.format(seed=prompt),
    api_key=settings.openai_api_key,
    response_format=StoryboardSpec,            # Pydantic class, accepted directly
)
spec = StoryboardSpec.model_validate_json(response.text)
```

`chat()` accepts a Pydantic `BaseModel` subclass for `response_format`
and internally calls `coerce_response_format()` to produce the
`{"type":"json_schema","json_schema":{...}}` envelope OpenAI expects.
The returned `ChatResponse` exposes the model's JSON output on `.text`,
which round-trips through `StoryboardSpec`.

## Stage A is a function, not a Pipeline

`genblaze-openai` 0.3.0 ships these surfaces:

- `DalleProvider` — a `BaseProvider` class for image generation.
- `SoraProvider` — a `BaseProvider` class for video.
- `OpenAITTSProvider` — a `BaseProvider` class for TTS.
- **`chat()` / `achat()`** — standalone functions at
  `genblaze_openai/chat.py:146` / `:240`, exported from
  `genblaze_openai/__init__.py`. They do NOT implement `BaseProvider`.

Because `Pipeline.step()` requires a `BaseProvider` instance, the
storyboard call cannot ride a Pipeline. Stage A is therefore a plain
synchronous function (`generate_storyboard()` in `pipelines.py`) that:

1. Calls `chat(model, prompt=…, response_format=StoryboardSpec, …)`.
2. Validates the response with `StoryboardSpec.model_validate_json(…)`.
3. Persists the JSON to B2 by hand under
   `explainers/<uuid>/storyboard.json` — there's no Manifest for this
   step because there's no Pipeline.

Stages B1 and B2 remain proper Pipelines.

> **Filed as Genblaze SDK feedback.** The function-vs-class asymmetry is
> the headline DX gap of this build. Two clean resolutions:
> (1) add an `OpenAIChatProvider` class so chat goes through
> `Pipeline.step()` like every other modality, or
> (2) document the function-vs-class divide loudly in the
> `genblaze-openai` README so future builders don't trip on it.

## Schema

```python
# services/api/app/types/storyboard.py
class Scene(BaseModel):
    image_prompt: str
    motion_prompt: str
    narration: str
    caption: str               # <= 60 chars; burned onto the final video
    duration_sec: float        # 4.0 – 12.0

class StoryboardSpec(BaseModel):
    title: str
    music_prompt: str
    total_duration_sec: float  # 20.0 – 120.0
    scenes: list[Scene]        # 3 – 8 entries
```

Bounds are enforced by Pydantic, not the prompt. If the model returns a
30-second scene or a 9-scene plan, `model_validate_json` raises and the
backend returns 502 — explicit failure beats silent truncation.

## Progressive guidance

The frontend defaults to "accept and continue" — the user never has to
look at the storyboard JSON. The "Review & refine" accordion exposes
each scene's fields for editing; the edited `StoryboardSpec` is posted
back as `MediaRequest.spec` to `/runs/media/stream`.

When `spec` is omitted, the streaming endpoint re-runs Stage A
in-request (one `chat()` call, ~3s) before kicking off Stage B1. There
is no cross-request session store — the storyboard either rides in on
the request body, or it gets regenerated server-side.
