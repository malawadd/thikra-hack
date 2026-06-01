# Progressive guidance

The default path is one prompt → one MP4 with zero intermediate input.
"Progressive guidance" is opt-in: the user can review and edit the
Stage A storyboard before Stages B1/B2/C fire. Disabled by default so
nobody is forced through a form they don't need.

## UI shape

The main page (`apps/web/src/app/page.tsx`) walks through four cards:

1. **Type one sentence** — `PromptForm` posts to `/runs/storyboard`.
2. **Storyboard** — when the spec arrives the title + scene count
   render with a "Review & refine" disclosure (`StoryboardReview`) and
   a primary "Generate media" CTA. Tapping the CTA directly fires Stage
   B1/B2/C with the unchanged spec.
3. **Pipeline progress** — `PipelineProgress` accumulates SSE events
   from `/runs/media/stream`. Empty until generation starts.
4. **Final explainer** — `FinalVideo` plus a B2 `AssetList` for the
   completed run.

`StoryboardReview` is an `Accordion` (collapsed by default). Opening it
reveals per-scene `image_prompt` / `motion_prompt` / `narration` /
`caption` editors. Edits flow back into the page-level `spec` state via
`onChange`, and the same `spec` is sent to the streaming endpoint when
the user hits "Generate media".

## Backend shape

```python
class MediaRequest(BaseModel):
    prompt: str            # the seed (always required)
    spec: StoryboardSpec | None = None   # optional refined storyboard
```

When `spec` is omitted, `/runs/media/stream` calls `generate_storyboard(prompt)`
(one `chat()` call, ~3s) to materialise the storyboard. When `spec` is
present, Stage A is skipped entirely and the streaming endpoint goes
straight to Stage B1 — there's no Pipeline `prev_result` to thread
through, since Stage A is a function rather than a Pipeline.

## Why this isn't a wizard

Most users will accept the auto-generated storyboard. Forcing them
through a multi-step form to refine four prompts each time makes the
sample slower, more confusing, and less honest about what one-prompt
generative media actually feels like in 2026. The disclosure pattern
gives power users the override without taxing everyone else.

## When to extend

If a future variant needs e.g. per-scene image-style controls, add them
inside `StoryboardReview` as additional fields on the spec. Don't add a
parallel form — the spec is the single source of truth for the refined
plan.
