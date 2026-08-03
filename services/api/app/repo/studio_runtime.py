"""Catalog-driven provider execution for curated Studio generation nodes.

Provider classes remain confined to provider_catalog.py. This module imports
Genblaze core orchestration types only and receives resolved CatalogEntry data.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from genblaze_core import Asset, Modality, Pipeline

from app.repo.pipelines import PIPELINE_NAME
from app.repo.provider_catalog import CatalogEntry
from app.studio.storage_connection import studio_presign_asset_url


async def _stream_result(
    pipeline: Pipeline, *, timeout: int, on_event: Callable[[object], None] | None
):
    result = None
    async for event in pipeline.astream(
        sink=None,
        timeout=timeout,
        raise_on_failure=True,
    ):
        if on_event:
            on_event(event)
        candidate = getattr(event, "result", None)
        if candidate is not None:
            result = candidate
    if result is None:
        raise RuntimeError("Genblaze pipeline completed without a result")
    return result


def generate_images(
    entry: CatalogEntry,
    model: str,
    prompt: str,
    variants: int,
    secret: str,
    *,
    on_event: Callable[[object], None] | None = None,
):
    provider = entry.make(secret)
    pipeline = Pipeline(f"{PIPELINE_NAME}-studio-image", max_concurrency=min(variants, 3))
    for _ in range(variants):
        pipeline = pipeline.step(provider, model=model, modality=Modality.IMAGE, prompt=prompt)
    return asyncio.run(_stream_result(pipeline, timeout=600, on_event=on_event))


def generate_videos(
    entry: CatalogEntry,
    model: str,
    prompt: str,
    image_url: str | None,
    variants: int,
    duration_sec: float,
    secret: str,
):
    provider = entry.make(secret)
    pipeline = Pipeline(
        f"{PIPELINE_NAME}-studio-video",
        max_concurrency=min(variants, 3),
        preflight=False,
    )
    image_ref = studio_presign_asset_url(image_url) if image_url else None
    for _ in range(variants):
        kwargs = {
            "model": model,
            "modality": Modality.VIDEO,
            "prompt": prompt,
            "duration": duration_sec,
        }
        if image_ref and entry.image_handoff == "external_inputs":
            kwargs["external_inputs"] = [Asset(url=image_ref, media_type="image/png")]
        elif image_ref:
            kwargs["image"] = image_ref
        pipeline = pipeline.step(provider, **kwargs)
    return pipeline.run(
        sink=None,
        timeout=1200,
        fail_fast=False,
        raise_on_failure=False,
    )


def generate_audio(
    entry: CatalogEntry,
    model: str,
    prompt: str,
    *,
    duration_sec: float | None = None,
    secret: str,
):
    params = {"duration": duration_sec} if duration_sec else {}
    pipeline = Pipeline(f"{PIPELINE_NAME}-studio-audio", preflight=False).step(
        entry.make(secret),
        model=model,
        modality=Modality.AUDIO,
        prompt=prompt,
        **params,
    )
    return pipeline.run(sink=None, timeout=600, fail_fast=False, raise_on_failure=False)


def result_assets(result) -> list[Asset]:
    assets = [asset for step in result.run.steps for asset in (step.assets or [])]
    if assets:
        return assets
    failures = [str(step.error) for step in result.run.steps if getattr(step, "error", None)]
    detail = "; ".join(failures) or f"pipeline status was {result.run.status}"
    raise RuntimeError(f"Provider returned no usable media: {detail}")
