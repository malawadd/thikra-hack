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
    steps = b2_run.run.steps
    kf_steps = b1_run.run.steps
    bundles: list[_SceneBundle] = []
    for i, scene in enumerate(spec.scenes):
        vi, ti = 2 * i, 2 * i + 1
        video_url = _asset_url_or_none(steps[vi]) if vi < len(steps) else None
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
                "scene_index": i, "video_step_index": vi,
            })
        narration_url = _asset_url_or_none(steps[ti]) if ti < len(steps) else None
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


def _music_url(b2_run) -> str | None:
    """URL of the trailing music step's asset, or None when music is absent.

    Music is the LAST step of the Stage B2 run. With `fail_fast=False` a
    failed music step is still present (order preserved) but assetless, so
    treat 'present-but-assetless' as 'no music'."""
    steps = b2_run.run.steps
    if not steps:
        return None
    return _asset_url_or_none(steps[-1])


def _download_music(b2_run, tmp: Path) -> Path | None:
    """Download the run's music track, or None when music is unavailable."""
    url = _music_url(b2_run)
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


# Canonical output canvas. Every scene clip — real Decart video or a
# keyframe-still fallback — is scaled+padded to this, so the concat filter
# never trips over mismatched source dimensions / SAR.
_CANVAS_W, _CANVAS_H, _FPS = 1280, 720, 30
_NORMALIZE = (
    f"scale={_CANVAS_W}:{_CANVAS_H}:force_original_aspect_ratio=decrease,"
    f"pad={_CANVAS_W}:{_CANVAS_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={_FPS}"
)


def _concat_video(scenes: list[_SceneBundle], tmp: Path) -> Path:
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
        filters.append(f"[{idx}:v]{_NORMALIZE}[v{idx}]")
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
    scenes: list[_SceneBundle], music_path: Path | None, tmp: Path,
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

    filters.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}"
        ":duration=longest:dropout_transition=0[aout]"
    )
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

def compose_final(b2_run, b1_run, spec: StoryboardSpec) -> tuple[Asset, list[str]]:
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
        music_path = _download_music(b2_run, tmp)
        # Notices derive from the grouped bundles + music presence (single
        # source of layout truth); `_finalize_with_captions` may append a
        # caption-degradation notice below.
        notices = degradation_notices(scenes, music_path is not None)
        logger.info("compose start", extra={
            "run_id": run_id, "scene_count": len(spec.scenes),
            "total_duration_sec": spec.total_duration_sec,
            "degradation_notices": notices,
        })

        video_only = _concat_video(scenes, tmp)
        audio_mix = _mix_audio(scenes, music_path, tmp)
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
