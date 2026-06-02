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

from app.config import settings
from app.repo import pipelines
from app.repo.pipelines import (
    PIPELINE_NAME,
    build_keyframe_pipeline,
    build_media_pipeline,
    snap_scene_durations,
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


def test_snap_scene_durations_quantizes_to_kling_grid(monkeypatch) -> None:
    """GMICloud Kling i2v renders 5s/10s clips only — every scene snaps to the
    nearest supported length and the total is recomputed; the source spec is
    left untouched (a copy is returned)."""
    monkeypatch.setattr(
        pipelines, "_resolve_video_provider",
        lambda: ("gmicloud", object(), "Kling-Image2Video-V2.1-Master"),
    )
    spec = _spec()
    spec = spec.model_copy(update={"scenes": [
        s.model_copy(update={"duration_sec": d})
        for s, d in zip(spec.scenes, [6.0, 8.0, 5.0, 11.0], strict=True)
    ]})
    out = snap_scene_durations(spec)
    assert [s.duration_sec for s in out.scenes] == [5.0, 10.0, 5.0, 10.0]
    assert out.total_duration_sec == 30.0
    # Source spec is not mutated.
    assert [s.duration_sec for s in spec.scenes] == [6.0, 8.0, 5.0, 11.0]


def test_snap_scene_durations_is_noop_for_non_gmicloud(monkeypatch) -> None:
    """Decart (the legacy path) had no duration grid, so snapping is skipped."""
    monkeypatch.setattr(
        pipelines, "_resolve_video_provider",
        lambda: ("decart", object(), "lucy-2.1"),
    )
    spec = _spec()
    assert snap_scene_durations(spec) is spec


def test_instrumental_music_registry_admits_lyrics_and_defaults_instrumental() -> None:
    """MiniMax-Music requires a `lyrics` field and the default GMICloud family
    drops it; the override admits `lyrics`/`is_instrumental` and defaults them
    to a vocal-free score so the music step submits successfully."""
    spec = pipelines._instrumental_music_registry().get(settings.music_model)
    assert spec.param_allowlist is not None
    assert {"lyrics", "is_instrumental"} <= spec.param_allowlist
    assert spec.param_defaults["is_instrumental"] is True
    assert spec.param_defaults["lyrics"]  # non-empty required-field placeholder
