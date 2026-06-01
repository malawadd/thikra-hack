"""Error-classification tests — `classify()` maps exceptions to clean,
actionable `ClassifiedError`s for the client.

Asserts the typed `ProviderErrorCode` path (retryable derived from the SDK's
`RETRYABLE_ERROR_CODES`, not hand-coded), the ffmpeg fallback, the unknown
default, and that no raw exception repr / traceback leaks into `message`.
"""

import os

os.environ.setdefault("B2_BUCKET_NAME", "_")
os.environ.setdefault("B2_REGION", "us-west-004")
os.environ.setdefault("B2_KEY_ID", "_")
os.environ.setdefault("B2_APPLICATION_KEY", "_")

import pytest
from genblaze_core.exceptions import ProviderError
from genblaze_core.models.enums import (
    RETRYABLE_ERROR_CODES,
    ProviderErrorCode,
)

from app.errors import classify

# A leaky secret/stack marker we assert never appears in user-facing copy.
_LEAK = "Traceback (most recent call last)"


@pytest.mark.parametrize("code", list(ProviderErrorCode))
def test_provider_error_codes_map_and_derive_retryable(code: ProviderErrorCode) -> None:
    """Every typed code classifies, and `retryable` comes from the SDK set."""
    ce = classify(ProviderError("upstream said no", error_code=code))
    assert ce.code == code.value
    assert ce.retryable is (code in RETRYABLE_ERROR_CODES)
    assert 400 <= ce.status <= 504
    assert ce.message and ce.hint
    # The raw upstream message must NOT leak into user copy.
    assert "upstream said no" not in ce.message
    assert "upstream said no" not in ce.hint


def test_auth_failure_is_not_retryable_and_401() -> None:
    ce = classify(ProviderError("bad key", error_code=ProviderErrorCode.AUTH_FAILURE))
    assert ce.code == "auth_failure"
    assert ce.retryable is False
    assert ce.status == 401


def test_rate_limit_is_retryable_and_429() -> None:
    ce = classify(ProviderError("slow down", error_code=ProviderErrorCode.RATE_LIMIT))
    assert ce.retryable is True
    assert ce.status == 429


def test_content_policy_is_not_retryable() -> None:
    ce = classify(ProviderError("refused", error_code=ProviderErrorCode.CONTENT_POLICY))
    assert ce.code == "content_policy"
    assert ce.retryable is False


def test_ffmpeg_missing_fallback() -> None:
    ce = classify(RuntimeError("ffmpeg binary not found on PATH — see infra/README.md"))
    assert ce.code == "ffmpeg_missing"
    assert ce.retryable is False
    assert "B2" in ce.hint  # tells the user their assets are safe


def test_unknown_default_is_retryable() -> None:
    ce = classify(ValueError("something weird happened"))
    assert ce.code == "unknown"
    assert ce.retryable is True
    assert "something weird happened" not in ce.message  # no leak


def test_provider_error_without_code_falls_through_to_unknown() -> None:
    ce = classify(ProviderError("no code set"))  # error_code=None
    assert ce.code == "unknown"


def test_as_dict_shape_is_wire_ready() -> None:
    ce = classify(ProviderError("x", error_code=ProviderErrorCode.MODEL_ERROR))
    d = ce.as_dict()
    assert set(d) == {"code", "retryable", "message", "hint", "status"}
    assert _LEAK not in d["message"]
