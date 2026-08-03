"""Smoke tests — the pipeline factories build without firing any provider calls.

We construct each Pipeline from resolved catalog entries, assert its name +
step count, and confirm the provider graph wired up. No real keys required
(settings default to empty strings; preflight is not exercised at construction).

Stage A (`generate_storyboard`) is a `genblaze_openai.chat()` call — a
function, not a Pipeline — so it isn't covered here.
"""

import os

import pytest

# Ensure module-level settings load without complaints when the .env is absent.
os.environ.setdefault("B2_BUCKET_NAME", "_")
os.environ.setdefault("B2_REGION", "us-west-004")
os.environ.setdefault("B2_KEY_ID", "_")
os.environ.setdefault("B2_APPLICATION_KEY", "_")

from app.repo import pipelines
from app.repo import provider_catalog as pc
from app.repo.pipelines import (
    PIPELINE_NAME,
    build_keyframe_pipeline,
    build_media_pipeline,
    snap_scene_durations,
)
from app.types.storyboard import Scene, StoryboardSpec

# Resolved entries reused across tests (default models, empty keys at construct).
IMAGE = pc.resolve("image", "google")
VIDEO_GMI = pc.resolve("video", "gmicloud")  # has the 5/10s snap grid
VIDEO_OPENAI = pc.resolve("video", "openai")
VIDEO_REPLICATE = pc.resolve("video", "replicate")  # no grid (no-op snap)
TTS = pc.resolve("tts", "nvidia")
MUSIC = pc.resolve("music", "gmicloud")


def _spec() -> StoryboardSpec:
    return StoryboardSpec(
        title="t",
        style_prompt="Flat-vector illustration, warm pastel palette, soft lighting",
        music_prompt="m",
        total_duration_sec=24.0,
        scenes=[
            Scene(
                image_prompt=f"img {i}",
                motion_prompt="motion",
                narration="narr",
                caption="c",
                duration_sec=8.0,
            )
            for i in range(3)
        ],
    )


def _build_media(spec, keyframe_result):
    """build_media_pipeline with the canonical (gmicloud/nvidia/gmicloud) graph."""
    return build_media_pipeline(
        spec,
        keyframe_result,
        video_entry=VIDEO_GMI,
        video_model=VIDEO_GMI.default_model,
        tts_entry=TTS,
        tts_model=TTS.default_model,
        music_entry=MUSIC,
        music_model=MUSIC.default_model,
    )


def test_keyframe_pipeline_constructs_with_one_step_per_scene() -> None:
    """Stage B1 stands alone — no `from_result()` anchor (Stage A is a function)."""
    spec = _spec()
    p = build_keyframe_pipeline(spec, IMAGE, IMAGE.default_model)
    assert len(p._steps) == len(spec.scenes)
    # `Pipeline` exposes name only via the private `_name` attribute in 0.3.x.
    assert getattr(p, "_name", None) == PIPELINE_NAME


def test_media_pipeline_requires_keyframe_assets() -> None:
    """Stage B2 reads keyframe_result.run.steps[i].assets[0]; empty list raises eagerly."""

    class _Empty:
        run = type("R", (), {"steps": [], "run_id": "stub"})()
        manifest = type("M", (), {})()

    with pytest.raises((IndexError, AttributeError)):
        _build_media(_spec(), _Empty())


def test_media_pipeline_built_without_preflight(monkeypatch) -> None:
    """Video + audio are best-effort, so B2 disables model preflight: a DEAD
    model fails at runtime (contained by the caller's fail_fast=False) instead
    of aborting the whole run at validation."""
    spec = _spec()
    # Stub the cross-pipeline image handoff + backend so no B2/network IO fires.
    monkeypatch.setattr(pipelines, "presign_asset_url", lambda url, **kw: "https://signed/x")
    from types import SimpleNamespace

    monkeypatch.setattr(pipelines, "backend", lambda: SimpleNamespace(key_from_url=lambda u: "k"))
    keyframe_result = SimpleNamespace(
        run=SimpleNamespace(
            steps=[
                SimpleNamespace(assets=[SimpleNamespace(url=f"https://b/img{i}.png")])
                for i in range(len(spec.scenes))
            ],
            run_id="kf",
        )
    )
    p = _build_media(spec, keyframe_result)
    assert p._preflight is False
    # (video, tts) per scene + one trailing music step.
    assert len(p._steps) == 2 * len(spec.scenes) + 1


def test_snap_scene_durations_quantizes_to_grid() -> None:
    """A video entry with a `snap_durations` grid (GMICloud Kling: 5s/10s)
    quantizes every scene to the nearest supported length and recomputes the
    total; the source spec is left untouched (a copy is returned)."""
    assert VIDEO_GMI.snap_durations == (5.0, 10.0)
    spec = _spec()
    spec = spec.model_copy(
        update={
            "scenes": [
                s.model_copy(update={"duration_sec": d})
                for s, d in zip(spec.scenes, [6.0, 8.0, 11.0], strict=True)
            ]
        }
    )
    out = snap_scene_durations(spec, VIDEO_GMI)
    assert [s.duration_sec for s in out.scenes] == [5.0, 10.0, 10.0]
    assert out.total_duration_sec == 25.0
    assert [s.duration_sec for s in spec.scenes] == [6.0, 8.0, 11.0]


def test_sora_catalog_exposes_the_current_duration_grid() -> None:
    assert VIDEO_OPENAI.snap_durations == (4.0, 8.0, 12.0)


def test_snap_scene_durations_is_noop_without_grid() -> None:
    """A video entry with no `snap_durations` (e.g. Replicate) skips snapping."""
    assert VIDEO_REPLICATE.snap_durations is None
    spec = _spec()
    assert snap_scene_durations(spec, VIDEO_REPLICATE) is spec


def test_instrumental_music_registry_admits_lyrics_and_defaults_instrumental() -> None:
    """MiniMax-Music requires a `lyrics` field the default GMICloud family drops;
    the catalog's override admits `lyrics`/`is_instrumental` and defaults them
    to a vocal-free score so the music step submits successfully."""
    from app.config import settings

    spec = pc._instrumental_music_registry().get(settings.music_model)
    assert spec.param_allowlist is not None
    assert {"lyrics", "is_instrumental"} <= spec.param_allowlist
    assert spec.param_defaults["is_instrumental"] is True
    assert spec.param_defaults["lyrics"]  # non-empty required-field placeholder
