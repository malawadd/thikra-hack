"""Authoritative commercial-order transitions and event hashes."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commerce.models import CommercialOrder, OrderEvent
from app.thikra.audit import append_event, canonical_json
from app.thikra.service import workspace

TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"QUOTED", "CANCELLED"},
    "QUOTED": {"QUOTE_EXPIRED", "PAYMENT_AUTHORIZATION_PENDING", "TEST_AUTHORIZED", "CANCELLED"},
    "QUOTE_EXPIRED": set(),
    "PAYMENT_AUTHORIZATION_PENDING": {"PAYMENT_AUTHORIZED", "FAILED", "CANCELLED"},
    "PAYMENT_AUTHORIZED": {"PAYMENT_PENDING", "CANCELLED"},
    "PAYMENT_PENDING": {"PAID", "FAILED", "CANCELLED"},
    "TEST_AUTHORIZED": {"ACCEPTED", "CANCELLED", "REDRESS_OPEN"},
    "PAID": {"ACCEPTED", "CANCELLED", "REDRESS_OPEN"},
    "ACCEPTED": {"FULFILLMENT_PENDING", "REDRESS_OPEN"},
    "FULFILLMENT_PENDING": {"FULFILLING", "FAILED", "REDRESS_OPEN"},
    "FULFILLING": {"VERIFYING", "FAILED", "REDRESS_OPEN"},
    "VERIFYING": {"REVIEW_REQUIRED", "FULFILLING", "READY", "REDRESS_OPEN", "FAILED"},
    "REVIEW_REQUIRED": {"FULFILLING", "READY", "REDRESS_OPEN", "FAILED"},
    "READY": {"DELIVERED", "REDRESS_OPEN"},
    "DELIVERED": {"COMPLETED", "DISPUTED"},
    "COMPLETED": {"DISPUTED"},
    "FAILED": {"REDRESS_OPEN", "CANCELLED"},
    "REDRESS_OPEN": {"REFUND_REQUESTED", "DISPUTED"},
    "REFUND_REQUESTED": {"REFUNDED"},
    "REFUNDED": set(),
    "DISPUTED": {"REDRESS_OPEN", "REFUND_REQUESTED"},
    "CANCELLED": set(),
}

EVENT_BY_STATE = {
    "QUOTED": "order.created",
    "PAYMENT_AUTHORIZATION_PENDING": "order.payment_authorization_requested",
    "PAYMENT_AUTHORIZED": "order.payment_authorized",
    "PAYMENT_PENDING": "order.payment_pending",
    "TEST_AUTHORIZED": "order.test_fulfillment_authorized",
    "PAID": "order.payment_succeeded",
    "ACCEPTED": "order.accepted",
    "FULFILLMENT_PENDING": "order.fulfillment_pending",
    "FULFILLING": "order.fulfillment_started",
    "VERIFYING": "order.verifying",
    "REVIEW_REQUIRED": "order.review_required",
    "READY": "order.ready",
    "DELIVERED": "order.delivered",
    "COMPLETED": "order.completed",
    "FAILED": "order.failed",
    "REDRESS_OPEN": "order.redress_open",
    "REFUND_REQUESTED": "refund.requested",
    "REFUNDED": "refund.completed",
    "DISPUTED": "order.disputed",
    "CANCELLED": "order.cancelled",
    "QUOTE_EXPIRED": "quote.expired",
}


class InvalidOrderTransition(ValueError):
    pass


def append_order_event(
    db: Session,
    order: CommercialOrder,
    event_type: str,
    *,
    actor_type: str,
    actor_id: str,
    payload: dict,
) -> OrderEvent:
    previous = db.scalar(
        select(OrderEvent)
        .where(OrderEvent.order_id == order.id)
        .order_by(OrderEvent.created_at.desc(), OrderEvent.id.desc())
        .limit(1)
    )
    previous_hash = previous.event_hash if previous else "0" * 64
    created_at = datetime.now(UTC)
    body = {
        "order_id": order.id,
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "payload": payload,
        "created_at": created_at.isoformat(),
    }
    event_hash = hashlib.sha256((canonical_json(body) + previous_hash).encode()).hexdigest()
    event = OrderEvent(
        order_id=order.id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        payload_json=canonical_json(payload),
        previous_event_hash=previous_hash,
        event_hash=event_hash,
        created_at=created_at,
    )
    db.add(event)
    db.flush()
    ws = workspace(db)
    append_event(
        db,
        workspace_id=ws.id,
        run_id=payload.get("generation_run_id"),
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        payload=payload | {"order_id": order.id, "public_order_number": order.public_order_number},
        related_object_ids=[order.id, event.id],
    )
    return event


def transition_order(
    db: Session,
    order: CommercialOrder,
    target: str,
    *,
    actor_type: str,
    actor_id: str,
    payload: dict | None = None,
) -> OrderEvent:
    if target not in TRANSITIONS.get(order.status, set()):
        raise InvalidOrderTransition(f"Cannot transition order from {order.status} to {target}")
    previous = order.status
    order.status = target
    now = datetime.now(UTC)
    if target == "PAID":
        order.paid_at = now
    elif target == "ACCEPTED":
        order.accepted_at = now
    elif target == "DELIVERED":
        order.delivered_at = now
    elif target == "COMPLETED":
        order.completed_at = now
    elif target == "CANCELLED":
        order.cancelled_at = now
    return append_order_event(
        db,
        order,
        EVENT_BY_STATE[target],
        actor_type=actor_type,
        actor_id=actor_id,
        payload={"from": previous, "to": target, **(payload or {})},
    )
