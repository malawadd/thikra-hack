"""Tamper-evident audit-chain helpers using deterministic canonical JSON."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.thikra.models import AuditEvent


def _iso_utc(value: datetime) -> str:
    """Canonicalize timestamps even when SQLite drops timezone metadata."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def append_event(
    db: Session,
    *,
    workspace_id: str,
    run_id: str | None,
    event_type: str,
    actor_type: str,
    actor_id: str,
    payload: dict,
    related_object_ids: list[str] | None = None,
) -> AuditEvent:
    previous = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    previous_hash = previous.event_hash if previous else "0" * 64
    timestamp = datetime.now(UTC)
    body = {
        "workspace_id": workspace_id,
        "run_id": run_id,
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "timestamp": _iso_utc(timestamp),
        "payload": payload,
        "related_object_ids": related_object_ids or [],
    }
    event_hash = hashlib.sha256((canonical_json(body) + previous_hash).encode()).hexdigest()
    event = AuditEvent(
        workspace_id=workspace_id,
        run_id=run_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        payload_json=canonical_json(payload),
        related_object_ids_json=canonical_json(related_object_ids or []),
        previous_event_hash=previous_hash,
        event_hash=event_hash,
        created_at=timestamp,
    )
    db.add(event)
    db.flush()
    return event


def verify_chain(events: list[AuditEvent]) -> bool:
    previous = "0" * 64
    for event in events:
        body = {
            "workspace_id": event.workspace_id,
            "run_id": event.run_id,
            "event_type": event.event_type,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "timestamp": _iso_utc(event.created_at),
            "payload": json.loads(event.payload_json),
            "related_object_ids": json.loads(event.related_object_ids_json),
        }
        expected = hashlib.sha256((canonical_json(body) + previous).encode()).hexdigest()
        if event.previous_event_hash != previous or event.event_hash != expected:
            return False
        previous = event.event_hash
    return True
