"""One storage boundary for non-media evidence; media remains Genblaze-owned."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.repo.pipelines import backend


class EvidenceStorage(Protocol):
    def put_json(self, key: str, document: dict) -> str: ...


class LocalEvidenceStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def put_json(self, key: str, document: dict) -> str:
        destination = (self.root / key).resolve()
        if self.root not in destination.parents:
            raise ValueError("Evidence key escapes the configured data directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        normalized = key.replace("\\", "/")
        return f"local://{normalized}"


class B2EvidenceStorage:
    def put_json(self, key: str, document: dict) -> str:
        payload = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        backend().put(key, payload, content_type="application/json")
        return backend().get_durable_url(key)


def evidence_storage() -> EvidenceStorage:
    b2_ready = all(
        (
            settings.b2_region,
            settings.b2_key_id,
            settings.b2_application_key,
            settings.b2_bucket_name,
        )
    )
    if settings.app_mode.upper() != "DEMO" and b2_ready:
        return B2EvidenceStorage()
    return LocalEvidenceStorage(Path(settings.thikra_data_dir))


def evidence_key(workspace_id: str, run_id: str) -> str:
    return f"thikra/workspaces/{workspace_id}/runs/{run_id}/evidence/evidence-export.json"
