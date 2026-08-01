"""Exception → user-facing classification for the SSE `error` frame and the
Stage-A HTTP error body.

Leans on Genblaze's typed `ProviderErrorCode` (+ `RETRYABLE_ERROR_CODES`)
rather than sniffing message strings — the SDK already classifies provider
failures (the `(code=...)` in its warnings). Substring matching is the
fallback only for non-Genblaze errors (ffmpeg) and the unknown default.

`message` is always a clean one-liner and `hint` the next action; raw
exception reprs / tracebacks never reach the client (they are logged
server-side via `logger.exception`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from genblaze_core.exceptions import ProviderError
from genblaze_core.models.enums import RETRYABLE_ERROR_CODES, ProviderErrorCode


@dataclass(frozen=True)
class ClassifiedError:
    """A failure rendered for humans + a machine `code` the UI branches on."""

    code: str  # ProviderErrorCode value | "ffmpeg_missing" | "unknown"
    retryable: bool  # whether a plain re-run might succeed
    message: str  # clean one-liner — never an Exception repr / traceback
    hint: str  # the next action the UI shows under the message
    status: int  # HTTP status for the Stage-A endpoint

    def as_dict(self) -> dict:
        return asdict(self)


# Per-code human message + actionable hint + Stage-A HTTP status. Kept static
# so classification never interpolates raw exception text into user copy.
_CODE_COPY: dict[ProviderErrorCode, tuple[str, str, int]] = {
    ProviderErrorCode.AUTH_FAILURE: (
        "Provider authentication failed.",
        "Check the provider API key in your `.env`, then restart the API.",
        401,
    ),
    ProviderErrorCode.RATE_LIMIT: (
        "Provider rate limit hit.",
        "The provider is rate-limiting — wait a moment and retry.",
        429,
    ),
    ProviderErrorCode.TIMEOUT: (
        "Provider request timed out.",
        "The provider was slow to respond — retry shortly.",
        504,
    ),
    ProviderErrorCode.SERVER_ERROR: (
        "Provider server error.",
        "The provider had a transient error — retry shortly.",
        502,
    ),
    ProviderErrorCode.CONTENT_POLICY: (
        "Prompt rejected by the content filter.",
        "The provider refused this prompt — edit it and try again.",
        422,
    ),
    ProviderErrorCode.MODEL_ERROR: (
        "Model unavailable.",
        "The model is retired or unavailable — update the model id in your `.env`.",
        502,
    ),
    ProviderErrorCode.INVALID_INPUT: (
        "Invalid request.",
        "The request was rejected as invalid — check the prompt and parameters.",
        400,
    ),
    ProviderErrorCode.UNKNOWN: (
        "Provider error.",
        "The provider returned an unspecified error — check the API logs.",
        502,
    ),
}


def classify(exc: Exception) -> ClassifiedError:
    """Map an exception to a `ClassifiedError` for the client."""
    # Typed path — Genblaze providers raise `ProviderError` carrying a code.
    if isinstance(exc, ProviderError) and exc.error_code is not None:
        try:
            code = ProviderErrorCode(exc.error_code)
        except ValueError:
            code = ProviderErrorCode.UNKNOWN
        message, hint, status = _CODE_COPY[code]
        return ClassifiedError(
            code=str(code.value),
            retryable=code in RETRYABLE_ERROR_CODES,
            message=message,
            hint=hint,
            status=status,
        )

    # Composition failures — the composer raises `RuntimeError` for
    # deterministic, non-transient problems: a missing ffmpeg binary, an ffmpeg
    # encode/mux error, or a scene with neither a clip nor a keyframe. A plain
    # re-run re-bills every provider and hits the same wall, so these are NOT
    # retryable (the UI offers Edit/Start-over, not Retry). Keyed on the
    # exception *type*, not message text, so it survives message rewording.
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "ffmpeg" in msg and "not found" in msg:
            return ClassifiedError(
                code="ffmpeg_missing",
                retryable=False,
                message="ffmpeg is not installed on the API host.",
                hint="Install ffmpeg (see infra/README.md). Your generated assets are saved in B2.",
                status=500,
            )
        return ClassifiedError(
            code="composition",
            retryable=False,
            message="Composing the final video failed.",
            hint="This usually isn't fixed by retrying. Check the API logs; any assets generated so far are saved in B2.",
            status=500,
        )

    # Default — unspecified failure. Treat as retryable; most are transient.
    return ClassifiedError(
        code="unknown",
        retryable=True,
        message="The run failed unexpectedly.",
        hint="Check the API logs for details, then retry.",
        status=502,
    )
