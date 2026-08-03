"""Entry point for the frozen Thikra Studio loopback engine."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    root = _bundle_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")


def main() -> None:
    parser = argparse.ArgumentParser(description="Thikra Studio embedded API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("The packaged Studio API may only bind to 127.0.0.1")
    os.environ.setdefault("THIKRA_DESKTOP", "1")
    from app.main import app

    _migrate()
    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_config=None,
        access_log=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
