"""Composer tests — mock B2 IO and ffmpeg; verify scene grouping + arg shape.

Narration (TTS) and music are best-effort: Stage B2 runs with
``fail_fast=False`` so a failed audio step is present in the run but carries
an empty ``assets`` list. These tests pin that the composer degrades on
missing audio (silent / partial mix) and never crashes, while video stays
essential (a missing scene clip raises).
"""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("B2_BUCKET_NAME", "_")
os.environ.setdefault("B2_REGION", "us-west-004")
os.environ.setdefault("B2_KEY_ID", "_")
os.environ.setdefault("B2_APPLICATION_KEY", "_")

from app.repo import composer
from app.types.storyboard import Scene, StoryboardSpec

# Realistic durable B2 URLs — match the layout ObjectStorageSink writes
# (`https://s3.<region>.backblazeb2.com/<bucket>/<prefix>/<run-id>/...`),
# so the test exercises `_key_from_asset_url`'s bucket-strip logic.
_B2_HOST = "https://s3.us-west-004.backblazeb2.com/test-bucket"
_RUN_PREFIX = "explainers/run-abc123"
SCENE_URLS = [f"{_B2_HOST}/{_RUN_PREFIX}/step-{i:02d}/scene.mp4" for i in range(3)]
NARRATION_URLS = [f"{_B2_HOST}/{_RUN_PREFIX}/step-{i:02d}/narration.wav" for i in range(3)]
KEYFRAME_URLS = [f"{_B2_HOST}/{_RUN_PREFIX}/kf-{i:02d}/keyframe.png" for i in range(3)]
MUSIC_URL = f"{_B2_HOST}/{_RUN_PREFIX}/step-music/track.wav"


def _make_spec(n: int = 3) -> StoryboardSpec:
    return StoryboardSpec(
        title="t",
        style_prompt="Flat-vector illustration, warm pastel palette, soft lighting",
        music_prompt="upbeat acoustic",
        total_duration_sec=float(n * 8),
        scenes=[
            Scene(image_prompt=f"i{i}", motion_prompt="m", narration=f"n{i}",
                  caption=f"c{i}", duration_sec=8.0)
            for i in range(n)
        ],
    )


def _step(url: str | None):
    """A run step that produced one asset, or a FAILED/assetless step (url=None)."""
    return SimpleNamespace(assets=[SimpleNamespace(url=url)] if url else [])


def _make_b2_run(
    n: int = 3, *,
    video_scenes: set[int] | None = None,
    narration_scenes: set[int] | None = None,
    music: bool = True,
):
    """Stage B2 emits (video, tts) x N + (music,); fake the asset URLs.

    `video_scenes` / `narration_scenes` select which scenes have that asset
    (default all); the rest model a FAILED step (assetless). `music=False`
    models a FAILED/absent music step.
    """
    if video_scenes is None:
        video_scenes = set(range(n))
    if narration_scenes is None:
        narration_scenes = set(range(n))
    steps = []
    for i in range(n):
        steps.append(_step(SCENE_URLS[i] if i in video_scenes else None))
        steps.append(_step(NARRATION_URLS[i] if i in narration_scenes else None))
    steps.append(_step(MUSIC_URL if music else None))
    return SimpleNamespace(
        run=SimpleNamespace(steps=steps, run_id="test-run-id"),
        manifest=SimpleNamespace(manifest_uri="https://b/manifests/test.json"),
    )


def _make_b1_run(n: int = 3, *, keyframe_scenes: set[int] | None = None):
    """Stage B1 emits one keyframe step per scene; fake the asset URLs."""
    if keyframe_scenes is None:
        keyframe_scenes = set(range(n))
    steps = [_step(KEYFRAME_URLS[i] if i in keyframe_scenes else None) for i in range(n)]
    return SimpleNamespace(run=SimpleNamespace(steps=steps, run_id="kf-run-id"))


def _run_compose(b2_run, spec, b1_run=None, *, has_subtitles=True):
    """Run compose_final with B2 IO + ffmpeg mocked; return (asset, notices, ffmpeg_calls).

    Each ffmpeg stage writes its output file (args[-1]) so the next stage /
    `read_bytes()` works, and we record (stage, args) for argv assertions.
    `has_subtitles` controls whether the (patched) ffmpeg build reports the
    `subtitles` filter — i.e. whether captions burn or fall back to soft subs.
    """
    if b1_run is None:
        b1_run = _make_b1_run(len(spec.scenes))
    fake = MagicMock()
    fake.get.side_effect = lambda key: b"fake-bytes"
    fake.get_durable_url.return_value = f"{_B2_HOST}/explainers/test-run-id/final.mp4"
    fake.put.return_value = "ok"
    calls: list[tuple[str, list[str]]] = []

    def _fake_ffmpeg(args, *, stage):
        calls.append((stage, args))
        Path(args[-1]).write_bytes(b"x")

    filters = frozenset({"subtitles"}) if has_subtitles else frozenset()
    with patch.object(composer, "backend", return_value=fake), \
         patch.object(composer, "_run_ffmpeg", side_effect=_fake_ffmpeg), \
         patch.object(composer, "_available_filters", return_value=filters), \
         patch.object(composer.shutil, "which", return_value="/usr/bin/ffmpeg"), \
         patch.object(composer.Mp4Handler, "embed", return_value=None):
        asset, notices = composer.compose_final(b2_run, b1_run, spec)
    return asset, notices, calls


def _filter_for(calls, stage: str) -> str:
    """Extract the -filter_complex value from a recorded ffmpeg invocation."""
    args = next(a for s, a in calls if s == stage)
    return args[args.index("-filter_complex") + 1]


def test_group_scenes_pairs_video_and_narration(tmp_path: Path) -> None:
    spec = _make_spec(3)
    b2_run = _make_b2_run(3)
    b1_run = _make_b1_run(3)
    fake = MagicMock()
    fake.get.side_effect = lambda key: b"fake-bytes"
    with patch.object(composer, "backend", return_value=fake):
        bundles = composer._group_scenes(b2_run, b1_run, spec, tmp_path)
    assert len(bundles) == 3
    assert all(b.video_path is not None and b.still_path is None for b in bundles)
    assert all(b.narration_path is not None for b in bundles)
    # Scene i's video must come from step 2i, narration from step 2i+1.
    assert "scene_00.mp4" in bundles[0].video_path.name
    assert "scene_02_voice.wav" in bundles[2].narration_path.name
    # Every key passed to backend.get must be the bucket-stripped object key
    # (`explainers/...`), NOT the full https URL — proving `_key_from_asset_url`
    # ran and stripped `https://s3.<region>.backblazeb2.com/<bucket>/` correctly.
    keys_fetched = [call.args[0] for call in fake.get.call_args_list]
    assert keys_fetched, "expected backend.get to be called for each scene"
    assert all(k.startswith("explainers/") for k in keys_fetched), keys_fetched
    assert all(not k.startswith("http") for k in keys_fetched), keys_fetched
    # Spot-check: scene 0's video key should be the exact bucket-stripped path.
    assert "explainers/run-abc123/step-00/scene.mp4" in keys_fetched


def test_group_scenes_falls_back_to_keyframe_when_video_missing(tmp_path: Path) -> None:
    """A failed video clip falls back to the scene's keyframe still (not fatal)."""
    spec = _make_spec(3)
    b2_run = _make_b2_run(3, video_scenes={0, 1})  # scene 2's video failed
    b1_run = _make_b1_run(3)
    fake = MagicMock()
    fake.get.side_effect = lambda key: b"fake-bytes"
    with patch.object(composer, "backend", return_value=fake):
        bundles = composer._group_scenes(b2_run, b1_run, spec, tmp_path)
    assert bundles[2].video_path is None
    assert bundles[2].still_path is not None and "scene_02_still.png" in bundles[2].still_path.name
    # Other scenes still use their real clips.
    assert bundles[0].video_path is not None and bundles[0].still_path is None
    # The keyframe key was the bucket-stripped Stage B1 asset.
    keys_fetched = [call.args[0] for call in fake.get.call_args_list]
    assert "explainers/run-abc123/kf-02/keyframe.png" in keys_fetched


def test_group_scenes_raises_when_video_and_keyframe_both_missing(tmp_path: Path) -> None:
    """Only when a scene has neither a clip nor a keyframe is it fatal."""
    spec = _make_spec(3)
    b2_run = _make_b2_run(3, video_scenes={0, 1})       # scene 2 video failed
    b1_run = _make_b1_run(3, keyframe_scenes={0, 1})    # scene 2 keyframe also missing
    fake = MagicMock()
    fake.get.side_effect = lambda key: b"fake-bytes"
    with patch.object(composer, "backend", return_value=fake), \
         pytest.raises(RuntimeError, match="scene 2: no video clip and no keyframe"):
        composer._group_scenes(b2_run, b1_run, spec, tmp_path)


def test_compose_final_writes_to_b2_under_explainers_prefix() -> None:
    spec = _make_spec(3)
    asset, notices, calls = _run_compose(_make_b2_run(3), spec)

    assert notices == []  # all audio present + captions burned — no degradation
    stages = [s for s, _ in calls]
    assert stages == ["concat", "mix-audio", "finalize"]
    # libass present → captions burned via the subtitles filter.
    finalize_args = next(a for s, a in calls if s == "finalize")
    assert any("subtitles=" in a for a in finalize_args)
    # Asset.url contract: durable, credential-free — never presigned (1h TTL would silently 403).
    assert asset.url.startswith("https://s3.")
    assert "X-Amz-Signature" not in asset.url
    assert asset.media_type == "video/mp4"
    assert asset.size_bytes == len(b"x")


def test_compose_final_music_unavailable_mixes_narration_only() -> None:
    """Failed music step → narration-only mix + a music notice, no crash."""
    spec = _make_spec(3)
    asset, notices, calls = _run_compose(_make_b2_run(3, music=False), spec)

    assert any("music unavailable" in n.lower() for n in notices)
    assert not any("narration unavailable" in n.lower() for n in notices)
    mix = _filter_for(calls, "mix-audio")
    assert "[mus]" not in mix          # music absent
    assert "amix=inputs=3" in mix      # three narration tracks only
    assert asset.media_type == "video/mp4"


def test_compose_final_music_only_when_all_narration_failed() -> None:
    """All TTS failed but music survived → music plays at full level (not ducked)."""
    spec = _make_spec(3)
    _, notices, calls = _run_compose(_make_b2_run(3, narration_scenes=set()), spec)

    assert any("narration unavailable" in n.lower() for n in notices)
    mix = _filter_for(calls, "mix-audio")
    assert "[v" not in mix             # no narration tracks
    assert "volume=0dB[mus]" in mix    # full level — nothing to duck under
    assert "amix=inputs=1" in mix


def test_compose_final_partial_narration_indexes_added_inputs() -> None:
    """Some scenes lack narration → ffmpeg input indices track added inputs."""
    spec = _make_spec(3)
    # Scene 1 (0-indexed) lost narration → kept: 0, 2.
    _, notices, calls = _run_compose(_make_b2_run(3, narration_scenes={0, 2}), spec)

    assert notices == ["Narration unavailable for scene(s) 2."]
    mix = _filter_for(calls, "mix-audio")
    # Two narration inputs (indices 0,1) + music (index 2) → amix of 3.
    assert "[0:a]adelay=0" in mix          # scene 0 at offset 0
    assert "[1:a]adelay=16000" in mix      # scene 2 at offset 2x8000ms
    assert "[2:a]volume=-18dB[mus]" in mix
    assert "amix=inputs=3" in mix
    # Regression guard: `apad` (no length) pads to infinity and hangs
    # `amix=longest` until the timeout — it must NOT appear in the graph.
    assert "apad" not in mix


def test_compose_final_no_audio_renders_silent_video() -> None:
    """No narration and no music → silent video; mix-audio skipped, finalize uses -an."""
    spec = _make_spec(3)
    asset, notices, calls = _run_compose(
        _make_b2_run(3, narration_scenes=set(), music=False), spec,
    )

    stages = [s for s, _ in calls]
    assert "mix-audio" not in stages   # no audio to mix
    finalize_args = next(a for s, a in calls if s == "finalize")
    assert "-an" in finalize_args      # explicit silent track
    assert "1:a" not in finalize_args  # no audio input mapped
    assert len(notices) == 2           # narration + music both reported
    assert asset.media_type == "video/mp4"


def test_compose_final_soft_subs_when_libass_missing() -> None:
    """No `subtitles` filter → captions muxed as a soft mov_text track + notice."""
    spec = _make_spec(3)
    asset, notices, calls = _run_compose(_make_b2_run(3), spec, has_subtitles=False)

    finalize_args = next(a for s, a in calls if s == "finalize")
    assert "mov_text" in finalize_args              # soft subtitle track
    assert not any("subtitles=" in a for a in finalize_args)  # NOT burned
    assert any("burned in" in n for n in notices)   # user is told why
    assert asset.media_type == "video/mp4"


def test_compose_final_captions_failure_falls_back_to_no_captions() -> None:
    """If the caption finalize raises, the run still produces an uncaptioned MP4."""
    spec = _make_spec(3)
    b2_run = _make_b2_run(3)
    b1_run = _make_b1_run(3)
    fake = MagicMock()
    fake.get.side_effect = lambda key: b"fake-bytes"
    fake.get_durable_url.return_value = f"{_B2_HOST}/explainers/test-run-id/final.mp4"

    # First finalize attempt raises; the no-caption retry succeeds.
    calls: list[str] = []

    def _fake_ffmpeg(args, *, stage):
        calls.append(stage)
        if stage == "finalize" and calls.count("finalize") == 1:
            raise RuntimeError("ffmpeg finalize failed: boom")
        Path(args[-1]).write_bytes(b"x")

    with patch.object(composer, "backend", return_value=fake), \
         patch.object(composer, "_run_ffmpeg", side_effect=_fake_ffmpeg), \
         patch.object(composer, "_available_filters", return_value=frozenset({"subtitles"})), \
         patch.object(composer.shutil, "which", return_value="/usr/bin/ffmpeg"), \
         patch.object(composer.Mp4Handler, "embed", return_value=None):
        asset, notices = composer.compose_final(b2_run, b1_run, spec)

    assert calls.count("finalize") == 2             # retried without captions
    assert any("Captions unavailable" in n for n in notices)
    assert asset.media_type == "video/mp4"


def test_compose_final_video_falls_back_to_keyframe_still() -> None:
    """A failed scene video → keyframe still looped into the concat + a notice."""
    spec = _make_spec(3)
    asset, notices, calls = _run_compose(_make_b2_run(3, video_scenes={0, 1}), spec)

    assert any("video unavailable for scene(s) 3" in n.lower() for n in notices)
    # The concat input list loops the still image for scene 2 (3rd input).
    concat_args = next(a for s, a in calls if s == "concat")
    assert "-loop" in concat_args                 # still image looped to a clip
    assert any("scene_02_still.png" in a for a in concat_args)
    # Run still completes with a valid MP4 asset.
    assert asset.media_type == "video/mp4"


def test_compose_final_fails_loud_when_ffmpeg_missing() -> None:
    with patch.object(composer.shutil, "which", return_value=None), \
         pytest.raises(RuntimeError, match="ffmpeg binary not found"):
        composer.compose_final(_make_b2_run(3), _make_b1_run(3), _make_spec(3))
