# App workflows

## One prompt → final MP4

```
User                Web (Next.js)              FastAPI                Genblaze + Providers              B2
 │                       │                        │                            │                        │
 │ "how LLMs think       │                        │                            │                        │
 │  step by step"        │                        │                            │                        │
 ├──────────────────────►│ POST /api/proxy/       │                            │                        │
 │                       │     runs/storyboard    │                            │                        │
 │                       ├───────────────────────►│ generate_storyboard(prompt)│                        │
 │                       │                        ├── genblaze_openai.chat() ─►│ OpenAI chat completion │
 │                       │                        │    response_format=        │  (response_format JSON │
 │                       │                        │    StoryboardSpec          │   schema enforced)     │
 │                       │                        ├── backend().put(           │                        │
 │                       │                        │    .../storyboard.json)    ───────────────────────►│
 │                       │◄── { spec,             │                            │                        │
 │                       │     storyboard_key } ──┤                            │                        │
 │                       │                        │                            │                        │
 │  (optional)           │                        │                            │                        │
 │  edits scenes ◄──────►│ StoryboardReview                                    │                        │
 │                       │                        │                            │                        │
 │ click Generate        │ POST /api/proxy/       │                            │                        │
 │                       │   runs/media/stream    │                            │                        │
 │                       ├───────────────────────►│ (Stage A skipped if        │                        │
 │                       │                        │  req.spec is supplied;     │                        │
 │                       │                        │  else generate_storyboard) │                        │
 │                       │                        ├── build_keyframe_pipeline  │                        │
 │                       │                        │   .stream()                │                        │
 │                       │                        ├───────────────────────────►│ ImagenProvider × N     │
 │                       │                        │                            ├── PNG_i → B2 ─────────►│
 │                       │◄─ SSE: stream events ──┤                            │                        │
 │                       │                        │◄── PipelineCompletedEvent  │                        │
 │                       │                        │   (result = b1_result)     │                        │
 │                       │                        ├── build_media_pipeline     │                        │
 │                       │                        │   .from_result(B1)         │                        │
 │                       │                        │   image=presign(B1.png)    │                        │
 │                       │                        │   .stream()                │                        │
 │                       │                        ├───────────────────────────►│ DecartVideoProvider × N│
 │                       │                        │                            │ NvidiaAudioProvider × N│
 │                       │                        │                            │ GMICloudAudio × 1      │
 │                       │                        │                            ├── MP4/WAV/WAV → B2 ───►│
 │                       │◄─ SSE: stream events ──┤                            │                        │
 │                       │                        │◄── PipelineCompletedEvent  │                        │
 │                       │                        │                            │                        │
 │                       │                        ├── await asyncio.to_thread( │                        │
 │                       │                        │     compose_final, ...)    │                        │
 │                       │                        │     ffmpeg concat/mix/burn │                        │
 │                       │                        │     Mp4Handler().embed     │                        │
 │                       │                        │     backend.put(final.mp4) ──────────────────────────►│
 │                       │◄─ SSE: compose.complete┤                            │                        │
 │  final video + asset  │                        │                            │                        │
 │  list shown           │                        │                            │                        │
```

## Lineage in B2

Stages B1 and B2 each write a Manifest at
`explainers/<run-id>/manifest.json`. Stage B2's Manifest records
`parent_run_id = stage_b1.run_id`; the full visual-track lineage is
reconstructible by walking from the B2 root.

Stage A persists the storyboard JSON to `explainers/<uuid>/storyboard.json`
directly via `backend().put(...)` — there is no Manifest for Stage A
because there is no Pipeline Run (the underlying `chat()` is a
standalone function).

The final MP4 has the Stage B2 Manifest embedded as MP4 metadata via
`Mp4Handler().embed(...)` — so consumers of the video can extract the
B1/B2 provenance with `Mp4Handler().extract(...)` without making any
network call.

## Cross-pipeline image handoff

`Pipeline.from_result(prev_result)` records the parent run id in the new
Manifest but does NOT hydrate prior assets into provider kwargs. So
Stage B2 reaches into `keyframe_result.run.steps[i].assets[0]` and
presigns the durable B2 URL via
`S3StorageBackend.get_url(key, expires_in=900)` (the `presign_asset_url`
helper in `pipelines.py`). The presigned URL is then passed as
`image=<url>` to `DecartVideoProvider`.

This pattern matches `genblaze-gmicloud-pipeline.build_video_fanout` —
the canonical 0.3.x shape for cross-pipeline asset handoffs.
