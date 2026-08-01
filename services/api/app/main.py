"""FastAPI surface for the multi-provider explainer pipeline.

Stage A (`generate_storyboard`) is a one-shot `genblaze_openai.chat()` call
— a function, not a Pipeline. Stages B1 (keyframes) and B2 (video + TTS +
music) are linked Pipelines streamed via `Pipeline.astream()` (native
async — no event-loop blocking). Stage C (ffmpeg composition) runs off
the event loop via `asyncio.to_thread`. Handlers return Genblaze models
(Run, Step, Asset, Manifest) directly; the only custom DTOs are request bodies.
"""

import asyncio
import json
import logging
import shutil
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import RedirectResponse, Response, StreamingResponse  # noqa: E402
from genblaze_core.observability.events import (  # noqa: E402
    PipelineCompletedEvent,
    PipelineFailedEvent,
    StepCompletedEvent,
)

from app.agents.mcp import mcp_app  # noqa: E402
from app.commerce.api import router as commerce_router  # noqa: E402
from app.commerce.discovery import router as discovery_router  # noqa: E402
from app.config import settings  # noqa: E402
from app.errors import classify  # noqa: E402
from app.http_middleware import request_logging  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.repo import (  # noqa: E402
    backend,
    build_keyframe_pipeline,
    build_media_pipeline,
    build_reference_pipeline,
    generate_storyboard,
    presign_asset_url,
    probe_storage,
    sink,
    snap_scene_durations,
)
from app.repo import provider_catalog as catalog  # noqa: E402
from app.repo.composer import compose_final  # noqa: E402
from app.startup import application_lifespan  # noqa: E402
from app.thikra import router as thikra_router  # noqa: E402
from app.types.api import MediaRequest, PromptRequest, ProviderChoice  # noqa: E402

setup_logging(settings.log_level)
logger = logging.getLogger("api.main")


# --- App -------------------------------------------------------------------
app = FastAPI(
    title="Thikra — Verify-Then-Pay Creative Commerce",
    description="Mandate-aware creative procurement with bounded payment, Genblaze orchestration, B2 provenance, layered verification, and redress.",
    version="1.0.0",
    lifespan=application_lifespan,
)
# Explicit origins via env (production), plus a regex that catches any
# localhost port so Next falling back to :3001/:3002 etc. doesn't break
# dev. The regex is permissive on purpose — only matches localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=(
        None if settings.app_mode.upper() == "PRODUCTION" else r"https?://localhost(:\d+)?"
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
)
app.include_router(thikra_router)
app.include_router(commerce_router)
app.include_router(discovery_router)
app.mount("/mcp", mcp_app)
app.middleware("http")(request_logging)


_sse_log = logging.getLogger("api.sse")


def _sse(payload: dict) -> str:
    """Format an SSE `data:` frame. One-liner so callers can inline yield.

    Also logs the frame at DEBUG so the engineer can replay exactly what
    the browser received without dropping into devtools network tab.
    """
    if _sse_log.isEnabledFor(logging.DEBUG):
        # Trim verbose event payloads in the log line — full payloads
        # still flow over the wire to the client.
        log_payload = dict(payload)
        if log_payload.get("kind") == "stream":
            ev = log_payload.get("event", {})
            if isinstance(ev, dict):
                log_payload["event"] = {
                    k: ev.get(k) for k in ("type", "step_index", "model", "timestamp") if k in ev
                }
        _sse_log.debug("sse out", extra={"payload": log_payload})
    return f"data: {json.dumps(payload, default=str)}\n\n"


async def _stream_stage(
    pipeline,
    stage: str,
    *,
    timeout: int,
    fail_fast: bool = True,
    raise_on_failure: bool = True,
):
    """Run a pipeline via `astream()` and yield (sse_string, captured_result).

    `fail_fast`/`raise_on_failure` default to strict (essential stages B0/B1
    fail loud). The best-effort B2 media stage passes both `False` so a
    failing audio step is contained as a FAILED step and video siblings still
    complete; the run then returns a result instead of raising. Passing
    `raise_on_failure` explicitly (never `None`) also avoids the SDK's
    0.4.0-default-flip `DeprecationWarning`.

    Synthesises a `scene.asset` SSE frame for every `step.completed` event
    that carries a populated `step.assets[0]`. Without this the wire would
    never see asset URLs because `StepCompletedEvent.step` is
    `exclude=True` in the Pydantic serializer (Genblaze SDK quirk —
    `genblaze_core/observability/events.py` excludes the rich `step` field
    so SSE bridges have to re-synthesise the asset payload). `step_index`
    is the event's own 0-based position in the pipeline, so the frontend
    can route the URL to the right scene slot deterministically.
    """
    captured: Any = None
    stage_log = logging.getLogger("api.stream_stage")
    stage_log.info("stage start", extra={"stage": stage, "timeout_sec": timeout})
    async for evt in pipeline.astream(
        sink=sink(),
        timeout=timeout,
        fail_fast=fail_fast,
        raise_on_failure=raise_on_failure,
    ):
        evt_type = getattr(evt, "type", evt.__class__.__name__)
        stage_log.debug(
            "event",
            extra={
                "stage": stage,
                "event_type": evt_type,
                "step_index": getattr(evt, "step_index", None),
                "model": getattr(evt, "model", None),
            },
        )
        yield (
            _sse({"kind": "stream", "stage": stage, "event": evt.model_dump(mode="json")}),
            captured,
        )
        # NB: this reads `evt.step` off the LIVE in-process event object —
        # `step` is `exclude=True`, so it's dropped by the `model_dump` above
        # and is `None` on any re-parsed/wire copy. The `evt.step.assets`
        # truthiness guard also makes the `[0]` index safe on an assetless step.
        if isinstance(evt, StepCompletedEvent) and evt.step and evt.step.assets:
            asset = evt.step.assets[0]
            stage_log.info(
                "step completed",
                extra={
                    "stage": stage,
                    "step_index": evt.step_index,
                    "model": getattr(evt, "model", None),
                    "size_bytes": getattr(asset, "size_bytes", None),
                },
            )
            yield (
                _sse(
                    {
                        "kind": "scene.asset",
                        "stage": stage,
                        "step_index": evt.step_index,
                        "asset_url": asset.url,  # durable URL — frontend hits /assets/{key}
                        "media_type": asset.media_type,
                    }
                ),
                captured,
            )
        # Capture the run result from EITHER terminal event. A best-effort
        # stage (raise_on_failure=False) that ends with status=FAILED emits a
        # `PipelineFailedEvent` — NOT `PipelineCompletedEvent` — but its result
        # still carries every succeeded step's assets (e.g. the video clips
        # when only audio failed). Surfacing that result lets Stage C compose
        # from what survived; the composer decides essential (video, with a
        # keyframe-still fallback) vs best-effort (audio). Strict stages raise
        # after this event, so the captured result is superseded by the error.
        if (
            isinstance(evt, (PipelineCompletedEvent, PipelineFailedEvent))
            and evt.result is not None
        ):
            captured = evt.result
            stage_log.info(
                "stage complete",
                extra={
                    "stage": stage,
                    "run_status": str(getattr(evt.result.run, "status", None)),
                    "run_id": getattr(evt.result.run, "run_id", None),
                    "step_count": len(evt.result.run.steps),
                },
            )
            yield _sse({"kind": "stage.complete", "stage": stage}), captured


# --- Endpoints -------------------------------------------------------------


@lru_cache(maxsize=1)
def _ffmpeg_present() -> bool:
    """Whether `ffmpeg` is on PATH — a deploy-time invariant, probed once."""
    return shutil.which("ffmpeg") is not None


@app.get("/health")
def health():
    """Liveness probe — B2 reachability + provider key presence + ffmpeg.

    `ffmpeg_present` lets the UI warn before a run that Stage C (compose) will
    fail — ffmpeg is only exercised at the very end, after minutes of paid
    B-stage generation. `status` stays tied to B2 (the `HealthBanner` contract).

    Sync `def` (NOT `async`): `probe_storage()` makes a blocking B2 call, so
    Starlette runs this in the threadpool. A stalled B2 probe must never block
    the event loop — the frontend polls this on an interval, and a wedged loop
    takes down every endpoint. (Same principle as AGENTS Rule 6's ffmpeg →
    `asyncio.to_thread`: keep blocking I/O off the event loop.)
    """
    b2_ok = probe_storage()
    # ffmpeg presence is a deploy-time invariant — cache it so a per-tab 60s
    # poll doesn't re-scan PATH. B2 reachability CAN flap, so it stays live.
    ffmpeg_ok = _ffmpeg_present()
    logger.debug("health probe", extra={"b2_connected": b2_ok, "ffmpeg_present": ffmpeg_ok})
    return {
        "status": "healthy" if b2_ok else "degraded",
        "b2_connected": b2_ok,
        "ffmpeg_present": ffmpeg_ok,
        "providers": {
            "openai_key_present": bool(settings.openai_api_key),
            "replicate_key_present": bool(settings.replicate_api_token),
            "google_key_present": bool(settings.google_api_key),
            "nvidia_key_present": bool(settings.nvidia_api_key),
            "decart_key_present": bool(settings.decart_api_key),
            "gmi_key_present": bool(settings.gmi_api_key),
            "runway_key_present": bool(settings.runway_api_secret),
            "luma_key_present": bool(settings.luma_api_key),
            "elevenlabs_key_present": bool(settings.elevenlabs_api_key),
            "lmnt_key_present": bool(settings.lmnt_api_key),
            "hume_key_present": bool(settings.hume_api_key),
        },
    }


@app.get("/providers")
def get_providers():
    """The switchboard catalog — drives the UI's per-modality vendor/model pickers.

    Returns `{modality: [{vendor, default_model, suggested_models, modality,
    key_available}]}`. `key_available` reflects which API keys are configured,
    so the UI can grey out vendors the operator hasn't set up. Pure dict
    construction (no I/O, no provider instantiation) → sync `def`.
    """
    return {"providers": catalog.matrix()}


def _resolve_choice(slot: str, choice: ProviderChoice) -> tuple[catalog.CatalogEntry, str]:
    """Resolve a `ProviderChoice` to `(entry, model)`; 422 on an unknown vendor.

    Model-slug correctness is NOT checked here — providers validate slugs
    against regex families at call time (and Replicate accepts any
    `owner/model`), so a bad slug surfaces at preflight/runtime, classified
    like any other provider error. `choice.model=None` falls back to the
    catalog's curated `default_model` for the vendor.
    """
    try:
        entry = catalog.resolve(slot, choice.vendor)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "bad_selection",
                "retryable": False,
                "message": str(exc),
                "hint": f"Pick a vendor listed under '{slot}' in GET /providers.",
            },
        ) from exc
    return entry, (choice.model or entry.default_model)


@app.post("/runs/storyboard")
def create_storyboard(req: PromptRequest):
    """Stage A only — returns the bare spec for optional client-side refinement.

    On provider failure we return a *classified* error body (the same shape the
    SSE `error` frame uses) so the client can show an actionable message + hint
    instead of a raw 500 / `Internal Server Error`.
    """
    logger.info(
        "storyboard endpoint",
        extra={
            "endpoint": "POST /runs/storyboard",
            "prompt_chars": len(req.prompt),
            "prompt_preview": req.prompt[:240],
        },
    )
    try:
        spec, storyboard_key = generate_storyboard(req.prompt)
    except Exception as exc:
        ce = classify(exc)
        logger.exception("storyboard failed", extra={"code": ce.code})
        raise HTTPException(status_code=ce.status, detail=ce.as_dict()) from exc
    return {"spec": spec, "storyboard_key": storyboard_key}


@app.post("/runs/media/stream")
def stream_media(req: MediaRequest):
    """Stages B1 + B2 + C as a single SSE stream.

    Stage A runs synchronously up front (one `chat()` call, ~3s) when no
    client-refined spec is supplied. Stages B1 and B2 are streamed via
    `Pipeline.astream()` — the async variant — so the FastAPI event
    loop is never blocked on provider HTTP between events. Stage C is
    ffmpeg-only and is dispatched via `asyncio.to_thread` so the loop
    stays responsive during the ~10-30s compose.
    """
    log = logging.getLogger("app.stream_media")
    # Resolve the per-modality provider selection BEFORE streaming starts, so an
    # unknown vendor returns a clean 422 (not a mid-stream error frame).
    sel = req.selection
    chat_entry, chat_model = _resolve_choice(catalog.CHAT, sel.chat)
    image_entry, image_model = _resolve_choice(catalog.IMAGE, sel.image)
    video_entry, video_model = _resolve_choice(catalog.VIDEO, sel.video)
    tts_entry, tts_model = _resolve_choice(catalog.TTS, sel.tts)
    music_entry, music_model = _resolve_choice(catalog.MUSIC, sel.music)
    log.info(
        "media stream endpoint",
        extra={
            "endpoint": "POST /runs/media/stream",
            "prompt_chars": len(req.prompt),
            "prompt_preview": req.prompt[:240],
            "spec_provided": req.spec is not None,
            "scene_count": len(req.spec.scenes) if req.spec else None,
            "selection": {
                "chat": f"{chat_entry.vendor}/{chat_model}",
                "image": f"{image_entry.vendor}/{image_model}",
                "video": f"{video_entry.vendor}/{video_model}",
                "tts": f"{tts_entry.vendor}/{tts_model}",
                "music": f"{music_entry.vendor}/{music_model}",
            },
        },
    )
    # Snap scene durations to the selected video provider's grid (e.g. Kling
    # renders 5s/10s only) so B2 and the composer share one clip-length truth.
    spec = snap_scene_durations(
        req.spec or generate_storyboard(req.prompt, chat_model)[0],
        video_entry,
    )

    async def gen():
        # Outer try/except converts ANY uncaught exception (provider failure,
        # auth error, timeout) into a final SSE `error` frame so the client
        # sees the cause instead of ERR_INCOMPLETE_CHUNKED_ENCODING. Without
        # this, an exception kills the async generator mid-stream and FastAPI
        # closes the response without a terminator chunk.
        current_stage = "B0.reference"
        try:
            # Stage B0 — single reference image locking the visual style.
            # Its prompt is the spec's `style_prompt`; B1 then prefixes that
            # same style onto each per-scene prompt for visual consistency.
            yield _sse({"kind": "stage.start", "stage": current_stage})
            b0_result = None
            async for frame, captured in _stream_stage(
                build_reference_pipeline(spec, image_entry, image_model),
                current_stage,
                timeout=240,
            ):
                yield frame
                if captured is not None:
                    b0_result = captured
            if b0_result is None:
                yield _sse(
                    {
                        "kind": "error",
                        "stage": current_stage,
                        "message": "no pipeline result captured",
                    }
                )
                return

            # Stage B1 — keyframe fan-out (one image per scene), seeded
            # via shared style-prompt prefix from B0's spec.
            current_stage = "B1.keyframes"
            yield _sse({"kind": "stage.start", "stage": current_stage})
            b1_result = None
            async for frame, captured in _stream_stage(
                build_keyframe_pipeline(spec, image_entry, image_model, b0_result),
                current_stage,
                timeout=600,
            ):
                yield frame
                if captured is not None:
                    b1_result = captured
            if b1_result is None:
                yield _sse(
                    {
                        "kind": "error",
                        "stage": current_stage,
                        "message": "no pipeline result captured",
                    }
                )
                return

            # Stage B2 — image-to-video + TTS + music. Cross-pipeline image
            # handoff uses `image=<presigned>` provider kwargs (see pipelines.py).
            # Run best-effort: video, narration, and music can each fail without
            # aborting the run (fail_fast=False / raise_on_failure=False). A
            # scene whose video clip fails falls back to its Stage B1 keyframe
            # still in the composer, so the run always produces a final MP4.
            current_stage = "B2.media"
            yield _sse({"kind": "stage.start", "stage": current_stage})
            b2_result = None
            async for frame, captured in _stream_stage(
                build_media_pipeline(
                    spec,
                    b1_result,
                    video_entry=video_entry,
                    video_model=video_model,
                    tts_entry=tts_entry,
                    tts_model=tts_model,
                    music_entry=music_entry,
                    music_model=music_model,
                ),
                current_stage,
                timeout=900,
                fail_fast=False,
                raise_on_failure=False,
            ):
                yield frame
                if captured is not None:
                    b2_result = captured
            if b2_result is None:
                yield _sse(
                    {
                        "kind": "error",
                        "stage": current_stage,
                        "message": "no pipeline result captured",
                    }
                )
                return

            # Stage C — compose (sync ffmpeg, off the event loop). AGENTS Rule 6
            # requires `asyncio.to_thread` so the SSE stream stays responsive.
            # `compose_final` returns degradation notices for any best-effort
            # audio that fell back; we relay them as `notice` frames (warnings,
            # not errors) so the UI can state what's missing.
            current_stage = "C.compose"
            yield _sse({"kind": "stage.start", "stage": current_stage})
            # Pass the Stage B1 keyframe result so the composer can substitute a
            # scene's keyframe still for any failed video clip.
            final_asset, notices = await asyncio.to_thread(
                compose_final,
                b2_result,
                b1_result,
                spec,
            )
            for message in notices:
                yield _sse({"kind": "notice", "stage": "B2.media", "message": message})
            # `manifest_uri` is the durable B2 URL of the Stage B2 Manifest
            # (provenance: pipeline name, parent_run_id, per-step assets,
            # canonical_hash). We surface it to the client so the user can
            # open and inspect the verification artifact alongside the MP4.
            manifest_uri = getattr(b2_result.manifest, "manifest_uri", None)
            yield _sse(
                {
                    "kind": "compose.complete",
                    "asset": final_asset.model_dump(mode="json"),
                    "spec": spec.model_dump(mode="json"),
                    "run_id": b2_result.run.run_id,
                    "manifest_uri": manifest_uri,
                }
            )
        except Exception as exc:
            # logger.exception emits the full traceback to the [api] log so the
            # engineer can diagnose; the SSE frame carries the *classified*
            # error (clean message + actionable hint + retryable) so the client
            # can offer the right recovery — never a raw Exception repr.
            log.exception("stream_media failed at stage=%s", current_stage)
            ce = classify(exc)
            yield _sse(
                {
                    "kind": "error",
                    "stage": current_stage,
                    "code": ce.code,
                    "retryable": ce.retryable,
                    "message": ce.message,
                    "hint": ce.hint,
                }
            )

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/runs/{run_id}/assets")
def list_run_assets(run_id: str):
    """Enumerate B2 keys under `explainers/<run_id>/` — powers the per-run asset list.

    Sync `def`: `backend().list()` is a blocking B2 call — kept off the event
    loop via the threadpool (see `health`)."""
    prefix = f"explainers/{run_id}/"
    try:
        page = backend().list(prefix, max_keys=200)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    entries = [
        {
            "key": e.key,
            "size": e.size,
            "last_modified": e.last_modified.isoformat() if e.last_modified else None,
        }
        for e in page.entries
    ]
    return {"prefix": prefix, "entries": entries}


@app.get("/files")
def list_files():
    """Enumerate every B2 key under `explainers/` — powers the /files page.

    Cap at 500 entries; the sample's storage scales linearly with runs, but
    a full browser is out of scope (point users at the B2 console for that).

    Sync `def`: `backend().list()` is a blocking B2 call — runs in the
    threadpool, off the event loop (see `health`).
    """
    try:
        page = backend().list("explainers/", max_keys=500)
    except Exception as exc:
        logger.exception("list files failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.debug("list files ok", extra={"count": len(page.entries)})
    entries = [
        {
            "key": e.key,
            "size": e.size,
            "last_modified": e.last_modified.isoformat() if e.last_modified else None,
        }
        for e in page.entries
    ]
    return {"prefix": "explainers/", "entries": entries}


@app.get("/assets/{key:path}")
def get_asset(key: str, inline: bool = False):
    """Serve a B2 object: 302 to a presigned URL, or proxy the bytes inline.

    Default (`inline=0`) redirects to a short-lived presigned URL — media tiles
    load these via `<img>`/`<video>` src, which CORS doesn't gate. The manifest
    viewer uses `fetch()` instead, which DOES follow the 302 into B2's
    cross-origin presigned URL and is then blocked (B2 sets no
    `Access-Control-Allow-Origin`). `inline=1` proxies the bytes through FastAPI
    (same-origin, CORS-allowed) so `fetch()` can read small JSON artifacts.

    Sync `def`: the B2 presign/get are blocking — kept off the event loop via
    the threadpool (see `health`)."""
    try:
        if inline:
            return Response(content=backend().get(key), media_type="application/json")
        url = presign_asset_url(key)
    except Exception as exc:
        logger.warning("asset 404", extra={"key": key, "exception": str(exc)})
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.debug("asset redirect", extra={"key": key})
    return RedirectResponse(url=url, status_code=302)
