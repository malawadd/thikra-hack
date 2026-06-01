# Media generation

Stage B1 generates one keyframe per scene; Stage B2 turns each keyframe
into a short video clip, narrates it, and lays a music bed underneath.
Four providers run inside two linked pipelines.

## Stage B1 — Keyframes (OpenAI image)

```python
# pipelines.py:build_keyframe_pipeline
img = DalleProvider(api_key=settings.openai_api_key)
p = _attach(Pipeline(PIPELINE_NAME, max_concurrency=3))
for scene in spec.scenes:
    p = p.step(
        img,
        model=settings.image_model,     # default gpt-image-1
        modality=Modality.IMAGE,
        prompt=scene.image_prompt,
    )
```

- **Provider class:** `DalleProvider` (not `OpenAIImageProvider` — the
  latter doesn't exist in `genblaze-openai` 0.3.0).
- **Model default:** `gpt-image-1`. `gpt-image-2` is the documented
  upgrade target; flip via env once OpenAI ships it and
  `genblaze-openai` accepts it.
- **Concurrency:** Genblaze runs the sibling steps at
  `max_concurrency=3` — no executor in the sample. Latency per scene is
  whatever DALL-E returns; total Stage B1 wall-clock is the slowest
  scene plus one batching gap.
- **Lineage:** Stage B1 has no `from_result()` anchor — Stage A is a
  `chat()` function call rather than a Pipeline, so there's no
  PipelineResult to chain from. Stage B1's Manifest is the lineage root
  for the visual track; Stage B2's Manifest carries B1's `parent_run_id`.

## Stage B2 — Image-to-video + TTS + music

This is the cross-pipeline asset-handoff stage. The keyframe assets
from Stage B1 are durable in B2; we presign each one and hand the URL to
Decart's `image=` kwarg.

```python
# pipelines.py:build_media_pipeline
vid = DecartVideoProvider(api_key=settings.decart_api_key)
tts = NvidiaAudioProvider(api_key=settings.nvidia_api_key)
music = GMICloudAudioProvider(api_key=settings.gmi_api_key)

# preflight=False — narration + music are best-effort; the caller runs this
# pipeline fail_fast=False so a DEAD audio model fails as one step, not the run.
p = _attach(Pipeline(PIPELINE_NAME, max_concurrency=3, preflight=False)).from_result(keyframe_result)
for i, scene in enumerate(spec.scenes):
    image_asset = keyframe_result.run.steps[i].assets[0]
    image_ref = presign_asset_url(image_asset.url)
    p = p.step(vid, model=settings.video_model, modality=Modality.VIDEO,
               prompt=scene.motion_prompt, image=image_ref,
               duration=scene.duration_sec)
    p = p.step(tts, model=settings.tts_model, modality=Modality.AUDIO,
               prompt=scene.narration)
return p.step(music, model=settings.music_model, modality=Modality.AUDIO,
              prompt=spec.music_prompt, duration=spec.total_duration_sec)
```

### Why `image=<presigned-url>` and not `input_from=`

`Pipeline.from_result(prev_result)` only records lineage in 0.3.x — it
does NOT hydrate prior step assets into provider kwargs. Cross-pipeline
handoffs go through the provider's image/audio/video kwarg as a
presigned URL. The reference implementation for this pattern is
`genblaze-gmicloud-pipeline/services/api/app/repo/pipelines.py::build_video_fanout`.

`input_from=<step_index>` is for within-pipeline fan-out from a shared
upstream step. This sample doesn't use it; per-scene parallelism comes
from sibling `.step()` calls at `max_concurrency=3` inside a single
Pipeline.

### Stage B2 step ordering

The composer relies on Stage B2 emitting steps in this order:

```
[ video_0, tts_0, video_1, tts_1, ..., video_{N-1}, tts_{N-1}, music ]
```

So scene `i` lives at `steps[2i]` (video) + `steps[2i+1]` (narration),
and `steps[-1]` is the music bed. `composer._group_scenes()` and
`_download_music()` pair on these indices.

Ordering holds even when steps fall back: with `fail_fast=False` a failed
step is still present in the result (order preserved) but carries an empty
`assets` list. The composer reads assets by *presence* (`_asset_url_or_none`),
never by blindly indexing `assets[0]` — so a FAILED step degrades instead of
raising `IndexError`.

### Everything is best-effort

Video, narration (NVIDIA TTS), and music (GMICloud) are all non-essential.
The B2 pipeline is built `preflight=False` and run
`fail_fast=False, raise_on_failure=False`, so a DEAD/failing model is
contained as one FAILED step rather than aborting the whole run at preflight
(which validates *every* step). A FAILED best-effort run completes via
`PipelineFailedEvent` (not `PipelineCompletedEvent`) — `main._stream_stage`
captures the result from both, so the composer still sees every succeeded
asset.

`compose_final(b2_run, b1_run, spec)` takes the Stage B1 keyframe result too:
- A failed **video** clip falls back to that scene's keyframe still (looped
  to the scene duration). Only a scene missing BOTH clip and keyframe raises.
- Failed **narration/music** is mixed as silence or dropped.

Each fallback emits a `notice` SSE frame (e.g. "Video unavailable for scene 3
— used the keyframe still instead") the UI shows as a warning. The final MP4
always renders.

## Provider table

| Provider                | Class                   | Env var          | Default model                              |
|-------------------------|-------------------------|------------------|--------------------------------------------|
| OpenAI image            | `DalleProvider`         | `OPENAI_API_KEY` | `gpt-image-1`                              |
| Decart image-to-video   | `DecartVideoProvider`   | `DECART_API_KEY` | `lucy-pro`                                 |
| NVIDIA TTS              | `NvidiaAudioProvider`   | `NVIDIA_API_KEY` | `nvidia/magpie-tts-multilingual`           |
| GMICloud music          | `GMICloudAudioProvider` | `GMI_API_KEY`    | `minimax-music-2.5`                        |

All four are imported only by `app/repo/pipelines.py`. Adding a fifth
provider is one `.step()` call (see AGENTS Rule 4).

## Preflight

The essential pipelines (B0 reference + B1 keyframes) keep the default
`preflight=True`: the Pipeline calls `validate_model()` on each step's
provider before any wire traffic — a misconfigured `OPENAI_API_KEY` fails
fast for $0, not after a partial run.

The B2 media pipeline is the exception — `preflight=False`. Preflight
validates *every* step up front and raises on the first DEAD model, which
would abort the whole run (video included) for a non-essential audio outage.
Since B2 is best-effort, it skips preflight and lets each model fail at
runtime as a contained FAILED step instead.
