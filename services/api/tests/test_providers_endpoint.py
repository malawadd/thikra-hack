"""`GET /providers` shape + the media-stream's selection validation (422)."""

import os

os.environ.setdefault("B2_BUCKET_NAME", "_")
os.environ.setdefault("B2_REGION", "us-west-004")
os.environ.setdefault("B2_KEY_ID", "_")
os.environ.setdefault("B2_APPLICATION_KEY", "_")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_providers_endpoint_returns_full_matrix() -> None:
    resp = client.get("/providers")
    assert resp.status_code == 200
    matrix = resp.json()["providers"]
    assert set(matrix) == {"chat", "image", "video", "tts", "music"}
    # Video lists the kitchen-sink set including the newly-released vendors.
    video_vendors = {row["vendor"] for row in matrix["video"]}
    assert {"replicate", "runway", "luma", "gmicloud"} <= video_vendors
    # Every row carries the fields the UI dropdowns need.
    for row in matrix["image"]:
        assert row["default_model"]
        assert isinstance(row["key_available"], bool)


def test_media_stream_rejects_unknown_vendor_before_streaming() -> None:
    """A bogus vendor 422s at selection-resolution time — BEFORE any provider
    call or storyboard generation fires (so the test needs no live keys)."""
    resp = client.post("/runs/media/stream", json={
        "prompt": "a valid seed prompt",
        "selection": {"image": {"vendor": "does-not-exist"}},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "bad_selection"
