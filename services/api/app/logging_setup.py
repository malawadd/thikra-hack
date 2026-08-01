"""Structured logging for the API.

Single stdout handler with a JSON formatter. Every log line carries:

  - timestamp (UTC, ISO 8601)
  - level / logger / message
  - request_id (when emitted inside a request — via ContextVar)
  - duration_ms / status / extras passed via `logger.info(..., extra={...})`
  - exception type + full traceback when `exc_info=True`

Why this matters: the streaming endpoint can fail deep inside a provider
call (OpenAI, Decart, NVIDIA, GMI) or inside ffmpeg. Without structured
logs + a stable request_id we can't correlate the SSE frames the user
saw with the exception the backend logged.

`setup_logging()` is idempotent — safe to call from module load.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Fields the JSONFormatter recognises from `extra={}` — anything else
# passed in `extra` is also emitted, but we list the conventional ones so
# call sites can copy-paste with confidence.
_KNOWN_EXTRAS = (
    "duration_ms",
    "status",
    "endpoint",
    "method",
    "path",
    "stage",
    "step_index",
    "model",
    "provider",
    "run_id",
    "key",
    "size_bytes",
    "scene_count",
    "argv0",
)


class JSONFormatter(logging.Formatter):
    """One JSON object per log line. Extras + request_id always included
    when present; tracebacks emitted as a multi-line string field so the
    log scraper can grep on them."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid is not None:
            entry["request_id"] = rid
        for name in _KNOWN_EXTRAS:
            if hasattr(record, name):
                entry[name] = getattr(record, name)
        # Anything else the caller passed via `extra={...}` that isn't a
        # standard LogRecord attribute — copy it over verbatim.
        for k, v in record.__dict__.items():
            if k in entry or k in _KNOWN_EXTRAS:
                continue
            if k in _LOGRECORD_RESERVED:
                continue
            entry[k] = _safe(v)
        if record.exc_info and record.exc_info[1]:
            exc_type, exc_val, exc_tb = record.exc_info
            entry["exception_type"] = exc_type.__name__ if exc_type else "Exception"
            entry["exception"] = str(exc_val)
            entry["traceback"] = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        return json.dumps(entry, default=str)


def _safe(value: object) -> object:
    """Best-effort JSON-friendly coercion for unknown extras."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


# Standard LogRecord attributes that we don't want to mirror into the
# JSON envelope (they're metadata about the log call itself).
_LOGRECORD_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "asctime",
    "message",
}


def setup_logging(level: int | str = logging.INFO) -> None:
    """Configure root logging — single JSON-to-stdout handler.

    Pass `level="DEBUG"` (or set `LOG_LEVEL=DEBUG` in env) to surface:
      - per-step prompts being sent to every provider
      - every SSE frame the backend yields to the client
      - every B2 put/get/list/exists call (key + size)
      - presigned-URL handoffs (B1 image → B2 Decart `image=` kwarg)
      - Genblaze tracer chatter from inside `Pipeline.astream()`

    Idempotent. Loggers configured here:
      - root → JSON formatter at `level`
      - uvicorn.access → INFO (every request hits stdout, useful for triage)
      - uvicorn.error → INFO
      - botocore / urllib3 → WARNING by default, DEBUG when root is DEBUG
        (botocore at DEBUG dumps every B2 request — useful when chasing an
        auth / signature mismatch)
      - asyncio → WARNING (the loop log is rarely actionable)
      - genblaze_core / genblaze_core.tracers.logging → matches root level
    """
    if isinstance(level, str):
        level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(level)
    is_debug = level <= logging.DEBUG
    for name, lvl in {
        "uvicorn.access": logging.INFO,
        "uvicorn.error": logging.INFO,
        # boto/urllib3 are extremely loud at DEBUG — only enable when the
        # operator explicitly went to DEBUG.
        "botocore": logging.DEBUG if is_debug else logging.WARNING,
        "urllib3": logging.DEBUG if is_debug else logging.WARNING,
        "asyncio": logging.WARNING,
        "genblaze_core.tracers.logging": level,
        "genblaze_core": level,
        "genblaze_openai": level,
        "genblaze_s3": level,
        "genblaze_decart": level,
        "genblaze_nvidia": level,
        "genblaze_gmicloud": level,
    }.items():
        logging.getLogger(name).setLevel(lvl)


def new_request_id() -> str:
    """8-hex-char id — short enough to grep, unique enough for one process."""
    return uuid.uuid4().hex[:8]
