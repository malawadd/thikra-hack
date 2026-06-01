# Architecture

## Layer diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ apps/web — Next.js App Router (React 19)                             │
│                                                                      │
│  page.tsx ──► PromptForm + StoryboardReview + SceneStrip             │
│             + PipelineProgress + FinalVideo + AssetList              │
│  lib/sse-client.ts ──► raw-fetch SSE parser (POST + stream)          │
│  lib/api.ts ──► typed helpers; everything routes through /api/proxy  │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ /api/proxy/<path>
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│ services/api — FastAPI                                               │
│                                                                      │
│  main.py        endpoints; returns Genblaze Run / Asset directly     │
│  config.py      pydantic-settings; B2_* + provider keys              │
│  types/         StoryboardSpec + request DTOs only                   │
│                                                                      │
│  repo/                                                               │
│    pipelines.py — ONLY file importing genblaze provider classes      │
│                   + the standalone `chat()` function for Stage A     │
│    composer.py  — ffmpeg orchestration (genblaze_core types only)    │
│                                                                      │
│  tests/                                                              │
│    test_structure.py — AST scan for boto3 / provider imports         │
│    test_pipelines_smoke.py — factory smoke tests (B1 + B2)           │
│    test_composer.py — mocked B2 + ffmpeg arg-shape tests             │
└────────────────────┬─────────────────────────────────┬───────────────┘
                     │ chat() + Pipeline.step(...)     │ subprocess.run(ffmpeg, ...)
                     ▼                                 │
┌──────────────────────────────────────────────────┐   │
│ genblaze-core + provider packages (PyPI)         │   │
│   genblaze-core, genblaze-s3,                    │   │
│   genblaze-openai, genblaze-decart,              │   │
│   genblaze-nvidia, genblaze-gmicloud             │   │
└──────────────┬───────────────────────────────────┘   │
               │                                       │
               ▼                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Backblaze B2 — `explainers/<run-id>/*`                               │
│   storyboard.json, scene_N.png, scene_N.mp4, scene_N_voice.wav,      │
│   music.wav, manifest.json, final.mp4                                │
└──────────────────────────────────────────────────────────────────────┘
```

The shape on the wire is **2 Pipelines + 1 ffmpeg compose**, plus one
standalone `chat()` function call for storyboard planning. There is no
Stage A Pipeline.

## Stages

### A — Storyboard planning (function, not a Pipeline)

- **Surface:** `genblaze_openai.chat()` — a standalone function exported
  from `genblaze_openai.__init__`. It does NOT implement the
  `BaseProvider` interface, so it cannot be passed to `Pipeline.step()`.
- **Idiom:** `chat(model, prompt=…, response_format=StoryboardSpec, api_key=…)`.
  `response_format` accepts a Pydantic `BaseModel` class directly — the
  function calls `coerce_response_format()` internally and the OpenAI
  endpoint enforces the JSON schema.
- **Persistence:** the storyboard JSON is written by hand to
  `explainers/<uuid>/storyboard.json` via `backend().put(...)` (there's
  no Pipeline Manifest covering this step).
- **Why a function and not a class:** see `docs/features/prompt-to-storyboard.md`.
  Filed as Genblaze SDK feedback.

### B1 — Keyframe fan-out

- **Pipeline:** `Pipeline("genblaze-gen-media-multi-provider-sample", max_concurrency=3)`.
  Stands alone — no `from_result()` anchor, because Stage A is not a Pipeline.
- **Provider:** `DalleProvider` (`gpt-image-1` default; `gpt-image-2`
  is the documented upgrade target).
- **Output:** one PNG per scene. The Stage B1 Manifest is the lineage
  root for the visual track in B2.

### B2 — Image-to-video + TTS + music

- **Pipeline:** `.from_result(stage_b1).max_concurrency=3`. The
  `from_result()` call records B1's `run_id` as the Stage B2 Manifest's
  `parent_run_id`, preserving B1 → B2 lineage in B2.
- **Cross-pipeline image handoff:** the keyframe asset URL is presigned
  via `S3StorageBackend.get_url(...)` and passed as the
  `image=<presigned-url>` kwarg to `DecartVideoProvider`. This is the
  canonical 0.3.x pattern; `from_result()` only records lineage in
  0.3.x — it does NOT hydrate prior assets into provider kwargs.
- **Per scene:** one `DecartVideoProvider` step + one `NvidiaAudioProvider` step.
- **Once at the end:** one `GMICloudAudioProvider` step for the music bed.
- **Output (Stage B2 Run):** `(video, narration) × N + (music,)` — the
  composer relies on this ordering when grouping scenes.

### C — Composition (NOT a Genblaze pipeline)

- **Module:** `app/repo/composer.py`
- **Why outside Genblaze:** no `genblaze-compose` / `genblaze-ffmpeg`
  package exists on PyPI as of 2026-05-28 (all 404). Filed as the
  primary SDK gap.
- **Steps:** concat per-scene visuals (resolution-normalizing `concat`
  filter) → mix **available** narration + ducked music (`amix` + `adelay`)
  → finalize captions (burn via the `subtitles`/libass filter when present,
  else soft `mov_text` track, else none) → embed Stage B2 Manifest via
  `Mp4Handler` (best-effort) → upload to B2 at `explainers/<run-id>/final.mp4`.
- **Every track is best-effort.** `compose_final(b2_run, b1_run, spec)`
  takes both the B2 result and the B1 keyframe result and returns
  `(Asset, notices)`; the notices become `notice` SSE frames.
  - *Video:* a failed Decart clip falls back to the scene's Stage B1
    keyframe still, looped to the scene duration (`-loop 1 -t`) and scaled to
    the common canvas so it concats cleanly with real clips. A scene is only
    fatal (raises → `error` frame) when BOTH its clip and keyframe are missing.
  - *Narration / music:* a failed/assetless audio step is mixed as silence
    or dropped; with no audio at all the final MP4 is silent (`-an`).
  - *Captions:* burned via the `subtitles` (libass) filter when the ffmpeg
    build has it, else muxed as a soft `mov_text` track, else omitted — a
    libass-less ffmpeg degrades instead of failing Stage C.
- **Execution:** `subprocess.run(["ffmpeg", ...], timeout=300, check=True)`,
  called from `main.py` via `await asyncio.to_thread(compose_final, ...)`
  inside an `async def` SSE generator. Stages B1 and B2 themselves run
  through `Pipeline.astream()` (native async), so the event loop is never
  blocked on provider HTTP between events either. No `ffmpeg-python`
  dependency.

## Ethos constraints

1. **`genblaze_*` imports confined to `app/repo/pipelines.py`** (and
   `composer.py` for `Asset` / `Manifest` / `Mp4Handler` types). Tested.
2. **No `boto3` / `botocore`.** Tested.
3. **FastAPI handlers return Genblaze models directly** — no DTO wrappers.
4. **`S3StorageBackend.for_backblaze(...)` called with explicit
   `key_id=` / `app_key=` kwargs** so the library's `B2_APP_KEY` env
   fallback never fires (parent-standard names: `B2_KEY_ID`,
   `B2_APPLICATION_KEY`).
5. **`preflight=True`** on the essential pipelines (B0/B1) — bad keys
   fail fast before any paid call. **Stage B2 sets `preflight=False`** and
   the caller runs it `fail_fast=False, raise_on_failure=False`: video,
   narration, and music are all best-effort, so a DEAD/failing model is
   contained as a single FAILED step rather than aborting the run at
   preflight (which validates *every* step). A FAILED best-effort run emits
   `PipelineFailedEvent` (not `PipelineCompletedEvent`); `_stream_stage`
   captures the result from **both** terminal events so the composer still
   sees every succeeded asset and degrades the rest.
6. **One Pipeline slug** (`genblaze-gen-media-multi-provider-sample`)
   across Stages B1 + B2 — Manifests differentiate via `parent_run_id`.

## SSE wire format

`POST /runs/media/stream` returns `text/event-stream`. Each frame is a
single `data: <json>\n\n` line. The JSON payload is one of:

```jsonc
// Stage boundary
{ "kind": "stage.start", "stage": "B1.keyframes" }
{ "kind": "stage.complete", "stage": "B1.keyframes" }

// Per-event from the underlying Pipeline.astream()
{ "kind": "stream", "stage": "B1.keyframes",
  "event": { "type": "step.completed", "step_id": "...", "provider": "openai", ... } }

// Synthetic per-asset frame — emitted alongside every step.completed
// because StepCompletedEvent.step is `exclude=True` in genblaze-core,
// so the wire JSON never carries the asset list. `asset_url` is a
// durable B2 URL; the frontend routes it through `/assets/{key}` for playback.
{ "kind": "scene.asset", "stage": "B2.media", "step_index": 0,
  "asset_url": "https://s3.<region>.backblazeb2.com/<bucket>/explainers/<run-id>/...",
  "media_type": "video/mp4" }

// Final
{ "kind": "compose.complete",
  "asset": { "url": "https://...", "media_type": "video/mp4", "sha256": "..." },
  "spec": { /* StoryboardSpec */ },
  "run_id": "..." }

// Best-effort degradation (narration/music unavailable) — a WARNING, not a
// failure. The run still completes with a final MP4. Emitted just before
// compose.complete, one per fallen-back track.
{ "kind": "notice", "stage": "B2.media", "message": "Background music unavailable — final video has no score." }

// Anywhere — a fatal failure. `code` / `retryable` / `hint` come from the
// backend classifier (app/errors.py): `code` is the SDK `ProviderErrorCode`
// (or "ffmpeg_missing" / "unknown"), `retryable` gates the UI's Retry action,
// `hint` is the next step. `message` is a clean one-liner — never a traceback.
// (Live per-step failures are NOT a separate frame: they ride the SDK's own
// `step.failed` / failed `step.completed` events inside `stream` frames.)
{ "kind": "error", "stage": "C.compose", "code": "ffmpeg_missing",
  "retryable": false, "message": "ffmpeg is not installed on the API host.",
  "hint": "Install ffmpeg (see infra/README.md). Your generated assets are saved in B2." }
```

The frontend's `streamSse()` helper (in `lib/sse-client.ts`) parses
these into a typed `SseFrame` union; the page accumulates them into the
`PipelineProgress` log and into per-scene slots for `SceneStrip`.

## Why `composer.py` lives in `repo/`

The composer is storage-adjacent infrastructure: it downloads source
assets from B2 via the same `S3StorageBackend` instance the pipelines
use, and uploads the final MP4 back to the same prefix. Putting it
under `services/api/app/repo/` keeps "I/O against B2" centralized and
makes its `Mp4Handler` + `Asset` imports the only `genblaze_core`
imports outside `pipelines.py` — easy to audit, easy to grep.

It would move into a hypothetical `genblaze-compose` package the day
one ships.

## Failure-mode policy

All failures are **classified** by `app/errors.py` (`classify()` — typed off
the SDK's `ProviderErrorCode` + `RETRYABLE_ERROR_CODES`, with a substring
fallback only for ffmpeg) into `{code, retryable, message, hint}`. Stage A
returns that as the HTTP error body; the streamed stages put it on the `error`
frame. The frontend renders a persistent `RunErrorPanel` (Retry — when
`retryable` — / Edit storyboard / Start over) and keeps the partial storyboard
+ media tiles visible. A pre-flight `ReadinessNotice` warns (never blocks) on
missing keys / ffmpeg from `/health`.

- **Stage A fails** → classified HTTP error (e.g. 401 auth, 429 rate-limit,
  502 provider); no B2 writes happened yet (Stage A persists storyboard.json
  only after a successful `chat()` + `model_validate_json()` round-trip).
  Recovery: Retry re-runs the storyboard from the seed prompt.
- **Stage B1 fails partway** → keyframes that completed are durable in
  B2 (sink writes as steps finish). The SSE stream emits an `error`
  frame; the UI shows what landed plus the error banner.
- **Stage B2 fails partway (video, narration, and/or music)** → contained,
  NOT fatal. Because B2 runs `preflight=False, fail_fast=False,
  raise_on_failure=False`, a DEAD/failing model becomes a single FAILED step
  and the run still returns a result (via `PipelineFailedEvent`). The
  composer degrades: a failed video clip falls back to the scene's keyframe
  still; failed audio is mixed as silence/dropped. Each fallback emits a
  `notice` frame. The only hard failure here is a scene that lost BOTH its
  clip and its keyframe (nothing to show) → `error` frame.
- **Stage C fails** → all source assets are durable in B2 under the
  Stage B2 run prefix. The user re-runs `/runs/media/stream` (which
  re-fires providers); there is no compose-only retry endpoint in the
  current shape.
- **Essential stages fail loud.** Stages A/B0/B1 surface every provider
  error and keep `preflight=True`, so a misconfigured key fails before any
  paid call fires. Best-effort suppression applies only inside Stage B2 +
  the composer.
