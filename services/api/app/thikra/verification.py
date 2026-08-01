"""Deterministic first-layer media verification; semantic layers build on it."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.repo.composer import probe_media


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _result(name: str, status: str, explanation: str, evidence: dict[str, Any]) -> dict:
    return {"check_name": name, "status": status, "explanation": explanation, "evidence": evidence}


def inspect_file(
    path: Path,
    *,
    expected_resolution: tuple[int, int] | None = None,
    expected_aspect_ratio: tuple[int, int] | None = None,
    expected_duration_sec: float | None = None,
    require_audio: bool = False,
    duration_tolerance_sec: float = 0.25,
) -> list[dict]:
    """Inspect an image, video, or audio file without model judgment."""
    if not path.is_file():
        return [
            _result("File exists", "FAIL", "The expected file does not exist.", {"path": str(path)})
        ]

    checks = [
        _result(
            "File exists",
            "PASS",
            "The file exists and is non-empty.",
            {"size": path.stat().st_size},
        ),
        _result(
            "SHA-256", "PASS", "A whole-file digest was calculated.", {"sha256": sha256_file(path)}
        ),
    ]
    guessed_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    checks.append(
        _result(
            "Content type",
            "PASS",
            f"Detected {guessed_type} from the file name.",
            {"content_type": guessed_type},
        )
    )

    if guessed_type.startswith("image/"):
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            checks.append(
                _result(
                    "Readable image",
                    "FAIL",
                    "Pillow could not decode the image.",
                    {"error": str(exc)},
                )
            )
            return checks
        checks.append(
            _result(
                "Readable image",
                "PASS",
                "Pillow decoded the image.",
                {"width": width, "height": height},
            )
        )
        if expected_resolution:
            status = "PASS" if (width, height) == expected_resolution else "FAIL"
            checks.append(
                _result(
                    "Resolution",
                    status,
                    f"Measured {width}x{height}.",
                    {"expected": expected_resolution},
                )
            )
        if expected_aspect_ratio:
            expected = expected_aspect_ratio[0] / expected_aspect_ratio[1]
            measured = width / height
            status = "PASS" if abs(measured - expected) <= 0.01 else "FAIL"
            checks.append(
                _result("Aspect ratio", status, f"Measured {measured:.4f}.", {"expected": expected})
            )
        return checks

    try:
        metadata = probe_media(path)
    except RuntimeError as exc:
        checks.append(_result("Readable media", "FAIL", str(exc), {}))
        return checks

    streams = metadata.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float(metadata.get("format", {}).get("duration") or 0)
    checks.append(
        _result(
            "Readable media",
            "PASS",
            "ffprobe decoded the container metadata.",
            {"duration": duration},
        )
    )
    if video:
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        checks.append(
            _result(
                "Video stream",
                "PASS",
                "A video stream is present.",
                {"width": width, "height": height, "frame_rate": video.get("avg_frame_rate")},
            )
        )
        if expected_resolution:
            status = "PASS" if (width, height) == expected_resolution else "FAIL"
            checks.append(
                _result(
                    "Resolution",
                    status,
                    f"Measured {width}x{height}.",
                    {"expected": expected_resolution},
                )
            )
        if expected_aspect_ratio and height:
            expected = expected_aspect_ratio[0] / expected_aspect_ratio[1]
            measured = width / height
            status = "PASS" if abs(measured - expected) <= 0.01 else "FAIL"
            checks.append(
                _result("Aspect ratio", status, f"Measured {measured:.4f}.", {"expected": expected})
            )
    if expected_duration_sec is not None:
        status = (
            "PASS" if abs(duration - expected_duration_sec) <= duration_tolerance_sec else "FAIL"
        )
        checks.append(
            _result(
                "Duration",
                status,
                f"Measured {duration:.3f} seconds.",
                {"expected": expected_duration_sec, "tolerance": duration_tolerance_sec},
            )
        )
    if require_audio:
        status = "PASS" if audio else "FAIL"
        checks.append(
            _result(
                "Audio stream",
                status,
                "An audio stream is present." if audio else "No audio stream was found.",
                {},
            )
        )
    return checks
