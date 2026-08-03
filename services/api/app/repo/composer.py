"""Final-MP4 composition via system ffmpeg.

This is the ONLY non-Genblaze adapter in the sample. It exists because
`genblaze-core` 0.3.x ships no composition primitive — no
`genblaze-ffmpeg`, no `genblaze-compose`, no `genblaze-video` package on
PyPI. The composer remains in `repo/` because it is storage-adjacent
(downloads from + uploads to B2 via the same `S3StorageBackend` used by
the pipelines). It imports `genblaze_core` *types* only (Asset, Manifest)
— no Pipeline or Provider use here.

ffmpeg is invoked via `subprocess.run([...], timeout=300, check=True)` —
no `ffmpeg-python` dependency layer. Call from `main.py` through
`asyncio.to_thread(...)` so the FastAPI event loop never blocks.
"""

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from genblaze_core.media import Mp4Handler
from genblaze_core.models.asset import Asset
from PIL import Image, ImageDraw, ImageFont

from app.repo.pipelines import PREFIX, backend
from app.types.storyboard import StoryboardSpec

logger = logging.getLogger("api.composer")
_FFMPEG_TIMEOUT_SEC = 300

def probe_media(path: Path) -> dict:
    """Return ffprobe JSON; all ffmpeg-family subprocesses stay in this module."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe binary not found on PATH")
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout=30, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"ffprobe could not read {path.name}: {result.stderr.strip()}")
    return json.loads(result.stdout)
# --- Scene grouping ---------------------------------------------------------

@dataclass
class _SceneBundle:
    """One scene's downloaded artifacts on the local filesystem.

    Every track is best-effort:
    - Exactly one of `video_path` / `still_path` is set. `video_path` is the
      Decart clip; when that step failed we fall back to `still_path` — the
      scene's Stage B1 keyframe image, looped to `duration` at concat time.
    - `narration_path` is optional: a scene whose TTS step failed has no
      voiceover and is mixed as silence under whatever music survived.
    """

    index: int
    video_path: Path | None
    still_path: Path | None
    narration_path: Path | None
    caption: str
    duration: float


def _asset_url_or_none(step) -> str | None:
    """First asset URL of a step, or None when the step produced no assets.

    With `fail_fast=False` a FAILED step is present in the run but carries an
    empty `assets` list — never index `[0]` blindly (it would IndexError and
    re-break the run we are trying to keep alive)."""
    assets = getattr(step, "assets", None) or []
    return assets[0].url if assets else None


def _scene_track_urls(b2_run, scene_count: int) -> tuple[list[str | None], list[str | None], str | None]:
    """Extract B2 tracks by media type rather than a fixed step position.

    Video and narration are concurrent, so a partial result can omit a step.
    Selecting by position then risks dropping a completed video or treating
    narration as music.  The first audio track per scene is narration; a
    remaining trailing audio track is optional music.
    """
    videos: list[str] = []
    speech: list[str] = []
    other_audio: list[str] = []
    for step in b2_run.run.steps:
        for asset in getattr(step, "assets", None) or []:
            media_type = (getattr(asset, "media_type", "") or "").lower()
            if media_type.startswith("video/"):
                videos.append(asset.url)
            elif media_type.startswith("audio/"):
                metadata = getattr(asset, "metadata", None) or {}
                if metadata.get("audio_type") == "speech":
                    speech.append(asset.url)
                else:
                    other_audio.append(asset.url)
    # Modern TTS connectors mark speech explicitly.  Keep a positional
    # fallback for older providers whose audio assets predate that metadata.
    if not speech:
        narrations = [
            _asset_url_or_none(b2_run.run.steps[index * 2 + 1])
            if index * 2 + 1 < len(b2_run.run.steps)
            else None
            for index in range(scene_count)
        ]
        music = _asset_url_or_none(b2_run.run.steps[-1]) if len(b2_run.run.steps) > scene_count * 2 else None
    else:
        narrations = [speech[i] if i < len(speech) else None for i in range(scene_count)]
        music = other_audio[0] if other_audio else None
    return (
        [videos[i] if i < len(videos) else None for i in range(scene_count)],
        narrations,
        music,
    )


def _key_from_asset_url(url: str) -> str:
    """Strip the `<bucket>/` prefix from a durable B2 URL to get the object key.

    Asset.url is the durable URL ObjectStorageSink wrote; the key is the
    URL path with the bucket segment removed.
    """
    path = urlparse(url).path.lstrip("/")
    _, _, key = path.partition("/")
    return key or path


def _download(key_or_url: str, dest: Path) -> Path:
    """Download a B2 object to `dest` (creates parent dirs)."""
    key = _key_from_asset_url(key_or_url) if key_or_url.startswith("http") else key_or_url
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = backend().get(key)
    dest.write_bytes(blob)
    logger.info("downloaded asset", extra={
        "key": key, "size_bytes": len(blob), "dest": str(dest.name),
    })
    return dest


def _group_scenes(b2_run, b1_run, spec: StoryboardSpec, tmp: Path) -> list[_SceneBundle]:
    """Pair each scene with its (video|keyframe-still, narration) assets.

    Stage B2 emits steps in `(video, tts) * N + (music,)` order, so scene
    `i` lives at steps `[2i, 2i+1]` and the music step is the last one.
    Every track is best-effort:
    - Video: a failed Decart clip falls back to scene `i`'s Stage B1 keyframe
      (`b1_run.run.steps[i]`), rendered as a static clip at concat time. Only
      when BOTH the clip and the keyframe are missing does the scene raise —
      there is then no visual at all to show.
    - Narration: a failed/absent TTS step yields `narration_path=None`.
    """
    kf_steps = b1_run.run.steps
    bundles: list[_SceneBundle] = []
    video_urls, narration_urls, _ = _scene_track_urls(b2_run, len(spec.scenes))
    for i, scene in enumerate(spec.scenes):
        video_url = video_urls[i]
        video_path = still_path = None
        if video_url is not None:
            video_path = _download(video_url, tmp / f"scene_{i:02d}.mp4")
        else:
            kf_url = _asset_url_or_none(kf_steps[i]) if i < len(kf_steps) else None
            if kf_url is None:
                raise RuntimeError(
                    f"scene {i}: no video clip and no keyframe still — "
                    "nothing to render for this scene"
                )
            still_path = _download(kf_url, tmp / f"scene_{i:02d}_still.png")
            logger.info("scene video fell back to keyframe still", extra={
                "scene_index": i,
            })
        narration_url = narration_urls[i]
        bundles.append(_SceneBundle(
            index=i,
            video_path=video_path,
            still_path=still_path,
            narration_path=(
                _download(narration_url, tmp / f"scene_{i:02d}_voice.wav")
                if narration_url else None
            ),
            caption=scene.caption,
            duration=scene.duration_sec,
        ))
    return bundles


def _music_url(b2_run, scene_count: int) -> str | None:
    """URL of the trailing music step's asset, or None when music is absent.

    Music is the LAST step of the Stage B2 run. With `fail_fast=False` a
    failed music step is still present (order preserved) but assetless, so
    treat 'present-but-assetless' as 'no music'."""
    _, _, music = _scene_track_urls(b2_run, scene_count)
    return music


def _download_music(b2_run, scene_count: int, tmp: Path) -> Path | None:
    """Download the run's music track, or None when music is unavailable."""
    url = _music_url(b2_run, scene_count)
    return _download(url, tmp / "music.wav") if url else None


def degradation_notices(scenes: list[_SceneBundle], music_present: bool) -> list[str]:
    """Human-readable messages for every best-effort track that fell back.

    Derived from the already-grouped `_SceneBundle`s (video→still and narration
    presence) plus the music flag — so the Stage B2 step layout is encoded in
    exactly ONE place (`_group_scenes`), not re-walked here.
    """
    notices: list[str] = []
    still = [s.index for s in scenes if s.video_path is None]
    if still:
        nums = ", ".join(str(i + 1) for i in still)
        notices.append(f"Video unavailable for scene(s) {nums} — used the keyframe still instead.")
    missing = [s.index for s in scenes if s.narration_path is None]
    if missing:
        if len(missing) == len(scenes):
            notices.append("Narration unavailable — final video has no voiceover.")
        else:
            nums = ", ".join(str(i + 1) for i in missing)
            notices.append(f"Narration unavailable for scene(s) {nums}.")
    if not music_present:
        notices.append("Background music unavailable — final video has no score.")
    return notices


# --- ffmpeg invocations -----------------------------------------------------

def _run_ffmpeg(args: list[str], *, stage: str) -> None:
    """Single-entry ffmpeg invoker — logs the stage, fail-stops on nonzero."""
    argv0 = " ".join(args[:8]) + (" ..." if len(args) > 8 else "")
    logger.info("ffmpeg start", extra={"stage": stage, "argv0": argv0})
    start = time.perf_counter()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
            check=True, timeout=_FFMPEG_TIMEOUT_SEC, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
        logger.error("ffmpeg failed", extra={
            "stage": stage, "argv0": argv0,
            "duration_ms": int((time.perf_counter() - start) * 1000),
            "stderr": stderr.strip()[-2000:],  # tail to keep log line bounded
        })
        raise RuntimeError(f"ffmpeg {stage} failed: {stderr.strip()}") from exc
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timeout", extra={"stage": stage, "argv0": argv0})
        raise
    logger.info("ffmpeg ok", extra={
        "stage": stage,
        "duration_ms": int((time.perf_counter() - start) * 1000),
    })


# Default output canvas for the standalone sample endpoint. Commerce passes
# the confirmed mandate's required resolution so portrait orders stay portrait.
_DEFAULT_CANVAS = (1280, 720)
_FPS = 30

def _normalize_filter(canvas: tuple[int, int]) -> str:
    width, height = canvas
    return f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={_FPS}"


def _concat_video(scenes: list[_SceneBundle], tmp: Path, canvas: tuple[int, int] = _DEFAULT_CANVAS) -> Path:
    """Concat per-scene visuals into one silent mp4, normalizing every input.

    Uses the concat *filter* (not the demuxer) so real Decart clips and
    keyframe-still fallbacks — which can differ in resolution/SAR — are each
    scaled+padded to a common canvas before concatenation. A still scene is
    fed as `-loop 1 -t <duration>` so the image becomes a clip of the scene's
    length.
    """
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for idx, s in enumerate(scenes):
        if s.video_path is not None:
            inputs += ["-i", str(s.video_path)]
        else:
            inputs += ["-loop", "1", "-t", str(s.duration), "-i", str(s.still_path)]
        filters.append(f"[{idx}:v]{_normalize_filter(canvas)}[v{idx}]")
        labels.append(f"[v{idx}]")
    filters.append(f"{''.join(labels)}concat=n={len(scenes)}:v=1:a=0[outv]")
    out = tmp / "video.mp4"
    # `-preset ultrafast`: this is a throwaway intermediate — when captions are
    # burned it gets re-encoded again in `_finalize`, so spend no CPU on its
    # compression here.
    _run_ffmpeg(
        [*inputs, "-filter_complex", ";".join(filters),
         "-map", "[outv]", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-an", str(out)],
        stage="concat",
    )
    return out


def _mix_audio(
    scenes: list[_SceneBundle], music_path: Path | None, tmp: Path, total_duration: float
) -> Path | None:
    """Lay available narration WAVs at scene start times + add the music track.

    Builds an `adelay`+`amix` filter graph over only the tracks that exist
    (narration is best-effort per scene; music may be absent). Music ducks to
    -18 dB *when narration is present* so the voiceover sits on top; with no
    narration it plays at full level. Returns None when there is no audio at
    all (caller renders a silent video).

    ffmpeg input indices are assigned per *added* input — never by scene
    index — so a gap (a scene with no narration) doesn't desync the graph.

    NB: `adelay` alone (no `apad`). `apad` with no length argument pads to
    *infinity*, and with `amix=duration=longest` that makes the mix run
    forever (ffmpeg only stops at the timeout). `amix=longest` already extends
    the output to the longest real input, so each delayed narration stays
    finite and the mix terminates.
    """
    inputs: list[str] = []
    filters: list[str] = []
    mix_labels: list[str] = []
    ff_idx = 0
    offset_ms = 0
    for s in scenes:
        if s.narration_path is not None:
            inputs += ["-i", str(s.narration_path)]
            filters.append(f"[{ff_idx}:a]adelay={offset_ms}|{offset_ms}[v{ff_idx}]")
            mix_labels.append(f"[v{ff_idx}]")
            ff_idx += 1
        offset_ms += int(s.duration * 1000)

    if music_path is not None:
        inputs += ["-i", str(music_path)]
        # Duck under narration only when there's narration to duck under.
        gain = "-18dB" if mix_labels else "0dB"
        filters.append(f"[{ff_idx}:a]volume={gain}[mus]")
        mix_labels.append("[mus]")
        ff_idx += 1

    if not mix_labels:
        return None  # no narration and no music — silent final video

    filters += [
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}"
        ":duration=longest:dropout_transition=0[mixed]",
        f"[mixed]atrim=duration={total_duration}[aout]",
    ]
    out = tmp / "audio.m4a"
    _run_ffmpeg(
        [*inputs, "-filter_complex", ";".join(filters),
         "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", str(out)],
        stage="mix-audio",
    )
    return out


@lru_cache(maxsize=1)
def _available_filters() -> frozenset[str]:
    """Filter names this ffmpeg build registers (parsed from `-filters`).

    Caption burning needs the `subtitles` filter, which only exists when ffmpeg
    was built `--enable-libass`. Many builds (e.g. some Homebrew bottles) omit
    it, so we probe once rather than assume. `ffmpeg -h filter=<name>` is NOT a
    usable probe — it exits 0 even for unknown filters."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
    except Exception as exc:
        logger.warning("ffmpeg -filters probe failed", extra={"exception": str(exc)})
        return frozenset()
    # Each line: " <flags(3)> <name> <in->out> <desc>"; flags are T/S/C or '.'.
    return frozenset(
        m.group(1)
        for line in out.splitlines()
        if (m := re.match(r"\s*[TSC.]{3}\s+(\S+)\s", line))
    )


def _write_srt(scenes: list[_SceneBundle], tmp: Path) -> Path:
    """Write one SRT cue per scene, timed by cumulative scene duration."""
    srt = tmp / "captions.srt"
    lines: list[str] = []
    t = 0.0
    for i, s in enumerate(scenes, start=1):
        # SRT separates blocks by blank lines; flatten any newlines in copy.
        safe = s.caption.replace("\n", " ").strip()
        lines += [str(i), f"{_srt_ts(t)} --> {_srt_ts(t + s.duration)}", safe, ""]
        t += s.duration
    srt.write_text("\n".join(lines))
    return srt


def _finalize(
    video: Path, audio: Path | None, srt: Path | None, tmp: Path, *, burn: bool,
) -> Path:
    """Produce final.mp4 from `video` (+ optional `audio`), handling captions:

    - `srt` + `burn=True`  → burn the SRT into the picture (`subtitles` filter,
      needs libass). Re-encodes video.
    - `srt` + `burn=False` → mux the SRT as a soft `mov_text` subtitle track
      (works on any build; video stream-copied).
    - `srt=None`           → no captions at all (video stream-copied).

    `audio=None` yields a silent video (`-an`). Captions are best-effort, so the
    caller falls back across these modes rather than failing the run.
    """
    out = tmp / "final.mp4"
    # All `-i` inputs must precede the output options; assign indices up front.
    inputs = ["-i", str(video)]
    idx = 1
    audio_idx = None
    if audio is not None:
        inputs += ["-i", str(audio)]
        audio_idx = idx
        idx += 1
    soft_srt_idx = None
    if srt is not None and not burn:
        inputs += ["-i", str(srt)]
        soft_srt_idx = idx
        idx += 1

    args = [*inputs]
    if srt is not None and burn:
        # The `subtitles` filter reads the SRT directly (NOT a `-i` input).
        # The path is from `tempfile` (no quotes/brackets/backslashes), so
        # escaping the `:` is the only filtergraph-special char we can hit.
        srt_arg = str(srt).replace(":", r"\:")
        # This is the deliverable encode (the burn path re-encodes video, so the
        # concat intermediate stays `ultrafast`); `veryfast` keeps it well under
        # the ffmpeg timeout with negligible quality loss at these durations.
        args += ["-filter_complex", f"[0:v]subtitles='{srt_arg}'[vout]", "-map", "[vout]",
                 "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    else:
        args += ["-map", "0:v", "-c:v", "copy"]
    if audio_idx is not None:
        args += ["-map", f"{audio_idx}:a", "-c:a", "copy"]
    else:
        args += ["-an"]
    if soft_srt_idx is not None:
        args += ["-map", f"{soft_srt_idx}:s", "-c:s", "mov_text"]
    args += ["-movflags", "+faststart", str(out)]
    _run_ffmpeg(args, stage="finalize")
    return out


_SOFT_SUB_NOTICE = (
    "Captions couldn't be burned in (this ffmpeg build lacks libass) — embedded "
    "as a selectable subtitle track instead. Install an ffmpeg with libass for "
    "burned-in captions."
)
_NO_SUB_NOTICE = "Captions unavailable — the final video has no captions."


def _finalize_with_captions(
    video: Path, audio: Path | None, scenes: list[_SceneBundle], tmp: Path,
    notices: list[str],
) -> Path:
    """Finalize the MP4, degrading captions gracefully (best-effort).

    Burn into the picture when the `subtitles` filter (libass) exists; otherwise
    mux a soft `mov_text` track; if either fails, finalize with no captions.
    Appends a degradation `notice` for the soft / no-caption paths. Captions
    never fail the run — video + audio are the essential product.
    """
    srt = _write_srt(scenes, tmp)
    burn = "subtitles" in _available_filters()
    try:
        final_path = _finalize(video, audio, srt, tmp, burn=burn)
        if not burn:
            notices.append(_SOFT_SUB_NOTICE)
        return final_path
    except RuntimeError as exc:
        logger.warning("caption finalize failed; producing an uncaptioned MP4",
                       extra={"burn": burn, "exception": str(exc)})
        notices.append(_NO_SUB_NOTICE)
        return _finalize(video, audio, None, tmp, burn=False)


def _srt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Public entry point -----------------------------------------------------

def compose_final(b2_run, b1_run, spec: StoryboardSpec, canvas: tuple[int, int] = _DEFAULT_CANVAS) -> tuple[Asset, list[str]]:
    """Concat scenes, mix available narration + music, burn captions, upload to B2.

    `b1_run` is the Stage B1 keyframe result — its assets back the video
    fallback (a failed Decart clip is replaced by the scene's keyframe still).
    Returns the synthesized `genblaze_core.Asset` plus degradation notices
    (empty when nothing fell back) so the SSE stream can tell the user which
    best-effort track — video→still, narration, music — degraded. A scene is
    only fatal when BOTH its video clip and keyframe are missing.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg binary not found on PATH — see infra/README.md")

    run_id = b2_run.run.run_id
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="genblaze-compose-") as tmp_str:
        tmp = Path(tmp_str)
        scenes = _group_scenes(b2_run, b1_run, spec, tmp)
        music_path = _download_music(b2_run, len(spec.scenes), tmp)
        # Notices derive from the grouped bundles + music presence (single
        # source of layout truth); `_finalize_with_captions` may append a
        # caption-degradation notice below.
        notices = degradation_notices(scenes, music_path is not None)
        logger.info("compose start", extra={
            "run_id": run_id, "scene_count": len(spec.scenes),
            "total_duration_sec": spec.total_duration_sec,
            "degradation_notices": notices,
        })

        video_only = _concat_video(scenes, tmp, canvas)
        audio_mix = _mix_audio(scenes, music_path, tmp, spec.total_duration_sec)
        final_path = _finalize_with_captions(video_only, audio_mix, scenes, tmp, notices)

        # Embed the Stage B2 Manifest into the MP4 if the helper is available.
        # Mp4Handler IS importable from `genblaze_core.media` in 0.3.2 (the
        # plan flagged this as uncertain — confirmed at build time).
        try:
            Mp4Handler().embed(final_path, b2_run.manifest)
        except Exception as exc:  # nosec - best-effort metadata embed
            logger.warning("manifest embed failed (continuing without)", extra={
                "run_id": run_id, "exception": str(exc),
            })

        # Whole-file read into RAM (then hashed + uploaded) is the deliberate
        # simple choice for a sample — a 30-60s MP4 is tens of MB. Stream from
        # disk via a file handle if this ever serves large outputs concurrently.
        final_bytes = final_path.read_bytes()
        key = f"{PREFIX}/{run_id}/final.mp4"
        backend().put(key, final_bytes, content_type="video/mp4")
        logger.info("compose ok", extra={
            "run_id": run_id, "key": key, "size_bytes": len(final_bytes),
            "duration_ms": int((time.perf_counter() - start) * 1000),
        })
        # `Asset.url` is contractually a durable URL (see Asset.url docstring
        # in genblaze_core.models.asset). The frontend hits `GET /assets/{key}`
        # to redirect to a fresh presigned URL — never write a presigned URL
        # into `Asset.url` (the 1h TTL would silently 403 for saved links).
        return Asset(
            url=backend().get_durable_url(key),
            media_type="video/mp4",
            sha256=_sha256(final_bytes),
            size_bytes=len(final_bytes),
        ), notices


def compose_studio(
    visual_url: str,
    audio_urls: list[str],
    *,
    project_id: str,
    execution_id: str,
    duration_sec: float = 5.0,
) -> Asset:
    """Compose one graph-selected visual with optional audio tracks.

    This intentionally small Studio primitive keeps every ffmpeg invocation in
    the established composer boundary while the richer timeline remains out of
    v1 scope.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg binary not found on PATH — see infra/README.md")
    with tempfile.TemporaryDirectory(prefix="thikra-studio-compose-") as tmp_str:
        tmp = Path(tmp_str)
        visual_suffix = ".mp4" if visual_url.lower().split("?")[0].endswith(".mp4") else ".png"
        visual = _download(visual_url, tmp / f"visual{visual_suffix}")
        audio = [_download(url, tmp / f"audio-{index}.wav") for index, url in enumerate(audio_urls)]
        output = tmp / "studio-final.mp4"
        inputs: list[str] = []
        if visual_suffix == ".mp4":
            inputs.extend(["-i", str(visual)])
        else:
            inputs.extend(["-loop", "1", "-t", str(duration_sec), "-i", str(visual)])
        for track in audio:
            inputs.extend(["-i", str(track)])
        args = [*inputs, "-map", "0:v:0", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio:
            labels = "".join(f"[{index + 1}:a]" for index in range(len(audio)))
            args.extend(
                [
                    "-filter_complex",
                    f"{labels}amix=inputs={len(audio)}:duration=longest:normalize=0[a]",
                    "-map",
                    "[a]",
                    "-c:a",
                    "aac",
                    "-shortest",
                ]
            )
        else:
            args.append("-an")
        args.append(str(output))
        _run_ffmpeg(args, stage="studio-compose")
        payload = output.read_bytes()
        key = f"studio/{project_id}/{execution_id}/final.mp4"
        backend().put(key, payload, content_type="video/mp4")
        return Asset(
            url=backend().get_durable_url(key),
            media_type="video/mp4",
            sha256=_sha256(payload),
            size_bytes=len(payload),
        )


# --- Studio editor media primitives ---------------------------------------

def _localize(source: str, destination: Path) -> Path:
    if source.startswith(("http://", "https://")):
        return _download(source, destination)
    path = Path(source).resolve()
    if not path.is_file():
        raise RuntimeError("Studio source media is missing")
    return path


def _rate(value: str | None) -> str | None:
    if not value or value == "0/0":
        return None
    return value


def prepare_studio_asset(source: str, content_type: str, cache: Path) -> dict:
    """Analyze a source and create versioned preview derivatives by source hash."""
    cache.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse(source).path).suffix or ".bin"
    original = _localize(source, cache / f"source{suffix}")
    if content_type.startswith("image/"):
        with Image.open(original) as image:
            width, height = image.size
            image.thumbnail((480, 270))
            thumb = cache / "thumbnail-v1.jpg"
            image.convert("RGB").save(thumb, "JPEG", quality=82)
        return {
            "width": width, "height": height, "duration_ms": None,
            "frame_rate": None, "has_audio": False,
            "thumbnail_path": str(thumb), "proxy_path": None,
            "waveform": [], "metadata": {"proxy_version": 1},
        }
    info = probe_media(original)
    streams = info.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration") or (video or audio or {}).get("duration") or 0)
    thumb: Path | None = None
    proxy: Path | None = None
    if video:
        thumb = cache / "thumbnail-v1.jpg"
        proxy = cache / "proxy-v1.mp4"
        if not thumb.exists():
            _run_ffmpeg(["-ss", "0", "-i", str(original), "-frames:v", "1", "-vf", "scale=480:-2", str(thumb)], stage="studio-thumbnail")
        if not proxy.exists():
            _run_ffmpeg([
                "-i", str(original), "-vf", "scale='min(1280,iw)':-2", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "27", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(proxy),
            ], stage="studio-proxy")
    elif audio:
        thumb = cache / "waveform-v1.png"
        if not thumb.exists():
            _run_ffmpeg([
                "-i", str(original), "-filter_complex", "showwavespic=s=1200x180:colors=6fe7c8",
                "-frames:v", "1", str(thumb),
            ], stage="studio-waveform")
    return {
        "width": int(video.get("width", 0)) if video else None,
        "height": int(video.get("height", 0)) if video else None,
        "duration_ms": max(1, round(duration * 1000)) if duration else None,
        "frame_rate": _rate(video.get("avg_frame_rate")) if video else None,
        "has_audio": bool(audio), "thumbnail_path": str(thumb) if thumb else None,
        "proxy_path": str(proxy) if proxy else None, "waveform": [],
        "metadata": {"proxy_version": 1, "format": info.get("format", {}).get("format_name")},
    }


def create_demo_editor_clip(destination: Path, duration_sec: float) -> Path:
    """Create a clearly synthetic local clip for the deterministic DEMO editor."""
    _run_ffmpeg(
        [
            "-f", "lavfi", "-i",
            f"color=c=0x30265a:s=1280x720:r=30:d={duration_sec}",
            "-vf", f"fade=t=in:st=0:d=.35,fade=t=out:st={max(.35, duration_sec - .35)}:d=.35",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(destination),
        ],
        stage="studio-demo-video",
    )
    return destination


def extract_studio_sequence_audio(
    document: dict, assets: dict[str, dict], destination: Path
) -> Path:
    """Create the current audible sequence mix for caption transcription."""
    tracks = {track["id"]: track for track in document["tracks"]}
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    input_index = 0
    for clip_index, clip in enumerate(document["clips"]):
        if clip["kind"] not in {"audio", "video"} or tracks[clip["track_id"]].get("muted"):
            continue
        descriptor = assets.get(clip.get("asset_id"))
        if not descriptor or (clip["kind"] == "video" and not descriptor.get("has_audio")):
            continue
        path = _localize(
            descriptor.get("path") or descriptor.get("url"),
            destination.parent / f"caption-source-{clip_index}.bin",
        )
        length = clip["duration_ms"] / 1000
        inputs += ["-ss", str(clip.get("source_in_ms", 0) / 1000), "-t", str(length), "-i", str(path)]
        audio = clip.get("audio") or {}
        gain = 0 if audio.get("muted") else float(audio.get("gain_db", 0))
        label = f"captiona{clip_index}"
        filters.append(
            f"[{input_index}:a]atrim=duration={length},asetpts=PTS-STARTPTS,"
            f"volume={10 ** (gain / 20):.5f},adelay={clip['start_ms']}|{clip['start_ms']}[{label}]"
        )
        labels.append(label)
        input_index += 1
    if not labels:
        raise RuntimeError("The sequence has no audible media to transcribe")
    filters.append(
        "".join(f"[{label}]" for label in labels)
        + f"amix=inputs={len(labels)}:duration=longest:normalize=0[aout]"
    )
    _run_ffmpeg(
        [
            *inputs, "-filter_complex", ";".join(filters), "-map", "[aout]",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination),
        ],
        stage="studio-caption-mix",
    )
    return destination


def _font_path(family: str) -> str | None:
    windows = Path("C:/Windows/Fonts")
    choices = {
        "Noto Sans Arabic": ["NotoSansArabic-Regular.ttf", "segoeui.ttf"],
        "Noto Serif": ["NotoSerif-Regular.ttf", "georgia.ttf"],
        "Noto Sans": ["NotoSans-Regular.ttf", "arial.ttf"],
    }
    for name in choices.get(family, choices["Noto Sans"]):
        candidate = windows / name
        if candidate.is_file():
            return str(candidate)
    return None


def _text_card(clip: dict, canvas: tuple[int, int], destination: Path) -> Path:
    settings = clip.get("text") or {}
    width, height = canvas
    image = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    size = max(10, int(settings.get("font_size", 56) * height / 1080))
    try:
        font = ImageFont.truetype(_font_path(settings.get("font_family", "Noto Sans")), size)
    except (OSError, TypeError):
        font = ImageFont.load_default(size=size)
    text = str(settings.get("content", ""))
    box = draw.multiline_textbbox((0, 0), text, font=font, align=settings.get("align", "center"), spacing=8)
    text_width, text_height = box[2] - box[0], box[3] - box[1]
    x = int(float(settings.get("position_x", .5)) * width - text_width / 2)
    y = int(float(settings.get("position_y", .82)) * height - text_height / 2)
    background = settings.get("background", "#00000000")
    if background[-2:] != "00":
        draw.rounded_rectangle((x - 18, y - 10, x + text_width + 18, y + text_height + 10), 12, fill=background)
    draw.multiline_text((x, y), text, font=font, fill=settings.get("color", "#ffffff"), align=settings.get("align", "center"), spacing=8)
    image.save(destination)
    return destination


def _srt_from_clips(clips: list[dict]) -> bytes | None:
    captions = sorted((clip for clip in clips if clip["kind"] == "caption"), key=lambda item: item["start_ms"])
    if not captions:
        return None
    lines: list[str] = []
    for index, clip in enumerate(captions, 1):
        start = clip["start_ms"] / 1000
        end = (clip["start_ms"] + clip["duration_ms"]) / 1000
        lines.extend([str(index), f"{_srt_ts(start)} --> {_srt_ts(end)}", clip["text"]["content"], ""])
    return "\n".join(lines).encode("utf-8")


def _run_editor_ffmpeg(args: list[str], duration_ms: int, on_progress, cancelled) -> None:
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args[:-1],
        "-progress", "pipe:1", "-nostats", args[-1],
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output: list[str] = []
    assert process.stdout is not None
    while True:
        if cancelled():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise InterruptedError("Studio export cancelled")
        line = process.stdout.readline()
        if line:
            output.append(line.strip())
            if line.startswith("out_time_ms="):
                encoded_ms = int(line.partition("=")[2] or 0) // 1000
                percent = min(94, 15 + round(encoded_ms / max(1, duration_ms) * 78))
                on_progress("encoding", percent, f"Encoding {max(0, percent - 15)}%")
        if process.poll() is not None:
            break
    if process.returncode:
        raise RuntimeError("ffmpeg sequence render failed: " + "\n".join(output[-20:])[-2000:])


def render_studio_sequence(
    document: dict,
    assets: dict[str, dict],
    canvas: tuple[int, int],
    *,
    project_id: str,
    render_id: str,
    on_progress,
    cancelled,
) -> tuple[Asset, Asset | None]:
    """Render a validated timeline document and upload MP4 plus optional SRT."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg binary not found on PATH — see infra/README.md")
    clips = document["clips"]
    duration_ms = max((clip["start_ms"] + clip["duration_ms"] for clip in clips), default=1000)
    tracks = {track["id"]: track for track in document["tracks"]}
    active = [clip for clip in clips if not tracks[clip["track_id"]].get("hidden")]
    on_progress("preparing", 5, "Preparing clips and overlays")
    with tempfile.TemporaryDirectory(prefix="thikra-editor-render-") as tmp_str:
        tmp = Path(tmp_str)
        input_args: list[str] = []
        filters = [f"color=c={document.get('background', '#05070a')}:s={canvas[0]}x{canvas[1]}:r=30:d={duration_ms / 1000}[base]"]
        visual_labels: list[tuple[dict, str]] = []
        audio_labels: list[tuple[str, str]] = []
        input_index = 0
        ordered = sorted(active, key=lambda item: (tracks[item["track_id"]]["order"], item["start_ms"]))
        for clip_index, clip in enumerate(ordered):
            if clip["kind"] in {"text", "caption"}:
                path = _text_card(clip, canvas, tmp / f"text-{clip_index}.png")
                input_args += ["-loop", "1", "-t", str(clip["duration_ms"] / 1000), "-i", str(path)]
                label = f"v{clip_index}"
                transform = clip.get("transform") or {}
                chain = f"[{input_index}:v]format=rgba,colorchannelmixer=aa={float(transform.get('opacity', 1))}"
                fade_in = min(clip["duration_ms"] // 2, int(transform.get("fade_in_ms", 0)))
                fade_out = min(clip["duration_ms"] // 2, int(transform.get("fade_out_ms", 0)))
                if fade_in:
                    chain += f",fade=t=in:st=0:d={fade_in / 1000}:alpha=1"
                if fade_out:
                    chain += f",fade=t=out:st={(clip['duration_ms'] - fade_out) / 1000}:d={fade_out / 1000}:alpha=1"
                filters.append(f"{chain},setpts=PTS-STARTPTS+{clip['start_ms']}/1000/TB[{label}]")
                visual_labels.append((clip, label))
                input_index += 1
                continue
            descriptor = assets.get(clip.get("asset_id"))
            if not descriptor:
                continue
            suffix = Path(urlparse(descriptor.get("url") or descriptor.get("path") or "source.bin").path).suffix or ".bin"
            path = _localize(descriptor.get("path") or descriptor.get("url"), tmp / f"source-{clip_index}{suffix}")
            start, length = clip.get("source_in_ms", 0) / 1000, clip["duration_ms"] / 1000
            if clip["kind"] == "image":
                input_args += ["-loop", "1", "-t", str(length), "-i", str(path)]
            else:
                input_args += ["-ss", str(start), "-t", str(length), "-i", str(path)]
            if clip["kind"] in {"video", "image"}:
                transform = clip.get("transform") or {}
                fit = transform.get("fit", "fill")
                normalize = (
                    f"scale={canvas[0]}:{canvas[1]}:force_original_aspect_ratio={'increase' if fit == 'fill' else 'decrease'},"
                    + (f"crop={canvas[0]}:{canvas[1]}" if fit == "fill" else f"pad={canvas[0]}:{canvas[1]}:(ow-iw)/2:(oh-ih)/2")
                )
                opacity = float(transform.get("opacity", 1))
                chain = f"[{input_index}:v]{normalize},fps=30,format=rgba,colorchannelmixer=aa={opacity}"
                if transform.get("ken_burns") and clip["kind"] == "image":
                    frames = max(1, round(clip["duration_ms"] / 1000 * 30))
                    chain += f",zoompan=z='min(zoom+0.0005,1.08)':d={frames}:s={canvas[0]}x{canvas[1]}:fps=30"
                scale = float(transform.get("scale", 1))
                rotation = float(transform.get("rotation", 0))
                if scale != 1:
                    chain += f",scale=iw*{scale}:ih*{scale}"
                if rotation:
                    chain += f",rotate={rotation}*PI/180:ow=rotw(iw):oh=roth(ih):c=none"
                transition_in = clip.get("transition_in", "cut")
                transition_out = clip.get("transition_out", "cut")
                transition_ms = int(clip.get("transition_duration_ms", 0))
                fade_in = min(clip["duration_ms"] // 2, int(transform.get("fade_in_ms", 0) or (transition_ms if transition_in != "cut" else 0)))
                fade_out = min(clip["duration_ms"] // 2, int(transform.get("fade_out_ms", 0) or (transition_ms if transition_out != "cut" else 0)))
                if fade_in:
                    chain += f",fade=t=in:st=0:d={fade_in / 1000}:alpha={1 if transition_in == 'dissolve' else 0}:color=black"
                if fade_out:
                    chain += f",fade=t=out:st={(clip['duration_ms'] - fade_out) / 1000}:d={fade_out / 1000}:alpha={1 if transition_out == 'dissolve' else 0}:color=black"
                label = f"v{clip_index}"
                filters.append(f"{chain},setpts=PTS-STARTPTS+{clip['start_ms']}/1000/TB[{label}]")
                visual_labels.append((clip, label))
                if clip["kind"] == "video" and descriptor.get("has_audio") and not tracks[clip["track_id"]].get("muted"):
                    audio = clip.get("audio") or {}
                    label = f"a{clip_index}"
                    gain = float(audio.get("gain_db", 0))
                    filters.append(
                        f"[{input_index}:a]atrim=duration={length},asetpts=PTS-STARTPTS,"
                        f"volume={10 ** (gain / 20):.5f},adelay={clip['start_ms']}|{clip['start_ms']}[{label}]"
                    )
                    audio_labels.append((label, str(audio.get("role", "source"))))
            elif clip["kind"] == "audio" and not tracks[clip["track_id"]].get("muted"):
                audio = clip.get("audio") or {}
                if not audio.get("muted"):
                    label = f"a{clip_index}"
                    gain = float(audio.get("gain_db", 0))
                    chain = f"[{input_index}:a]atrim=duration={length},asetpts=PTS-STARTPTS,volume={10 ** (gain / 20):.5f}"
                    if audio.get("fade_in_ms"):
                        chain += f",afade=t=in:st=0:d={audio['fade_in_ms'] / 1000}"
                    if audio.get("fade_out_ms"):
                        chain += f",afade=t=out:st={max(0, length - audio['fade_out_ms'] / 1000)}:d={audio['fade_out_ms'] / 1000}"
                    filters.append(f"{chain},adelay={clip['start_ms']}|{clip['start_ms']}[{label}]")
                    audio_labels.append((label, str(audio.get("role", "other"))))
            input_index += 1
        current = "base"
        for overlay_index, (clip, label) in enumerate(visual_labels):
            next_label = f"canvas{overlay_index}"
            start, end = clip["start_ms"] / 1000, (clip["start_ms"] + clip["duration_ms"]) / 1000
            transform = clip.get("transform") or {}
            x = float(transform.get("position_x", .5))
            y = float(transform.get("position_y", .5))
            filters.append(f"[{current}][{label}]overlay=(W-w)*{x}:(H-h)*{y}:eof_action=pass:enable='between(t,{start},{end})'[{next_label}]")
            current = next_label
        if audio_labels:
            labels = [label for label, _ in audio_labels]
            narration = [label for label, role in audio_labels if role == "narration"]
            music = [label for label, role in audio_labels if role == "music"]
            if document.get("duck_music_under_narration") and narration and music:
                narr_refs = "".join(f"[{label}]" for label in narration)
                music_refs = "".join(f"[{label}]" for label in music)
                filters.append(f"{narr_refs}amix=inputs={len(narration)}:normalize=0[narrmix]")
                filters.append("[narrmix]asplit=2[narrside][narrout]")
                filters.append(f"{music_refs}amix=inputs={len(music)}:normalize=0[musicmix]")
                filters.append("[musicmix][narrside]sidechaincompress=threshold=.08:ratio=8:attack=20:release=500[ducked]")
                other = [label for label in labels if label not in narration and label not in music]
                refs = "[ducked][narrout]" + "".join(f"[{label}]" for label in other)
                count = 2 + len(other)
            else:
                refs = "".join(f"[{label}]" for label in labels)
                count = len(labels)
            filters.append(f"{refs}amix=inputs={count}:duration=longest:normalize=0[aout]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration_ms / 1000}[aout]")
        output = tmp / "sequence.mp4"
        args = [*input_args, "-filter_complex", ";".join(filters), "-map", f"[{current}]", "-t", str(duration_ms / 1000), "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20"]
        args += ["-map", "[aout]", "-c:a", "aac", "-ar", "48000", "-ac", "2"]
        args += ["-movflags", "+faststart", str(output)]
        _run_editor_ffmpeg(args, duration_ms, on_progress, cancelled)
        on_progress("uploading", 96, "Uploading durable project export")
        payload = output.read_bytes()
        key = f"studio/{project_id}/renders/{render_id}/final.mp4"
        backend().put(key, payload, content_type="video/mp4")
        result = Asset(url=backend().get_durable_url(key), media_type="video/mp4", sha256=_sha256(payload), size_bytes=len(payload))
        srt_payload = _srt_from_clips(clips)
        srt_asset = None
        if srt_payload:
            srt_key = f"studio/{project_id}/renders/{render_id}/captions.srt"
            backend().put(srt_key, srt_payload, content_type="application/x-subrip")
            srt_asset = Asset(url=backend().get_durable_url(srt_key), media_type="application/x-subrip", sha256=_sha256(srt_payload), size_bytes=len(srt_payload))
        return result, srt_asset
