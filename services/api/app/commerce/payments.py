"""Customer-to-Thikra payment orchestration over the existing Prava gateway."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commerce.models import CommercialOrder
from app.commerce.security import AuthContext
from app.commerce.service import require_order_owner
from app.commerce.state_machine import transition_order
from app.config import settings
from app.thikra.audit import canonical_json
from app.thikra.models import PaymentEvent, PaymentRecord
from app.thikra.payments import EPHEMERAL_CREDENTIALS, gateway, sanitize_payment_result
from app.thikra.service import workspace

TEST_BYPASS_GATEWAY = "TEST_BYPASS"
TEST_BYPASS_PAYMENT_STATE = "TEST_BYPASSED_NO_CUSTOMER_PAYMENT"


def _local_sandbox_test_fulfillment_enabled() -> bool:
    """Whether this process may spend provider credits without customer payment."""
    host = urlparse(settings.thikra_api_base_url).hostname
    return (
        settings.app_mode.upper() == "SANDBOX"
        and settings.thikra_agent_test_fulfillment_enabled
        and host in {"127.0.0.1", "localhost", "::1"}
    )


def authorize_test_fulfillment(
    db: Session, auth: AuthContext, order: CommercialOrder
) -> PaymentRecord:
    """Record a local SANDBOX-only, non-payment authorization for live testing."""
    auth.require("orders:test")
    require_order_owner(db, auth, order)
    if not _local_sandbox_test_fulfillment_enabled():
        raise ValueError(
            "Local Sandbox test fulfillment is disabled or this API host is not loopback"
        )
    if order.quoted_total_minor > settings.thikra_agent_test_max_quote_minor:
        raise ValueError(
            "Test fulfillment quote exceeds the configured local Sandbox spend cap"
        )
    existing = db.scalar(select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id))
    if existing:
        if existing.gateway == TEST_BYPASS_GATEWAY and order.status == "TEST_AUTHORIZED":
            return existing
        raise ValueError("Order already has a payment or fulfillment authorization")
    if order.status != "QUOTED":
        raise ValueError("Test fulfillment requires a newly quoted order")

    payment = PaymentRecord(
        workspace_id=workspace(db).id,
        mandate_id=None,
        commercial_order_id=order.id,
        run_id=None,
        gateway=TEST_BYPASS_GATEWAY,
        environment="SANDBOX",
        external_session_id=None,
        external_order_id=None,
        merchant=settings.thikra_merchant_name,
        currency=order.currency,
        maximum_amount_minor=order.quoted_total_minor,
        invoked_amount_minor=0,
        paid_amount_minor=0,
        authorization_state="TEST_AUTHORIZED",
        payment_state=TEST_BYPASS_PAYMENT_STATE,
        expires_at=None,
        direction="CUSTOMER_TO_THIKRA",
    )
    db.add(payment)
    db.flush()
    db.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="order.test_fulfillment_authorized",
            sanitized_payload_json=canonical_json(
                {
                    "customer_payment_collected": False,
                    "provider_spend_may_occur": True,
                    "quote_cap_minor": settings.thikra_agent_test_max_quote_minor,
                }
            ),
            idempotency_key=f"commerce-test-fulfillment:{order.id}",
        )
    )
    transition_order(
        db,
        order,
        "TEST_AUTHORIZED",
        actor_type="AGENT",
        actor_id=order.buyer_agent_id,
        payload={
            "customer_payment_collected": False,
            "provider_spend_may_occur": True,
            "quote_cap_minor": settings.thikra_agent_test_max_quote_minor,
        },
    )
    db.commit()
    return payment


def serialize_commerce_payment(payment: PaymentRecord, order: CommercialOrder) -> dict:
    return {
        "id": payment.id,
        "order_id": order.id,
        "gateway": payment.gateway,
        "environment": payment.environment,
        "direction": payment.direction,
        "merchant": payment.merchant,
        "currency": payment.currency,
        "authorized_amount_minor": payment.maximum_amount_minor,
        "paid_amount_minor": payment.paid_amount_minor,
        "authorization_state": payment.authorization_state,
        "payment_state": payment.payment_state,
        "simulated": payment.gateway == "DEMO",
        "external_session_reference": payment.external_session_id,
        "external_order_reference": payment.external_order_id,
        "expires_at": payment.expires_at.isoformat() if payment.expires_at else None,
        "created_at": payment.created_at.isoformat(),
        "redress_state": payment.redress_state,
    }


async def create_payment_authorization(
    db: Session,
    auth: AuthContext,
    order: CommercialOrder,
    *,
    user_id: str,
    user_email: str,
) -> tuple[PaymentRecord, dict]:
    auth.require("payments:create")
    require_order_owner(db, auth, order)
    existing = db.scalar(select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id))
    if existing:
        return existing, {
            "approval_url": f"{settings.public_web_url}/orders/{order.public_order_number}",
            "simulated": existing.gateway == "DEMO",
            "expires_at": existing.expires_at.isoformat() if existing.expires_at else None,
        }
    event = transition_order(
        db,
        order,
        "PAYMENT_AUTHORIZATION_PENDING",
        actor_type="AGENT",
        actor_id=order.buyer_agent_id,
        payload={"amount_minor": order.quoted_total_minor, "currency": order.currency},
    )
    request = {
        "mandate_id": f"commercial-order:{order.id}",
        "maximum_amount_minor": order.quoted_total_minor,
        "currency": order.currency,
        "user_id": user_id,
        "user_email": user_email,
        "merchant": settings.thikra_merchant_name,
        "merchant_url": settings.thikra_merchant_url or settings.public_web_url,
        "integration_type": "full_checkout",
        "idempotency_key": f"commerce-payment-{order.id}",
    }
    result = await gateway().create_authorization(request)
    expires_at = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
    payment = PaymentRecord(
        workspace_id=workspace(db).id,
        mandate_id=None,
        commercial_order_id=order.id,
        run_id=None,
        gateway="DEMO" if result.get("simulated") else "PRAVA",
        environment=settings.app_mode.upper(),
        external_session_id=result["session_id"],
        external_order_id=result.get("order_id"),
        merchant=settings.thikra_merchant_name,
        currency=order.currency,
        maximum_amount_minor=order.quoted_total_minor,
        invoked_amount_minor=0,
        paid_amount_minor=0,
        authorization_state="AUTHORIZATION_PENDING",
        payment_state="AWAITING_USER_APPROVAL",
        expires_at=expires_at,
        direction="CUSTOMER_TO_THIKRA",
    )
    db.add(payment)
    db.flush()
    db.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="order.payment_authorization_requested",
            sanitized_payload_json=canonical_json(
                {
                    "session_id": result["session_id"],
                    "order_id": result.get("order_id"),
                    "simulated": bool(result.get("simulated")),
                }
            ),
            idempotency_key=f"commerce-auth:{order.id}",
        )
    )
    db.commit()
    checkout = {
        "approval_url": result.get("iframe_url")
        or f"{settings.public_web_url}/orders/{order.public_order_number}",
        "iframe_url": result.get("iframe_url"),
        "session_token": result.get("session_token"),
        "publishable_key": settings.prava_publishable_key if not result.get("simulated") else None,
        "expires_at": result["expires_at"],
        "amount_minor": order.quoted_total_minor,
        "currency": order.currency,
        "merchant": settings.thikra_merchant_name,
        "simulated": bool(result.get("simulated")),
        "order_event_id": event.id,
    }
    return payment, checkout


def approve_demo_payment(
    db: Session,
    auth: AuthContext,
    order: CommercialOrder,
    *,
    approved_by: str,
) -> PaymentRecord:
    require_order_owner(db, auth, order)
    payment = db.scalar(select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id))
    if payment is None:
        raise LookupError("Payment authorization not found")
    if settings.app_mode.upper() != "DEMO" or payment.gateway != "DEMO":
        raise ValueError("Demo payment approval is available only for simulated transactions")
    if order.status == "PAID":
        return payment
    if order.status != "PAYMENT_AUTHORIZATION_PENDING":
        raise ValueError("Order is not awaiting payment approval")
    payment.authorization_state = "AUTHORIZED"
    order.authorized_total_minor = order.quoted_total_minor
    transition_order(
        db,
        order,
        "PAYMENT_AUTHORIZED",
        actor_type="USER",
        actor_id=approved_by,
        payload={"simulated": True, "bounded_amount_minor": order.quoted_total_minor},
    )
    transition_order(
        db,
        order,
        "PAYMENT_PENDING",
        actor_type="SYSTEM",
        actor_id="demo-payment-gateway",
        payload={"simulated": True},
    )
    payment.payment_state = "SIMULATED_PAID"
    payment.invoked_amount_minor = order.quoted_total_minor
    payment.paid_amount_minor = order.quoted_total_minor
    order.paid_total_minor = order.quoted_total_minor
    transition_order(
        db,
        order,
        "PAID",
        actor_type="SYSTEM",
        actor_id="demo-payment-gateway",
        payload={
            "simulated": True,
            "amount_minor": order.quoted_total_minor,
            "currency": order.currency,
        },
    )
    db.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="order.payment_succeeded",
            sanitized_payload_json=canonical_json(
                {"simulated": True, "amount_minor": order.quoted_total_minor}
            ),
            idempotency_key=f"commerce-demo-paid:{order.id}",
        )
    )
    db.commit()
    return payment


async def reconcile_authorization(
    db: Session, auth: AuthContext, order: CommercialOrder
) -> tuple[PaymentRecord, dict]:
    require_order_owner(db, auth, order)
    payment = db.scalar(select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id))
    if payment is None:
        raise LookupError("Payment authorization not found")
    if payment.gateway == "DEMO":
        return payment, {"status": "awaiting_explicit_demo_approval", "simulated": True}
    result = await gateway().get_authorization(payment.external_session_id)
    sanitized, credentials = sanitize_payment_result(result)
    if credentials:
        EPHEMERAL_CREDENTIALS[payment.external_session_id] = credentials
    status = sanitized.get("status", "pending")
    if status == "completed" and order.status == "PAYMENT_AUTHORIZATION_PENDING":
        payment.authorization_state = "AUTHORIZED"
        order.authorized_total_minor = order.quoted_total_minor
        transition_order(
            db,
            order,
            "PAYMENT_AUTHORIZED",
            actor_type="SYSTEM",
            actor_id="prava-reconciliation",
            payload={"credential_available_server_side": bool(credentials)},
        )
        transition_order(
            db,
            order,
            "PAYMENT_PENDING",
            actor_type="SYSTEM",
            actor_id="thikra-merchant-payment",
            payload={
                "message": "Prava authorization is complete; a documented merchant charge confirmation is still required."
            },
        )
        payment.payment_state = "MERCHANT_CHARGE_REQUIRED"
    elif status == "failed" and order.status == "PAYMENT_AUTHORIZATION_PENDING":
        payment.authorization_state = "FAILED"
        payment.payment_state = "FAILED"
        transition_order(
            db,
            order,
            "FAILED",
            actor_type="SYSTEM",
            actor_id="prava-reconciliation",
            payload={"reason": "Prava authorization failed"},
        )
    db.commit()
    return payment, sanitized


async def record_merchant_charge(
    db: Session,
    auth: AuthContext,
    order: CommercialOrder,
    *,
    txn_ref_id: str,
    approved: bool,
    amount_paid_minor: int,
) -> PaymentRecord:
    auth.require("payments:create")
    require_order_owner(db, auth, order)
    payment = db.scalar(select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id))
    if payment is None:
        raise LookupError("Payment not found")
    if payment.paid_amount_minor:
        if payment.paid_amount_minor != amount_paid_minor:
            raise ValueError("Order already has a different completed payment amount")
        return payment
    if order.status != "PAYMENT_PENDING":
        raise ValueError("Order is not awaiting merchant payment completion")
    if approved and amount_paid_minor != order.quoted_total_minor:
        raise ValueError("Completed payment must exactly match the accepted quote")
    result = await gateway().report_outcome(
        payment.external_session_id,
        {
            "txn_ref_id": txn_ref_id,
            "txn_status": "APPROVED" if approved else "DECLINED",
            "amount_paid": f"{amount_paid_minor / 100:.2f}",
        },
    )
    EPHEMERAL_CREDENTIALS.pop(payment.external_session_id, None)
    payment.invoked_amount_minor = amount_paid_minor
    payment.payment_state = "PAID" if approved else "FAILED"
    if approved:
        payment.paid_amount_minor = amount_paid_minor
        order.paid_total_minor = amount_paid_minor
        transition_order(
            db,
            order,
            "PAID",
            actor_type="SYSTEM",
            actor_id="merchant-payment-reconciliation",
            payload={"amount_minor": amount_paid_minor, "currency": order.currency},
        )
    else:
        transition_order(
            db,
            order,
            "FAILED",
            actor_type="SYSTEM",
            actor_id="merchant-payment-reconciliation",
            payload={"reason": "Merchant payment declined"},
        )
    db.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="order.payment_succeeded" if approved else "order.payment_failed",
            sanitized_payload_json=canonical_json(result),
            idempotency_key=f"commerce-charge:{order.id}:{txn_ref_id}",
        )
    )
    db.commit()
    return payment
