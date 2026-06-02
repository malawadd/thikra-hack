# AGENTS.md — hard rules for AI assistants

This file is the source of truth for anyone (human or model) modifying
this sample. If something here conflicts with a generic instinct, this
file wins.

## Sample-local instructions

Follow [parent CLAUDE.md](../CLAUDE.md) and the engineering working
agreement at all times. The following rules are specific to this sample:

### Rule 1: never add `boto3` or `botocore`

Storage is fully delegated to `genblaze-s3`. A structural test
(`services/api/tests/test_structure.py::test_no_direct_aws_client_imports`)
fails CI if either module is imported anywhere under
`services/api/app/`. There is no allow-list — this sample has no
`_client` reach-through.

If you need a B2 capability `genblaze-s3` doesn't expose (e.g. a new
list/copy/permission helper), file an SDK gap in the build feedback
section of the next sample build and use the existing primitives in
the meantime. Do not add a side-channel client.

### Rule 2: never wrap Genblaze response types

FastAPI handlers return `Run`, `Step`, `Asset`, `Manifest` instances
directly. The only custom DTOs in this codebase are request bodies
(`PromptRequest`, `MediaRequest`). If you need a field a Genblaze
model doesn't have, file SDK feedback — don't shadow the type with a
mirrored Pydantic class.

### Rule 3: `genblaze_*` imports live in `app/repo/pipelines.py`

- `pipelines.py` is the only file that imports provider classes
  (`DalleProvider`, `DecartVideoProvider`, etc.), `Pipeline`, and the
  standalone `genblaze_openai.chat()` function used for Stage A.
- `composer.py` may import `genblaze_core` *types* only
  (`Asset`, `Manifest`, `Mp4Handler` from `genblaze_core.media`) — no
  `Pipeline` or `Provider` use.
- `main.py` / `app/types/**` may not import from any `genblaze_*` package.
  Use the re-exports in `app/repo/__init__.py` instead.

The structural test
(`test_pipelines_is_the_only_genblaze_provider_consumer`) enforces this.

### Rule 4: adding a new provider = one `.step()` in `pipelines.py`

When the next provider lands on PyPI as `genblaze-<vendor>`:

1. Add the dependency to `services/api/pyproject.toml` and re-pin
   `requirements.txt` with `uv pip compile`.
2. Import the provider class in `app/repo/pipelines.py` (no other file).
3. Wire it into the appropriate stage as a `.step()`. The slug
   (`PIPELINE_NAME`) does NOT change — Manifest lineage handles
   differentiation through `parent_run_id`.
4. If the new provider produces audio/video/image assets that the
   composer needs to read, extend `_group_scenes()` in `composer.py`.
5. Update `docs/features/media-generation.md`.

### Rule 5: cross-pipeline asset handoff uses the `image=<presigned>` kwarg

Genblaze 0.3.x `from_result()` only records lineage; it does NOT
hydrate prior step assets into provider kwargs. The Stage B1 → Stage B2
image-to-video handoff therefore goes through
`presign_asset_url(...)` + the `image=` provider kwarg, matching the
canonical pattern in `genblaze-gmicloud-pipeline.build_video_fanout`.

Do not introduce `input_from=<step_index>` for cross-pipeline handoffs.
`input_from=` is for within-pipeline fan-out from a shared upstream
step (this sample doesn't use it; the fan-out is at `max_concurrency=3`
across sibling `.step()` calls within a single Pipeline).

### Rule 6: composer is the ONLY ffmpeg surface

`repo/composer.py` is the lone non-Genblaze media-processing module
because the SDK ships no composition primitive. Do not add ffmpeg calls
elsewhere. Do not add `ffmpeg-python` (the explicit `subprocess.run([...])`
shape is intentional — one fewer dependency layer, matches production
media pipelines). Call the composer from `main.py` via
`asyncio.to_thread(...)` so the FastAPI event loop never blocks.

### Rule 7: preflight stays on for the ESSENTIAL pipelines

The essential pipelines — B0 (reference) and B1 (keyframes) — are
constructed with the default `preflight=True`. This catches a misconfigured
`OPENAI_API_KEY` before any paid call fires. Do not set `preflight=False` on
these for dev convenience.

**Stage B2 (video + TTS + music) is the deliberate exception:** it is built
`preflight=False` and run `fail_fast=False, raise_on_failure=False` because
video, narration, and music are best-effort — a DEAD/failing model must be
contained as one FAILED step (then degraded by the composer) rather than
aborting the run at preflight, which validates *every* step. See
ARCHITECTURE.md §"Ethos constraints" #5. Keep this divergence intentional and
documented; do not "restore" `preflight=True` on B2.

Stage A (`genblaze_openai.chat()`) has no preflight surface; a bad
`OPENAI_API_KEY` will surface as a `ProviderError` from `chat()` itself
on the first call (classified by `app/errors.py`).

### Rule 8: env var names follow the parent standard

`.env` uses `B2_REGION`, `B2_KEY_ID`, `B2_APPLICATION_KEY`,
`B2_BUCKET_NAME` — no aliases. Never use `B2_APP_KEY` (that's the
Genblaze env fallback; we pass explicit `app_key=` kwargs into
`S3StorageBackend.for_backblaze()`).

**Deliberate divergence from parent CLAUDE.md §3:** this sample has no
`B2_ENDPOINT`. `S3StorageBackend.for_backblaze()` derives the endpoint
from `region` internally, and carrying a dead env var would mislead
readers into thinking the sample consumes it. Parent rule predates the
Genblaze library handling endpoint derivation.

### Rule 9: docs change in the same PR as code

If you touch `pipelines.py`, update `docs/features/media-generation.md`
or `docs/features/composition.md` in the same change. If you touch
`composer.py`, update `docs/features/composition.md`. The structural
tests do not catch doc drift; reviewers do.

## Test commands

```bash
# Backend
cd services/api && uv run pytest tests/ -x

# Frontend type-check
cd apps/web && pnpm typecheck
```

Always run the structural tests before opening a PR; they're cheap.
