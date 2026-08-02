"""Compatibility shim for the installed OpenAI Videos SDK."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from genblaze_core import Asset, VideoMetadata
from genblaze_core._utils import open_pinned_https_connection
from genblaze_core.exceptions import ProviderError
from PIL import Image, ImageOps


def _download_reference(url: str, timeout: float, size: str) -> Path:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    target = Path(name)
    conn = None
    try:
        conn = open_pinned_https_connection(url, timeout=timeout, exc_type=ProviderError)
        conn.request("GET", path, headers={"Host": parsed.hostname or ""})
        response = conn.getresponse()
        if response.status >= 300:
            raise ProviderError(f"HTTP {response.status} downloading Sora reference")
        target.write_bytes(response.read())
        width, height = (int(part) for part in size.split("x", maxsplit=1))
        with Image.open(target) as image:
            ImageOps.fit(image.convert("RGB"), (width, height), Image.Resampling.LANCZOS).save(
                target, format="PNG"
            )
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        if conn is not None:
            conn.close()


def submit_sora(provider: Any, step: Any) -> Any:
    """Submit an image-guided Sora request using the SDK's multipart shape."""
    reference_file = None
    try:
        payload = provider.prepare_payload(step)
        params: dict[str, Any] = {
            "model": step.model,
            "prompt": payload.get("prompt", step.prompt or ""),
        }
        for key in ("seconds", "size"):
            if key in payload:
                params[key] = payload[key]
        if "image" in payload:
            reference_file = _download_reference(
                payload["image"], provider._http_timeout, payload.get("size", "720x1280")
            )
            params["input_reference"] = reference_file
        return provider._get_client().videos.create(**params).id
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"Sora submit failed: {exc}") from exc
    finally:
        if reference_file is not None:
            reference_file.unlink(missing_ok=True)


def fetch_sora_output(provider: Any, prediction_id: Any, step: Any) -> Any:
    """Download a completed Sora video using the installed SDK's endpoint name.

    Genblaze 0.3.2 calls the older ``videos.content`` method. The installed
    OpenAI SDK exposes the same endpoint as ``videos.download_content``.
    """
    try:
        client = provider._get_client()
        video = provider._get_cached_poll_result(prediction_id)
        if video is None:
            video = client.videos.retrieve(prediction_id)
        step.provider_payload = {
            "openai": {
                "video_id": video.id,
                "model": getattr(video, "model", None),
                "status": video.status,
            }
        }
        if video.status == "failed":
            raise ProviderError(str(getattr(video, "error", None) or "Video generation failed"))

        content = client.videos.download_content(prediction_id, variant="video")
        if provider._output_dir:
            provider._output_dir.mkdir(parents=True, exist_ok=True)
            out_path = provider._output_dir / f"{step.step_id}.mp4"
        else:
            fd, name = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
            out_path = Path(name)
        content.write_to_file(str(out_path))

        asset = Asset(url=f"file://{quote(str(out_path.resolve()))}", media_type="video/mp4")
        asset.video = VideoMetadata(has_audio=False, codec="h264")
        step.assets.append(asset)
        provider._apply_registry_pricing(step)
        return step
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"Sora fetch_output failed: {exc}") from exc
