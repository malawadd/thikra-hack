"""Shared Agent Gateway facade.

REST and MCP remain thin transports over the same commerce services.  This
module owns MCP transaction/session boundaries so the MCP adapter never
imports database models or repositories directly.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.commerce.fulfillment import (
    execute_live_fulfillment,
    retry_order,
    serialize_deliverables,
    serialize_receipt,
    start_order,
)
from app.commerce.models import CommercialOrder, DeliveryReceipt, Dispute, OrderEvent, Quote
from app.commerce.payments import (
    authorize_test_fulfillment,
    create_payment_authorization,
    reconcile_authorization,
    serialize_commerce_payment,
)
from app.commerce.schemas import AgentDeclaration, PrincipalDeclaration, QuoteCreate
from app.commerce.security import AuthContext, authenticate_api_key
from app.commerce.service import (
    accept_quote,
    active_services,
    create_dispute,
    create_order,
    create_quote,
    get_service,
    owns_quote,
    require_order_owner,
    serialize_dispute,
    serialize_order,
    serialize_order_event,
    serialize_quote,
    serialize_service,
)
from app.thikra.database import SessionLocal

logger = logging.getLogger("app.agents.gateway")

_live_fulfillment_threads: set[threading.Thread] = set()


def _run_live_fulfillment(order_id: str) -> None:
    """Run provider work outside the MCP request cancellation scope."""
    try:
        asyncio.run(execute_live_fulfillment(order_id))
    except Exception:
        logger.exception("live test fulfillment worker crashed", extra={"order_id": order_id})


def _schedule_live_fulfillment(order_id: str) -> None:
    """Return the MCP result immediately; provider work can take minutes."""
    worker = threading.Thread(
        target=_run_live_fulfillment,
        args=(order_id,),
        name=f"thikra-fulfillment-{order_id[:8]}",
        daemon=True,
    )
    _live_fulfillment_threads.add(worker)
    worker.start()

    def discard() -> None:
        _live_fulfillment_threads.discard(worker)

    threading.Thread(target=lambda: (worker.join(), discard()), daemon=True).start()


@dataclass(frozen=True)
class GatewayIdentity:
    key_id: str
    application_id: str
    principal_id: str
    scopes: tuple[str, ...]

    def auth(self) -> AuthContext:
        return AuthContext(
            self.key_id,
            self.application_id,
            self.principal_id,
            frozenset(self.scopes),
        )


def verify_gateway_token(token: str) -> GatewayIdentity | None:
    try:
        with SessionLocal() as db:
            auth = authenticate_api_key(db, token)
            identity = GatewayIdentity(
                auth.key_id,
                auth.application_id,
                auth.principal_id,
                tuple(sorted(auth.scopes)),
            )
            db.commit()
            return identity
    except ValueError:
        return None


def list_services() -> dict[str, Any]:
    with SessionLocal() as db:
        items = [serialize_service(item) for item in active_services(db)]
        return {"items": items, "total": len(items)}


def service_detail(service_slug: str) -> dict[str, Any]:
    with SessionLocal() as db:
        return serialize_service(get_service(db, service_slug), detail=True)


async def request_quote(
    identity: GatewayIdentity,
    *,
    service: str,
    input_payload: dict[str, Any],
    buyer_agent: dict[str, Any],
    buyer_principal: dict[str, Any],
    maximum_budget_minor: int | None = None,
    currency: str = "USD",
    callback_url: str | None = None,
) -> dict[str, Any]:
    request = QuoteCreate(
        service=service,
        input=input_payload,
        buyer_agent=AgentDeclaration.model_validate(buyer_agent),
        buyer_principal=PrincipalDeclaration.model_validate(buyer_principal),
        maximum_budget=(
            {"amount_minor": maximum_budget_minor, "currency": currency}
            if maximum_budget_minor is not None
            else None
        ),
        callback_url=callback_url,
    )
    with SessionLocal() as db:
        quote = await create_quote(db, identity.auth(), request)
        return serialize_quote(db, quote)


def get_quote(identity: GatewayIdentity, quote_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        quote = db.get(Quote, quote_id)
        if quote is None:
            raise LookupError("Quote not found")
        if not owns_quote(db, identity.auth(), quote):
            raise PermissionError("Quote belongs to another application")
        return serialize_quote(db, quote)


def accept_quote_by_id(identity: GatewayIdentity, quote_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        quote = db.get(Quote, quote_id)
        if quote is None:
            raise LookupError("Quote not found")
        quote = accept_quote(db, identity.auth(), quote)
        return serialize_quote(db, quote)


def create_order_from_quote(
    identity: GatewayIdentity,
    quote_id: str,
    callback_url: str | None = None,
    external_reference: str | None = None,
) -> dict[str, Any]:
    with SessionLocal() as db:
        quote = db.get(Quote, quote_id)
        if quote is None:
            raise LookupError("Quote not found")
        order = create_order(
            db,
            identity.auth(),
            quote,
            callback_url=callback_url,
            external_reference=external_reference,
        )
        return serialize_order(db, order, detail=True)


def get_order(identity: GatewayIdentity, order_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        require_order_owner(db, identity.auth(), order)
        return serialize_order(db, order, detail=True)


async def create_authorization(
    identity: GatewayIdentity,
    order_id: str,
    user_id: str,
    user_email: str,
) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        payment, checkout = await create_payment_authorization(
            db,
            identity.auth(),
            order,
            user_id=user_id,
            user_email=user_email,
        )
        safe_checkout = {
            key: value
            for key, value in checkout.items()
            if key in {"approval_url", "expires_at", "simulated", "publishable_key"}
        }
        return {
            "payment": serialize_commerce_payment(payment, order),
            "authorization": safe_checkout,
            "next_action": "USER_APPROVAL_REQUIRED",
        }


async def payment_status(identity: GatewayIdentity, order_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        payment, reconciliation = await reconcile_authorization(db, identity.auth(), order)
        return {
            "payment": serialize_commerce_payment(payment, order),
            "reconciliation": reconciliation,
        }


def start_paid_order(identity: GatewayIdentity, order_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        start_order(db, identity.auth(), order)
        return serialize_order(db, order, detail=True)


async def start_test_fulfillment(identity: GatewayIdentity, order_id: str) -> dict[str, Any]:
    """Launch local SANDBOX generation after a documented non-payment bypass."""
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        payment = authorize_test_fulfillment(db, identity.auth(), order)
        job = start_order(db, identity.auth(), order)
        response = {
            "payment": serialize_commerce_payment(payment, order),
            "order": serialize_order(db, order, detail=True),
            "test_fulfillment": {
                "customer_payment_collected": False,
                "provider_spend_may_occur": True,
                "fulfillment_job_id": job.id,
            },
        }
    _schedule_live_fulfillment(order_id)
    return response


def order_status(identity: GatewayIdentity, order_id: str) -> dict[str, Any]:
    order = get_order(identity, order_id)
    events = order_events(identity, order_id)["items"]
    latest = events[-1] if events else None
    return {
        "order_id": order["id"],
        "public_order_number": order["public_order_number"],
        "commercial_status": order["status"],
        "payment": order.get("payment"),
        "fulfillment": order.get("fulfillment"),
        "latest_event": latest,
        "user_action_required": order["status"]
        in {"PAYMENT_AUTHORIZATION_PENDING", "REVIEW_REQUIRED", "DELIVERED"},
    }


def order_events(identity: GatewayIdentity, order_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        require_order_owner(db, identity.auth(), order)
        rows = list(
            db.scalars(
                select(OrderEvent)
                .where(OrderEvent.order_id == order.id)
                .order_by(OrderEvent.created_at, OrderEvent.id)
            )
        )
        return {"items": [serialize_order_event(row) for row in rows]}


def deliverables(identity: GatewayIdentity, order_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        identity.auth().require("deliverables:read")
        require_order_owner(db, identity.auth(), order)
        return serialize_deliverables(db, order)


def delivery_receipt(identity: GatewayIdentity, order_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        require_order_owner(db, identity.auth(), order)
        receipt = db.scalar(select(DeliveryReceipt).where(DeliveryReceipt.order_id == order.id))
        if receipt is None:
            raise LookupError("Delivery receipt not found")
        return serialize_receipt(receipt)


def request_retry(
    identity: GatewayIdentity, order_id: str, component: str, reason: str
) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        retry_order(db, identity.auth(), order, component, reason)
        return serialize_order(db, order, detail=True)


def open_dispute(
    identity: GatewayIdentity,
    order_id: str,
    reason_code: str,
    description: str,
    deliverable_id: str | None = None,
) -> dict[str, Any]:
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        if order is None:
            raise LookupError("Order not found")
        dispute = create_dispute(
            db, identity.auth(), order, reason_code, description, deliverable_id
        )
        return serialize_dispute(dispute)


def get_dispute(identity: GatewayIdentity, dispute_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        dispute = db.get(Dispute, dispute_id)
        if dispute is None:
            raise LookupError("Dispute not found")
        order = db.get(CommercialOrder, dispute.order_id)
        require_order_owner(db, identity.auth(), order)
        return serialize_dispute(dispute)
