# Exec Plan — Fail/error-state UX (revised after red-team)

Goal: a coherent three-severity UX — **blocked** (pre-flight advisory),
**degraded** (notice, run continues), **failed** (error, with recovery) —
without losing partial progress or leaking stack traces.

Key red-team corrections baked in:
- Classify off the SDK's typed `ProviderErrorCode` + `RETRYABLE_ERROR_CODES`,
  **not** brittle message substrings (substring fallback only for ffmpeg/unknown).
- **No new `step.failed` wire frame** — the SDK already emits failure events that
  `_stream_stage` forwards inside `stream` frames; derive live failure on the FE.
- **No `run_id` on the error frame** (3 runs → ambiguous); recovery uses `/files`.
- Readiness **warns, never disables** the CTA (60s-stale `/health`; preflight +
  cheap Stage-A failure already guard spend).
- One shared `AlertBanner` primitive for RunErrorPanel + ReadinessNotice +
  (refactored) HealthBanner. Media `onError` → one-shot placeholder, no loop.
- Retry is offered only when `retryable`, and its copy states it re-runs from
  the start and re-incurs provider cost.

## Step 1 — Tier 2: enum-first classification (backend)

New `services/api/app/errors.py`:

```python
@dataclass(frozen=True)
class ClassifiedError:
    code: str        # ProviderErrorCode value | "ffmpeg_missing" | "network" | "unknown"
    retryable: bool
    message: str     # clean one-liner (never an Exception repr / traceback)
    hint: str        # the next action
    status: int      # HTTP status for the Stage-A endpoint

def classify(exc: Exception) -> ClassifiedError: ...
```
Logic:
- `isinstance(exc, ProviderError)` and `exc.error_code` set → map the enum
  directly; `retryable = error_code in RETRYABLE_ERROR_CODES`. Per-code hint +
  status (AUTH_FAILURE→401, RATE_LIMIT→429, others→502). Use a short static
  `{code: (hint, status)}` table — no message sniffing.
- else substring fallback: `"ffmpeg binary not found"` → `ffmpeg_missing`
  (not retryable, hint "install ffmpeg — your source assets are saved in B2");
  default → `unknown` (retryable, 502, generic hint).
- `message` = a cleaned human string, never `f"{type}: {exc}"`.

Wiring (`main.py`):
- `/runs/storyboard`: try/except → `raise HTTPException(ce.status, detail=asdict(ce))`.
- SSE `error` frame: `{kind:"error", stage, code, retryable, message, hint}`
  built from `classify(exc)`. (No `run_id`.)

## Step 2 — Tier 1: recovery (frontend)

- `types/pipeline.ts`: extend `error` frame with `code?, retryable?, hint?`.
- `api-client.ts`: `ApiError` parses structured `detail` (`{code,message,hint,retryable}`)
  into optional fields; string `detail` still works.
- `studio-page.tsx`: add `runError` state set from the `error` frame / stream
  catch (keep the toast). `spec`, slots, `referenceUrl`, `musicUrl` already
  survive (verify). Add `retryMedia()` (re-run media stream from existing
  `spec`; clears `runError` + per-run visuals) and `editStoryboard()`
  (`setPhase("refining")`, clear `runError`).
- New `components/studio/run-error-panel.tsx`: an `AlertBanner` (error tone,
  `role="alert"`) with `message`, `hint`, a "Your generated assets are saved
  in B2 — View files" link, and actions **Retry** (only when `retryable`; copy
  notes it re-runs from the start + re-incurs cost) / **Edit storyboard** /
  **Start over**. Rendered in studio-page (persistent, below the header).
- `pipeline-canvas.tsx`: add `"error"` to `showStoryboard`/`showMedia` so the
  partial storyboard + media tiles stay visible under the panel.

## Step 3 — Tier 3: readiness advisory (backend + frontend)

- `/health`: add `ffmpeg_present: bool` (`shutil.which("ffmpeg")`).
- `lib/api-client.ts` `HealthResponse`: add `ffmpeg_present`.
- New `components/studio/readiness-notice.tsx` (`AlertBanner`, warning tone):
  shown only when something's missing. **Advisory, not blocking.** Copy maps
  each gap to its consequence: OpenAI key → "storyboard + keyframes will fail";
  ffmpeg → "the final MP4 can't be composed (assets still saved to B2)";
  Decart → "video falls back to keyframe stills"; NVIDIA → "no narration";
  GMI → "no music"; B2 → already covered by `HealthBanner`.

## Step 4 — Tier 4: live failure + media resilience (frontend only)

- Live failure: in `studio-page`, when a `stream` frame's `event.type` is a
  failure (`"step.failed"`, or `"step.completed"` with
  `event.step_status === "failed"`), mark the scene slot by `step_index` +
  stage (reuse the B2 2i/2i+1/last → video/narration/music mapping already in
  `harvestSlot`). `SceneSlots` gains `videoFailed?`/`narrationFailed?`; add
  `musicFailed`. Tiles flip from spinner to "failed → keyframe still" /
  "narration failed" immediately (pre-`done`), reconciled with the
  authoritative compose-time `notice` copy.
- Media `onError`: on `<video>/<audio>/<img>`, a **one-shot** swap (guarded by a
  per-element flag) to a "couldn't load — reload" placeholder. No cache-buster
  retry loop.

## Shared primitive
- `components/ui/alert-banner.tsx`: `{ tone: "error"|"warning", icon, title,
  children, actions? }` using `--destructive` / `--attention` tokens +
  `role="alert"`. Refactor `HealthBanner` onto it (removes the duplicated div).

## Cut / deferred
- App-level `step.failed` frame, error-frame `run_id`, CTA hard-disable,
  cache-buster auto-retry — all cut.
- Compose-only retry endpoint (reconstruct `PipelineResult`s from B2 manifests)
  — deferred; "Retry media" is the recovery path for now.

## Tests
- `tests/test_errors.py`: `ProviderError(error_code=…)` → code + `retryable`
  from `RETRYABLE_ERROR_CODES` + status; `RuntimeError("ffmpeg binary not
  found")` → `ffmpeg_missing`; bare `Exception` → `unknown`; assert no
  `repr`/traceback leaks into `message`.
- `/health` includes `ffmpeg_present`.
- `test_structure.py`: bump `main.py` budget if needed; line budget for
  `errors.py`.
- Frontend: `pnpm typecheck` + `pnpm lint`.
- Docs: update `ARCHITECTURE.md` §SSE wire format (error frame gains optional
  `code/retryable/hint`; no new frame kind) in the same change.

## Files
- `services/api/app/errors.py` (new), `app/main.py`, `tests/test_errors.py`.
- `apps/web/src/types/pipeline.ts`, `lib/api-client.ts`, `lib/queries.ts`,
  `components/ui/alert-banner.tsx` (new),
  `components/studio/run-error-panel.tsx` (new),
  `components/studio/readiness-notice.tsx` (new),
  `components/studio/studio-page.tsx`, `components/studio/pipeline-canvas.tsx`,
  `components/layout/health-banner.tsx` (refactor onto AlertBanner).
</content>
