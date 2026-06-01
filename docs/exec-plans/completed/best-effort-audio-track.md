# Exec Plan — Best-effort audio (narration + music) track

## Problem

Stage B2 is a single pipeline emitting `(video, tts) × N + (music,)`. Genblaze's
preflight `Pipeline._validate_models()` validates **every** step before any runs;
a DEAD model (e.g. the retired `MiniMax-Music-1`) raises `ProviderError` at
preflight, aborting the **entire** run — video included. Fixing the music slug
(`minimax-music-2.5`, already done) removes today's symptom but not the
fragility: any future DEAD/failing audio model re-breaks the whole run.

Requirement: the run must always produce a final MP4 from the video track.
Narration (NVIDIA TTS) and music (GMICloud) are **best-effort** — if either fails
at preflight (DEAD model) or runtime (provider error), the run continues, the UI
**states** the degradation, and there is no hard error.

## Design (revised after red-team)

Keep the **single** Stage B2 pipeline. Push the essential-vs-best-effort boundary
into the composer; let runtime — not preflight — surface failures, contained by
`fail_fast=False`. This avoids a topology split, a second factory, and a
build-time `validate_model` probe (which fires a billable, audit-logged GMI call
on the happy path).

### Backend (`services/api/app/repo/pipelines.py`)

- `build_media_pipeline` unchanged except construct the B2 `Pipeline` with
  `preflight=False`. A DEAD audio model then fails at **runtime** as a FAILED
  step instead of aborting preflight. Video keeps the same per-scene Decart
  handoff. Comment explains the call-site contract (run with `fail_fast=False`).

### Backend (`services/api/app/main.py`)

- `_stream_stage(pipeline, stage, *, timeout, fail_fast=True, raise_on_failure=True)`.
  - B0/B1 (essential): strict defaults — a failure raises → outer `except` → an
    `error` frame, as today. Passing explicit `raise_on_failure=True` also kills
    the `DeprecationWarning` the `None` default currently emits on every stage.
  - B2.media: `fail_fast=False, raise_on_failure=False` so a failing TTS/music
    step is contained (FAILED, assetless) and video siblings still complete; the
    run returns a `PipelineResult` rather than raising.
- After B2: `asset, notices = compose_final(b2_result, spec)` (off-loop via
  `asyncio.to_thread`). Emit a `notice` SSE frame per degradation message, then
  `compose.complete`. Video failure still surfaces as an `error` (composer raises
  — see below).

### Backend (`services/api/app/repo/composer.py`)

With `fail_fast=False`, the result preserves step **order** (`_gather_fail_fast`
sorts by index) but a failed step is present **with no assets**. So detect by
asset *presence*, not by assuming `[0]`:

- `_asset_url_or_none(step) -> str | None` — `step.assets[0].url` or `None`.
- `_SceneBundle.narration_path: Path | None`.
- `_group_scenes`: video per scene is **required** — a missing/assetless video
  step raises `RuntimeError` (→ `error` frame; video stays essential).
  Narration is optional (`None` when its step is assetless).
- `_music_url(b2_run) -> str | None` — guards `steps[-1]` length + asset presence
  (replaces the unconditional `steps[-1].assets[0]` that would `IndexError` on a
  FAILED music step).
- `audio_notices(b2_run, spec) -> list[str]` — human messages for missing
  narration/music; shares the same presence helpers (DRY).
- `_mix_audio(scenes, music_path | None, tmp) -> Path | None` — build the
  `adelay`+`amix` graph only from available narration tracks + music; return
  `None` when **no** audio exists.
- `_burn_captions(video, audio | None, scenes, tmp)` — mux audio when present;
  otherwise `-an` (silent video) with captions still burned.
- `compose_final(b2_run, spec) -> tuple[Asset, list[str]]`.

### Frontend

- `types/pipeline.ts`: add `{ kind: "notice"; stage; message }` to `SseFrame`.
- `studio-page.tsx`: handle `notice` → `toast.warning` + push to inspector;
  phase stays `generating`/`done` (never `error`). Existing `B2.media`
  `step_index` routing is unchanged (no stage rename, no `role` tag).
- `pipeline-canvas.tsx`: `allDone` must not require `!!musicUrl`; when
  `phase==="done"` and a slot is empty, render "score unavailable" /
  "narration unavailable" instead of a perpetual loader.

### Tests

- `test_pipelines_smoke.py`: assert the media pipeline is built with preflight
  disabled (`getattr(p, "_preflight") is False`); keep step-count coverage.
- `test_composer.py`: happy path (now returns `(asset, notices)`); music step
  assetless → no `IndexError`, narration-only mix, notice emitted; all narration
  assetless + music present → music-only mix (assert the filter string, don't
  just mock `_run_ffmpeg`); no audio at all → silent video (`-an`, no `1:a` map,
  assert argv); per-scene video missing → `RuntimeError`.
- Pin the SDK ordering invariant the composer relies on, and ensure no
  `DeprecationWarning` leaks (explicit `raise_on_failure`).

## Out of scope

- Per-scene video resilience (video stays essential — a video failure is a real
  error).
- Retry/backoff tuning for audio providers; build-time model gating (rejected:
  duplicates SDK preflight and fires an audit-logged probe on the happy path).

## Files

- `services/api/app/repo/pipelines.py`, `app/main.py`, `app/repo/composer.py`
- `apps/web/src/types/pipeline.ts`, `studio-page.tsx`, `pipeline-canvas.tsx`
- `services/api/tests/test_pipelines_smoke.py`, `test_composer.py`
- Docs: `README.md`, `docs/features/media-generation.md`, `ARCHITECTURE.md`
</content>
