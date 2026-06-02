"""Genblaze pipeline factories — the only file that imports `genblaze_*` for pipelines.

Stage A is a one-shot `genblaze_openai.chat()` call (a standalone function,
NOT a `BaseProvider`), so it cannot ride `Pipeline.step()`. Stages B1 and B2
are linked Pipelines; B2 reaches into B1's PipelineResult and hands each
keyframe asset to `DecartVideoProvider` via the canonical `image=<presigned>`
kwarg (`from_result()` only records lineage in 0.3.x).
"""

import json
import logging
import time
import uuid
from dataclasses import replace
from functools import lru_cache

from genblaze_core import (
    Asset,
    KeyStrategy,
    Modality,
    ObjectStorageSink,
    Pipeline,
    StepCache,
)
from genblaze_core.observability import CompositeTracer, LoggingTracer, OTelTracer
from genblaze_core.providers.model_registry import ModelRegistry
from genblaze_decart import DecartVideoProvider
from genblaze_gmicloud import GMICloudAudioProvider, GMICloudVideoProvider
from genblaze_gmicloud.models.audio import build_audio_registry
from genblaze_google import ImagenProvider
from genblaze_nvidia import NvidiaAudioProvider
from genblaze_openai import chat
from genblaze_s3 import S3StorageBackend

from app.config import settings
from app.types.storyboard import StoryboardSpec

PIPELINE_NAME = "genblaze-gen-media-multi-provider-sample"
PREFIX = "explainers"

logger = logging.getLogger("api.pipelines")


# --- Backend + sink singletons ----------------------------------------------

@lru_cache(maxsize=1)
def backend() -> S3StorageBackend:
    """Singleton backend — explicit kwargs bypass the library's B2_APP_KEY env fallback."""
    return S3StorageBackend.for_backblaze(
        settings.b2_bucket_name,
        region=settings.b2_region,
        key_id=settings.b2_key_id,
        app_key=settings.b2_application_key,
        auto_lifecycle=True,
    )


def sink() -> ObjectStorageSink:
    """Per-run hierarchical layout: `explainers/<run-id>/...`."""
    return ObjectStorageSink(backend(), prefix=PREFIX, key_strategy=KeyStrategy.HIERARCHICAL)


def _tracer() -> CompositeTracer:
    tracers = [LoggingTracer()]
    if settings.otel_endpoint:
        tracers.append(OTelTracer(endpoint=settings.otel_endpoint))
    return CompositeTracer(tracers)


def _attach(p: Pipeline) -> Pipeline:
    """Attach tracer + step-cache to every pipeline."""
    return p.tracer(_tracer()).cache(StepCache(settings.step_cache_dir))


def presign_asset_url(key_or_url: str, *, expires_in: int = 900) -> str:
    """Presign a key OR durable Manifest/Asset URL for B1→B2 image handoff
    and frontend playback. Unrecognized http URLs raise — callers presign
    known sink URLs only."""
    if key_or_url.startswith("http"):
        key = backend().key_from_url(key_or_url)
        if key is None:
            logger.error("presign: unrecognized B2 URL", extra={"input": key_or_url})
            raise ValueError(f"Unrecognized B2 asset URL: {key_or_url}")
    else:
        key = key_or_url
    presigned = backend().get_url(key, expires_in=expires_in)
    logger.debug("presigned asset", extra={
        "key": key, "expires_in_sec": expires_in,
    })
    return presigned


def probe_storage() -> bool:
    """Health check — True if the backend can reach the bucket."""
    try:
        backend().exists("__genblaze_health_probe__")
        return True
    except Exception:
        return False


# --- Stage A: storyboard planning -------------------------------------------

_STORYBOARD_INSTRUCTION = (
    "You are a storyboard writer for a short narrated explainer video. "
    "Given the seed below, produce a JSON storyboard with 4-6 scenes. Each "
    "scene's `duration_sec` MUST be exactly 5 or 10 (the video model only "
    "renders 5s or 10s clips); aim for a 30-60 second total. First pick a "
    "`style_prompt`: "
    "ONE sentence locking the visual look every scene must share "
    "(palette + illustration style + lighting + mood, e.g. \"Soft pastel "
    "flat-vector illustration, warm afternoon light, friendly rounded "
    "shapes, slight grain\"). Then for every scene write a vivid "
    "`image_prompt` (one sentence, descriptive, no camera jargon), a "
    "`motion_prompt` describing how that frame should animate (subject + "
    "camera motion only), a `narration` of 1-2 sentences in plain spoken "
    "English, a short `caption` (<=60 chars), and a `duration_sec`. Pick a "
    "`music_prompt` (mood + genre) and a `title`.\n\nSEED: {seed}"
)


def generate_storyboard(prompt: str) -> tuple[StoryboardSpec, str]:
    """Stage A — one-shot OpenAI chat call via `genblaze_openai.chat()`.

    `chat()` is a standalone function (not a `BaseProvider`), so it can't
    ride `Pipeline.step()`. We persist the JSON to B2 by hand under a
    UUID-keyed prefix since there is no Pipeline Manifest for this call.
    Returns the parsed spec and the B2 object key for inspection.
    """
    logger.info("storyboard generate start", extra={
        "model": settings.chat_model,
        "prompt_chars": len(prompt),
        # Truncate at 240 chars — enough to recognise the prompt, short
        # enough that the log line stays scannable in a terminal.
        "prompt_preview": prompt[:240],
    })
    start = time.perf_counter()
    try:
        response = chat(
            settings.chat_model,
            prompt=_STORYBOARD_INSTRUCTION.format(seed=prompt),
            api_key=settings.openai_api_key,
            response_format=StoryboardSpec,
        )
    except Exception:
        logger.exception("storyboard chat failed", extra={
            "model": settings.chat_model,
            "duration_ms": int((time.perf_counter() - start) * 1000),
        })
        raise
    spec = StoryboardSpec.model_validate_json(response.text)
    key = f"{PREFIX}/{uuid.uuid4().hex}/storyboard.json"
    backend().put(
        key,
        json.dumps(spec.model_dump(), indent=2).encode("utf-8"),
        content_type="application/json",
    )
    logger.info("storyboard generate ok", extra={
        "model": settings.chat_model,
        "duration_ms": int((time.perf_counter() - start) * 1000),
        "title": spec.title,
        "style_prompt": spec.style_prompt,
        "scene_count": len(spec.scenes),
        "total_duration_sec": spec.total_duration_sec,
        # Per-scene captions are the cheapest single-line summary of what
        # the model returned. Full prompts surface at DEBUG only.
        "scene_captions": [s.caption for s in spec.scenes],
        "key": key,
    })
    if logger.isEnabledFor(logging.DEBUG):
        for i, s in enumerate(spec.scenes):
            logger.debug("scene plan", extra={
                "step_index": i, "caption": s.caption,
                "image_prompt": s.image_prompt,
                "motion_prompt": s.motion_prompt,
                "narration": s.narration,
                "duration_sec": s.duration_sec,
            })
    return spec, key


# --- Stage B1: keyframe fan-out ---------------------------------------------

def _imagen() -> ImagenProvider:
    """Shared Imagen provider — Google's image-generation surface.

    Replaces the previous OpenAI DalleProvider in Stages B0 + B1. Imagen
    is generate-only (no edit endpoint), so style consistency between B0
    and B1 still comes from the shared `style_prompt` prefix, not from
    CLIP-style image conditioning.
    """
    return ImagenProvider(api_key=settings.google_api_key)


def build_reference_pipeline(spec: StoryboardSpec) -> Pipeline:
    """Stage B0 — generate ONE master reference image from `style_prompt`.

    The image is the visual anchor for the entire run; its prompt is
    prefixed onto every Stage B1 per-scene generation so all keyframes
    share a look.
    """
    logger.info("build B0 pipeline", extra={
        "stage": "B0.reference",
        "model": settings.image_model,
        "provider": "google/imagen",
    })
    reference_prompt = f"Style reference frame for an explainer video. {spec.style_prompt}"
    logger.info("B0 step queued", extra={
        "stage": "B0.reference", "step_index": 0,
        "model": settings.image_model, "provider": "google/imagen",
        "prompt": reference_prompt,
    })
    return _attach(Pipeline(PIPELINE_NAME, max_concurrency=1)).step(
        _imagen(),
        model=settings.image_model,
        modality=Modality.IMAGE,
        prompt=reference_prompt,
    )


def build_keyframe_pipeline(spec: StoryboardSpec, reference_result=None) -> Pipeline:
    """Stage B1 — one `.step()` per scene, fanned out at max_concurrency=3.

    Prefixes every per-scene `image_prompt` with `spec.style_prompt` so the
    Imagen outputs visually rhyme with the Stage B0 reference frame. If
    `reference_result` is supplied, B1's Manifest carries it as
    `parent_run_id` (lineage only — the actual reference image is not
    passed as a provider input).
    """
    logger.info("build B1 pipeline", extra={
        "stage": "B1.keyframes",
        "model": settings.image_model,
        "provider": "google/imagen",
        "scene_count": len(spec.scenes),
        "parent_run_id": getattr(getattr(reference_result, "run", None), "run_id", None),
    })
    img = _imagen()
    p = _attach(Pipeline(PIPELINE_NAME, max_concurrency=3))
    if reference_result is not None:
        p = p.from_result(reference_result)
    style = spec.style_prompt.strip().rstrip(".")
    for i, scene in enumerate(spec.scenes):
        prompt = f"{style}. {scene.image_prompt}"
        logger.info("B1 step queued", extra={
            "stage": "B1.keyframes", "step_index": i,
            "model": settings.image_model, "provider": "google/imagen",
            "caption": scene.caption, "prompt": prompt,
        })
        p = p.step(img, model=settings.image_model, modality=Modality.IMAGE, prompt=prompt)
    return p


def _resolve_video_provider() -> tuple[str, object, str]:
    """Pick the video provider for Stage B2 based on env + key availability.

    Returns `(provider_label, provider_instance, model_id)`. The label is
    a short, log-friendly slug (`decart` / `gmicloud`).

    Selection rules:
      1. If `VIDEO_PROVIDER=gmicloud` is set OR `decart` is requested but
         `DECART_API_KEY` is empty, swap to GMICloud's image-to-video
         provider (Kling by default) when its key is configured.
      2. Otherwise default to Decart (`lucy-pro-i2v`).
      3. If neither key is configured we still construct Decart so the
         pipeline can be assembled — the actual step will surface a
         provider auth error at runtime instead of silently doing nothing.
    """
    choice = (settings.video_provider or "decart").strip().lower()
    have_decart = bool(settings.decart_api_key)
    have_gmi = bool(settings.gmi_api_key)
    fell_back = False
    if choice == "decart" and not have_decart and have_gmi:
        fell_back = True
        choice = "gmicloud"
    if choice == "gmicloud":
        if not have_gmi and have_decart:
            # Explicit gmicloud asked but no key — fall the other way.
            fell_back = True
            choice = "decart"
        else:
            logger.info(
                "video provider resolved",
                extra={"provider": "gmicloud", "model": settings.gmi_video_model,
                       "fell_back": fell_back},
            )
            return (
                "gmicloud",
                GMICloudVideoProvider(api_key=settings.gmi_api_key),
                settings.gmi_video_model,
            )
    # Decart path (default + post-swap fallback target).
    logger.info(
        "video provider resolved",
        extra={"provider": "decart", "model": settings.video_model,
               "fell_back": fell_back,
               "key_present": have_decart},
    )
    if not have_decart:
        logger.warning(
            "video provider has no key configured — Stage B2 video will error",
            extra={"provider": "decart"},
        )
    return ("decart", DecartVideoProvider(api_key=settings.decart_api_key), settings.video_model)


# --- Stage B2 helpers: duration grid + instrumental music override ---------

# Kling Image2Video V2.1 (the GMICloud video model) renders ONLY 5s or 10s
# clips — any other duration 400s ("duration value 'N' is invalid"). We snap
# each scene to the nearest supported length so the video step is accepted AND
# the composer's still/caption/audio timing (all keyed off `duration_sec`)
# matches the real clip — `duration_sec` stays the single source of truth.
_KLING_DURATIONS = (5.0, 10.0)


def snap_scene_durations(spec: StoryboardSpec) -> StoryboardSpec:
    """Snap scene durations to the active video provider's supported grid.

    No-op for any provider but GMICloud Kling (Decart, the legacy path, had no
    such constraint). Returns a copy with normalized `duration_sec` and a
    recomputed `total_duration_sec` so downstream B2 + composition agree.
    """
    if _resolve_video_provider()[0] != "gmicloud":
        return spec
    scenes = [
        s.model_copy(update={
            "duration_sec": min(_KLING_DURATIONS, key=lambda d: abs(d - s.duration_sec)),
        })
        for s in spec.scenes
    ]
    return spec.model_copy(update={
        "scenes": scenes,
        "total_duration_sec": sum(s.duration_sec for s in scenes),
    })


def _instrumental_music_registry() -> ModelRegistry:
    """Audio registry override that makes MiniMax-Music produce an INSTRUMENTAL bed.

    GMICloud's MiniMax-Music family requires a `lyrics` payload field and drops
    any param outside its allowlist (so a bare prompt 400s with
    "lyrics (Required parameter is missing)"). We want a vocal-free score, so we
    register a per-model override that (1) admits MiniMax's `lyrics` +
    `is_instrumental` controls and (2) defaults them to an instrumental track —
    `is_instrumental=True` with the documented `[Inst]` lyrics marker as the
    required-but-unused placeholder. Music stays best-effort: if GMICloud ever
    rejects these the step just fails and the composer renders a silent video.
    """
    reg = build_audio_registry()
    base = reg.get(settings.music_model)
    reg.register(replace(
        base,
        param_allowlist=(base.param_allowlist or frozenset()) | {"lyrics", "is_instrumental"},
        param_defaults={**dict(base.param_defaults), "lyrics": "[Inst]", "is_instrumental": True},
    ))
    return reg


# --- Stage B2: image-to-video + TTS per scene + music (single trailing) ----

def build_media_pipeline(spec: StoryboardSpec, keyframe_result) -> Pipeline:
    """Stage B2 — Decart video + NVIDIA TTS per scene, then one GMI music step.

    Image-to-video handoff goes through `image=<presigned-url>`, NOT
    `input_from=` — `from_result()` only records lineage in 0.3.x. Same
    pattern as `genblaze-gmicloud-pipeline.build_video_fanout`.

    Built with `preflight=False` because narration (TTS) and music are
    best-effort: a DEAD audio model must NOT abort the run at preflight
    (which validates every step before any runs). With preflight off such a
    model fails at *runtime* as a single FAILED step, and the caller runs
    this pipeline with `fail_fast=False, raise_on_failure=False` so video
    siblings still complete. The composer degrades gracefully on the missing
    audio asset (silent/partial mix) and surfaces a notice; video remains
    the essential track (the composer raises if a scene clip is missing).
    """
    video_label, vid, resolved_video_model = _resolve_video_provider()
    logger.info("build B2 pipeline", extra={
        "stage": "B2.media",
        "scene_count": len(spec.scenes),
        "video_provider": video_label,
        "video_model": resolved_video_model,
        "tts_model": settings.tts_model,
        "music_model": settings.music_model,
        "parent_run_id": getattr(keyframe_result.run, "run_id", None),
    })
    tts = NvidiaAudioProvider(api_key=settings.nvidia_api_key)
    # Custom registry so MiniMax-Music gets the `lyrics`/`is_instrumental`
    # payload fields it requires, defaulted to a vocal-free score.
    music = GMICloudAudioProvider(
        api_key=settings.gmi_api_key, models=_instrumental_music_registry(),
    )

    p = _attach(
        Pipeline(PIPELINE_NAME, max_concurrency=3, preflight=False)
    ).from_result(keyframe_result)
    for i, scene in enumerate(spec.scenes):
        image_asset = keyframe_result.run.steps[i].assets[0]
        image_ref = presign_asset_url(image_asset.url)
        logger.info("B2 scene queued", extra={
            "stage": "B2.media", "scene_index": i,
            "video_provider": video_label,
            "video_model": resolved_video_model,
            "tts_model": settings.tts_model,
            "motion_prompt": scene.motion_prompt,
            "narration": scene.narration,
            "duration_sec": scene.duration_sec,
            # Truncate the presigned URL: keep the key part, drop the
            # SigV4 noise. The full URL hits debug-only via presign log.
            "image_ref_key": backend().key_from_url(image_asset.url),
        })
        # Cross-provider keyframe handoff differs by video provider:
        #  - GMICloud Kling routes the image from step INPUTS (its family uses
        #    `route_images(slots=("image",))`), so hand it the presigned
        #    keyframe as an `external_inputs` Asset — an `image=` kwarg would be
        #    dropped by the param allowlist and Kling 400s "image required".
        #  - Decart's (now-retired) image-to-video took the URL via `image=`.
        video_kwargs: dict = {
            "model": resolved_video_model,
            "modality": Modality.VIDEO,
            "prompt": scene.motion_prompt,
            "duration": scene.duration_sec,
        }
        if video_label == "gmicloud":
            video_kwargs["external_inputs"] = [Asset(url=image_ref, media_type="image/png")]
        else:
            video_kwargs["image"] = image_ref
        p = p.step(vid, **video_kwargs)
        p = p.step(
            tts,
            model=settings.tts_model,
            modality=Modality.AUDIO,
            prompt=scene.narration,
        )
    logger.info("B2 music queued", extra={
        "stage": "B2.media", "model": settings.music_model,
        "provider": "gmicloud",
        "prompt": spec.music_prompt,
        "duration_sec": spec.total_duration_sec,
    })
    return p.step(
        music,
        model=settings.music_model,
        modality=Modality.AUDIO,
        prompt=spec.music_prompt,
        duration=spec.total_duration_sec,
    )
