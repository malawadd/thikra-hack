"""Verify an exported Thikra receipt with the configured/public Ed25519 key."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from app.commerce.receipts import verify_receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, help="Receipt JSON exported by Thikra")
    args = parser.parse_args()
    document = json.loads(args.receipt.read_text(encoding="utf-8"))
    valid = verify_receipt(
        document["receipt_payload"], document["receipt_hash"], document["signature"]
    )
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger(__name__).info("VALID" if valid else "INVALID")
    if not valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
