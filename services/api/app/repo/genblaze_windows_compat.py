"""Narrow compatibility patch for Genblaze 0.3.x Windows ``file://`` assets.

The OpenAI image connector emits ``file://`` + a quoted native Windows path.
``urllib.parse`` places that path in ``netloc`` (for example
``file://D%3A%5Ctmp%5Cimage.png``), while the installed Genblaze transfer
reader only inspected ``path``.  The result was a failed transfer before B2
received the generated image.  Keep this at the application boundary until
the upstream SDK accepts native Windows file URIs.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from genblaze_core._utils import ALLOWED_FILE_ROOTS
from genblaze_core.exceptions import StorageError
from genblaze_core.storage import transfer


def install_windows_file_uri_compat() -> None:
    """Teach the SDK transfer reader to retain a Windows URI's drive path."""
    if os.name != "nt" or getattr(transfer, "_thikra_windows_file_uri_compat", False):
        return

    original = transfer._read_local_file

    def read_local_file(url: str, *, extra_roots: list[Path] | None = None) -> tuple[bytes, str | None]:
        parsed = urlparse(url)
        if parsed.scheme != "file" or not parsed.netloc:
            return original(url, extra_roots=extra_roots)

        resolved = Path(unquote(f"{parsed.netloc}{parsed.path}")).resolve()
        allowed = list(ALLOWED_FILE_ROOTS)
        if extra_roots:
            allowed.extend(root.resolve() for root in extra_roots)
        if not any(resolved.is_relative_to(root) for root in allowed):
            raise StorageError(
                f"Access denied: local file path {resolved} is outside allowed directories. "
                "Files must be under temp or output_dir."
            )
        try:
            data = resolved.read_bytes()
        except Exception as exc:
            raise StorageError(f"Failed to read local file {resolved}: {exc}") from exc
        content_type, _ = mimetypes.guess_type(str(resolved))
        return data, content_type

    transfer._read_local_file = read_local_file
    transfer._thikra_windows_file_uri_compat = True
