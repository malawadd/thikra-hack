"""Smoke tests — the pipeline factories build without firing any provider calls.

We construct each Pipeline, assert its name + step count, and confirm the
provider class graph wired up. No real keys required (Pydantic settings
default to empty strings; preflight is not exercised at construction).

Stage A (`generate_storyboard`) is a `genblaze_openai.chat()` call — a
function, not a Pipeline — so it isn't covered here. A real end-to-end
test would need a live OpenAI key.
"""

import os
from types import SimpleNamespace

import pytest

# Ensure module-level settings load without complaints when the .env is absent.
os.environ.setdefault("B2_BUCKET_NAME", "_")
os.environ.setdefault("B2_REGION", "us-west-004")
os.environ.setdefault("B2_KEY_ID", "_")
os.environ.setdefault("B2_APPLICATION_KEY", "_")

from app.repo import pipelines
from app.repo.pipelines import (
    PIPELINE_NAME,
    build_keyframe_pipeline,
    build_media_pipeline,
)
from app.types.storyboard import Scene, StoryboardSpec


def _spec() -> StoryboardSpec:
    return StoryboardSpec(
        title="t",
        style_prompt="Flat-vector illustration, warm pastel palette, soft lighting",
        music_prompt="m",
        total_duration_sec=32.0,
        scenes=[
            Scene(image_prompt=f"img {i}", motion_prompt="motion",
                  narration="narr", caption="c", duration_sec=8.0)
            for i in range(4)
        ],
    )


def test_keyframe_pipeline_constructs_with_one_step_per_scene() -> None:
    """Stage B1 stands alone — no `from_result()` anchor (Stage A is a function)."""
    spec = _spec()
    p = build_keyframe_pipeline(spec)
    # One image step per scene; cross-pipeline lineage shows up in B2 via
    # the B2 pipeline's `from_result(B1)` call, not B1 itself.
    assert len(p._steps) == len(spec.scenes)
    # `Pipeline` exposes name only via the private `_name` attribute in 0.3.2.
    assert getattr(p, "_name", None) == PIPELINE_NAME


def test_media_pipeline_requires_keyframe_assets() -> None:
    """Stage B2 reads keyframe_result.run.steps[i].assets[0]; empty list raises eagerly."""
    class _Empty:
        run = type("R", (), {"steps": [], "run_id": "stub"})()
        manifest = type("M", (), {})()
    with pytest.raises((IndexError, AttributeError)):
        build_media_pipeline(_spec(), _Empty())


def test_media_pipeline_built_without_preflight(monkeypatch) -> None:
    """Audio (TTS + music) is best-effort, so the B2 pipeline disables model
    preflight: a DEAD audio model must fail at runtime (contained by the
    caller's fail_fast=False) instead of aborting the whole run at validation.
    """
    spec = _spec()
    # Stub the cross-pipeline image handoff + backend so no B2/network IO fires.
    monkeypatch.setattr(pipelines, "presign_asset_url", lambda url, **kw: "https://signed/x")
    monkeypatch.setattr(pipelines, "backend", lambda: SimpleNamespace(key_from_url=lambda u: "k"))
    keyframe_result = SimpleNamespace(run=SimpleNamespace(
        steps=[
            SimpleNamespace(assets=[SimpleNamespace(url=f"https://b/img{i}.png")])
            for i in range(len(spec.scenes))
        ],
        run_id="kf",
    ))
    p = build_media_pipeline(spec, keyframe_result)
    assert p._preflight is False
    # (video, tts) per scene + one trailing music step.
    assert len(p._steps) == 2 * len(spec.scenes) + 1
