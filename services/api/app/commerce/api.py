"""Versioned public REST API backed by the shared commercial domain services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commerce.fulfillment import (
    execute_live_fulfillment,
    retry_order,
    serialize_deliverables,
    serialize_receipt,
    start_order,
    verify_download_signature,
)
from app.commerce.models import (
    APIKey,
    BuyerAgent,
    CommercialOrder,
    Deliverable,
    DeliveryReceipt,
    DeveloperApplication,
    Dispute,
    OrderEvent,
    Quote,
    ServiceOffer,
    WebhookSubscription,
)
from app.commerce.payments import (
    approve_demo_payment,
    authorize_test_fulfillment,
    create_payment_authorization,
    reconcile_authorization,
    record_merchant_charge,
    refresh_payment_authorization,
    serialize_commerce_payment,
)
from app.commerce.receipts import verify_receipt
from app.commerce.schemas import (
    APIKeyCreate,
    DemoPaymentApproval,
    DeveloperApplicationCreate,
    DisputeCreate,
    MerchantChargeReport,
    OrderCreate,
    PaymentAuthorizationCreate,
    QuoteCreate,
    ReceiptVerify,
    RetryCreate,
    ServiceStatusUpdate,
    ServiceVersionCreate,
    WebhookSubscriptionCreate,
    WebhookTestRequest,
)
from app.commerce.security import (
    AuthContext,
    AuthenticationError,
    AuthorizationError,
    IdempotencyConflict,
    authenticate_api_key,
    idempotent_result,
    issue_api_key,
    remember_idempotent_result,
)
from app.commerce.service import (
    accept_quote,
    active_services,
    create_developer_application,
    create_dispute,
    create_order,
    create_quote,
    create_service_version,
    create_webhook_subscription,
    get_service,
    owns_quote,
    require_order_owner,
    serialize_application,
    serialize_dispute,
    serialize_order,
    serialize_order_event,
    serialize_quote,
    serialize_service,
    serialize_webhook_subscription,
    set_service_status,
)
from app.commerce.state_machine import (
    InvalidOrderTransition,
    append_order_event,
    transition_order,
)
from app.commerce.webhooks import deliver_order_event
from app.config import settings
from app.repo.pipelines import presign_asset_url
from app.thikra.database import get_db
from app.thikra.models import Asset as DBAsset
from app.thikra.models import PaymentRecord

router = APIRouter(prefix="/api/v1", tags=["Agent Commerce"])


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return _error(404, "NOT_FOUND", str(exc))
    if isinstance(exc, (PermissionError, AuthorizationError)):
        return _error(403, "FORBIDDEN", str(exc))
    if isinstance(exc, AuthenticationError):
        return _error(401, "AUTHENTICATION_REQUIRED", str(exc))
    if isinstance(exc, IdempotencyConflict):
        return _error(409, "IDEMPOTENCY_CONFLICT", str(exc))
    if isinstance(exc, (ValueError, InvalidOrderTransition)):
        return _error(409, "COMMERCE_POLICY_CONFLICT", str(exc))
    return _error(500, "COMMERCE_INTERNAL_ERROR", "Commercial operation failed")


def _token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise _error(401, "AUTHENTICATION_REQUIRED", "Use Authorization: Bearer thikra_test_…")
    return authorization.removeprefix("Bearer ").strip()


def auth_context(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> AuthContext:
    try:
        context = authenticate_api_key(db, _token(authorization))
        db.commit()
        return context
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc


def _key(value: str | None) -> str:
    if not value or len(value) < 8 or len(value) > 180:
        raise _error(400, "IDEMPOTENCY_KEY_REQUIRED", "A unique Idempotency-Key header is required")
    return value


def _cached(db: Session, auth: AuthContext, operation: str, key: str, payload: dict) -> dict | None:
    try:
        return idempotent_result(db, auth, operation, key, payload)
    except Exception as exc:
        raise _translate(exc) from exc


def _remember(
    db: Session,
    auth: AuthContext,
    operation: str,
    key: str,
    payload: dict,
    kind: str,
    resource_id: str,
    response: dict,
) -> dict:
    remember_idempotent_result(db, auth, operation, key, payload, kind, resource_id, response)
    db.commit()
    return response


def _quote(db: Session, quote_id: str) -> Quote:
    item = db.get(Quote, quote_id)
    if item is None:
        raise _error(404, "QUOTE_NOT_FOUND", "Quote not found")
    return item


def _order(db: Session, order_id: str) -> CommercialOrder:
    item = db.get(CommercialOrder, order_id)
    if item is None:
        raise _error(404, "ORDER_NOT_FOUND", "Order not found")
    return item


@router.get("/services")
def list_services(db: Session = Depends(get_db)):
    services = active_services(db)
    return {"items": [serialize_service(item) for item in services], "total": len(services)}


@router.get("/services/{service_slug}")
def service_detail(service_slug: str, db: Session = Depends(get_db)):
    try:
        return serialize_service(get_service(db, service_slug), detail=True)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/quotes", status_code=201)
async def request_quote(
    request: QuoteCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = request.model_dump(mode="json")
    cached = _cached(db, auth, "quote.create", key, payload)
    if cached:
        return cached
    try:
        quote = await create_quote(db, auth, request)
        response = serialize_quote(db, quote)
        return _remember(db, auth, "quote.create", key, payload, "quote", quote.id, response)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/quotes/{quote_id}")
def get_quote(
    quote_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    quote = _quote(db, quote_id)
    if not owns_quote(db, auth, quote):
        raise _error(403, "QUOTE_OWNERSHIP_REQUIRED", "Quote belongs to another application")
    return serialize_quote(db, quote)


@router.post("/quotes/{quote_id}/accept")
def accept_quote_route(
    quote_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = {"quote_id": quote_id}
    cached = _cached(db, auth, "quote.accept", key, payload)
    if cached:
        return cached
    try:
        quote = accept_quote(db, auth, _quote(db, quote_id))
        response = serialize_quote(db, quote)
        return _remember(db, auth, "quote.accept", key, payload, "quote", quote.id, response)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders", status_code=201)
def create_order_route(
    request: OrderCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = request.model_dump(mode="json")
    cached = _cached(db, auth, "order.create", key, payload)
    if cached:
        return cached
    try:
        order = create_order(
            db,
            auth,
            _quote(db, request.quote_id),
            callback_url=str(request.callback_url) if request.callback_url else None,
            external_reference=request.external_reference,
        )
        response = serialize_order(db, order, detail=True)
        return _remember(db, auth, "order.create", key, payload, "order", order.id, response)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/orders")
def list_orders(auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(CommercialOrder).order_by(CommercialOrder.created_at.desc())))
    items = []
    for row in rows:
        try:
            require_order_owner(db, auth, row)
            items.append(serialize_order(db, row))
        except PermissionError:
            continue
    return {"items": items, "total": len(items)}


@router.get("/orders/by-number/{public_order_number}")
def order_by_number(
    public_order_number: str,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    item = db.scalar(
        select(CommercialOrder).where(CommercialOrder.public_order_number == public_order_number)
    )
    if item is None:
        raise _error(404, "ORDER_NOT_FOUND", "Order not found")
    try:
        require_order_owner(db, auth, item)
    except Exception as exc:
        raise _translate(exc) from exc
    return serialize_order(db, item, detail=True)


@router.get("/orders/{order_id}")
def get_order(
    order_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    item = _order(db, order_id)
    try:
        auth.require("orders:read")
        require_order_owner(db, auth, item)
        return serialize_order(db, item, detail=True)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/orders/{order_id}/events")
def order_events(
    order_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    item = _order(db, order_id)
    try:
        require_order_owner(db, auth, item)
    except Exception as exc:
        raise _translate(exc) from exc
    rows = list(
        db.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == item.id)
            .order_by(OrderEvent.created_at, OrderEvent.id)
        )
    )
    return {"items": [serialize_order_event(row) for row in rows]}


@router.get("/orders/{order_id}/stream")
def order_stream(
    order_id: str,
    after: str | None = Query(default=None),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    item = _order(db, order_id)
    try:
        require_order_owner(db, auth, item)
    except Exception as exc:
        raise _translate(exc) from exc
    rows = list(
        db.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == item.id)
            .order_by(OrderEvent.created_at, OrderEvent.id)
        )
    )
    if after:
        ids = [row.id for row in rows]
        if after in ids:
            rows = rows[ids.index(after) + 1 :]

    async def generate():
        for row in rows:
            yield f"id: {row.id}\ndata: {json.dumps(serialize_order_event(row))}\n\n"

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/orders/{order_id}/payment-authorization", status_code=201)
async def payment_authorization(
    order_id: str,
    request: PaymentAuthorizationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = request.model_dump(mode="json") | {"order_id": order_id}
    cached = _cached(db, auth, "payment.authorization", key, payload)
    if cached:
        return cached
    try:
        order = _order(db, order_id)
        payment, checkout = await create_payment_authorization(
            db, auth, order, user_id=request.user_id, user_email=str(request.user_email)
        )
        response = {"payment": serialize_commerce_payment(payment, order), "checkout": checkout}
        return _remember(
            db, auth, "payment.authorization", key, payload, "payment", payment.id, response
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/payment-authorization/refresh")
async def refresh_payment_authorization_route(
    order_id: str,
    request: PaymentAuthorizationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = request.model_dump(mode="json") | {"order_id": order_id}
    cached = _cached(db, auth, "payment.authorization.refresh", key, payload)
    if cached:
        return cached
    try:
        order = _order(db, order_id)
        payment, checkout = await refresh_payment_authorization(
            db, auth, order, user_id=request.user_id, user_email=str(request.user_email)
        )
        response = {"payment": serialize_commerce_payment(payment, order), "checkout": checkout}
        return _remember(
            db, auth, "payment.authorization.refresh", key, payload, "payment", payment.id, response
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/payment/confirm-demo")
def confirm_demo_payment(
    order_id: str,
    request: DemoPaymentApproval,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = request.model_dump() | {"order_id": order_id}
    cached = _cached(db, auth, "payment.demo-confirm", key, payload)
    if cached:
        return cached
    try:
        order = _order(db, order_id)
        payment = approve_demo_payment(db, auth, order, approved_by=request.approved_by)
        response = {
            "payment": serialize_commerce_payment(payment, order),
            "order": serialize_order(db, order, detail=True),
        }
        return _remember(
            db, auth, "payment.demo-confirm", key, payload, "payment", payment.id, response
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/orders/{order_id}/payment")
async def payment_status(
    order_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    try:
        order = _order(db, order_id)
        payment, reconciliation = await reconcile_authorization(db, auth, order)
        return {
            "payment": serialize_commerce_payment(payment, order),
            "reconciliation": reconciliation,
        }
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/payment/reconcile")
async def merchant_charge_reconcile(
    order_id: str,
    request: MerchantChargeReport,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = request.model_dump() | {"order_id": order_id}
    cached = _cached(db, auth, "payment.reconcile", key, payload)
    if cached:
        return cached
    try:
        order = _order(db, order_id)
        payment = await record_merchant_charge(
            db,
            auth,
            order,
            txn_ref_id=request.txn_ref_id,
            approved=request.status == "APPROVED",
            amount_paid_minor=request.amount_paid_minor,
        )
        response = {
            "payment": serialize_commerce_payment(payment, order),
            "order": serialize_order(db, order),
        }
        return _remember(
            db, auth, "payment.reconcile", key, payload, "payment", payment.id, response
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/start")
def start_order_route(
    order_id: str,
    background: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = {"order_id": order_id}
    cached = _cached(db, auth, "fulfillment.start", key, payload)
    if cached:
        return cached
    try:
        order = _order(db, order_id)
        job = start_order(db, auth, order)
        if settings.app_mode.upper() != "DEMO":
            background.add_task(execute_live_fulfillment, order.id)
        response = serialize_order(db, order, detail=True)
        return _remember(
            db, auth, "fulfillment.start", key, payload, "fulfillment_job", job.id, response
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/test-fulfillment")
def start_test_fulfillment_route(
    order_id: str,
    background: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = {"order_id": order_id}
    cached = _cached(db, auth, "fulfillment.test-start", key, payload)
    if cached:
        return cached
    try:
        order = _order(db, order_id)
        payment = authorize_test_fulfillment(db, auth, order)
        job = start_order(db, auth, order)
        background.add_task(execute_live_fulfillment, order.id)
        response = {
            "payment": serialize_commerce_payment(payment, order),
            "order": serialize_order(db, order, detail=True),
        }
        return _remember(
            db, auth, "fulfillment.test-start", key, payload, "fulfillment_job", job.id, response
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = {"order_id": order_id}
    cached = _cached(db, auth, "order.cancel", key, payload)
    if cached:
        return cached
    try:
        order = _order(db, order_id)
        require_order_owner(db, auth, order)
        if order.paid_total_minor:
            raise ValueError("Paid orders require redress; cancellation cannot imply a refund")
        transition_order(db, order, "CANCELLED", actor_type="AGENT", actor_id=order.buyer_agent_id)
        db.commit()
        response = serialize_order(db, order, detail=True)
        return _remember(db, auth, "order.cancel", key, payload, "order", order.id, response)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/retry")
def retry_order_route(
    order_id: str,
    request: RetryCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = request.model_dump() | {"order_id": order_id}
    cached = _cached(db, auth, "fulfillment.retry", key, payload)
    if cached:
        return cached
    try:
        order = _order(db, order_id)
        job = retry_order(db, auth, order, request.component, request.reason)
        response = serialize_order(db, order, detail=True)
        return _remember(
            db, auth, "fulfillment.retry", key, payload, "fulfillment_job", job.id, response
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/orders/{order_id}/deliverables")
def order_deliverables(
    order_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    try:
        auth.require("deliverables:read")
        order = _order(db, order_id)
        require_order_owner(db, auth, order)
        if order.status not in {"DELIVERED", "COMPLETED", "DISPUTED", "REDRESS_OPEN"}:
            raise ValueError("Verified deliverables are not ready")
        return serialize_deliverables(db, order)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/orders/{order_id}/delivery-receipt")
def delivery_receipt(
    order_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    try:
        order = _order(db, order_id)
        require_order_owner(db, auth, order)
        receipt = db.scalar(select(DeliveryReceipt).where(DeliveryReceipt.order_id == order.id))
        if receipt is None:
            raise ValueError("Delivery receipt has not been issued")
        return serialize_receipt(receipt)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/delivery-receipts/verify")
def verify_delivery_receipt(request: ReceiptVerify):
    if request.signing_key_id != settings.thikra_receipt_signing_key_id:
        return {"valid": False, "reason": "Unknown signing key"}
    return {
        "valid": verify_receipt(request.receipt_payload, request.receipt_hash, request.signature),
        "signing_key_id": request.signing_key_id,
    }


@router.get("/deliverables/{deliverable_id}/content")
def deliverable_content(
    deliverable_id: str, expires: int, signature: str, db: Session = Depends(get_db)
):
    if not verify_download_signature(deliverable_id, expires, signature):
        raise _error(403, "DOWNLOAD_URL_EXPIRED", "Signed download URL is invalid or expired")
    item = db.get(Deliverable, deliverable_id)
    if item is None:
        raise _error(404, "DELIVERABLE_NOT_FOUND", "Deliverable not found")
    asset = db.get(DBAsset, item.asset_id)
    if asset.object_key.startswith("local://"):
        relative = asset.object_key.removeprefix("local://")
        root = Path(settings.thikra_data_dir).resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            raise _error(404, "DELIVERABLE_CONTENT_MISSING", "Stored evidence file is unavailable")
        return FileResponse(target, media_type=asset.content_type, filename=item.name)
    if asset.object_key.startswith("demo://"):
        return RedirectResponse(
            f"{settings.thikra_api_base_url}/thikra/assets/{asset.id}/content", status_code=302
        )
    return RedirectResponse(presign_asset_url(asset.object_key, expires_in=120), status_code=302)


@router.post("/orders/{order_id}/accept")
def accept_delivery(
    order_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    try:
        order = _order(db, order_id)
        require_order_owner(db, auth, order)
        transition_order(db, order, "COMPLETED", actor_type="AGENT", actor_id=order.buyer_agent_id)
        db.commit()
        return serialize_order(db, order, detail=True)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/disputes", status_code=201)
def open_dispute(
    order_id: str,
    request: DisputeCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = request.model_dump() | {"order_id": order_id}
    cached = _cached(db, auth, "dispute.create", key, payload)
    if cached:
        return cached
    try:
        dispute = create_dispute(
            db,
            auth,
            _order(db, order_id),
            request.reason_code,
            request.description,
            request.deliverable_id,
        )
        response = serialize_dispute(dispute)
        return _remember(db, auth, "dispute.create", key, payload, "dispute", dispute.id, response)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/disputes/{dispute_id}")
def get_dispute(
    dispute_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    item = db.get(Dispute, dispute_id)
    if item is None:
        raise _error(404, "DISPUTE_NOT_FOUND", "Dispute not found")
    try:
        require_order_owner(db, auth, _order(db, item.order_id))
        return serialize_dispute(item)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/disputes/{dispute_id}/refund-request")
def request_refund(
    dispute_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    key = _key(idempotency_key)
    payload = {"dispute_id": dispute_id}
    cached = _cached(db, auth, "refund.request", key, payload)
    if cached:
        return cached
    item = db.get(Dispute, dispute_id)
    if item is None:
        raise _error(404, "DISPUTE_NOT_FOUND", "Dispute not found")
    try:
        order = _order(db, item.order_id)
        require_order_owner(db, auth, order)
        if order.status == "DISPUTED":
            transition_order(
                db, order, "REDRESS_OPEN", actor_type="SYSTEM", actor_id="redress-service"
            )
        transition_order(
            db,
            order,
            "REFUND_REQUESTED",
            actor_type="AGENT",
            actor_id=order.buyer_agent_id,
            payload={"dispute_id": item.id},
        )
        item.status = "REFUND_REQUESTED"
        payment = db.scalar(
            select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id)
        )
        payment.redress_state = "REFUND_REQUESTED_UNSUPPORTED"
        db.commit()
        response = {
            "dispute": serialize_dispute(item),
            "refund": {
                "status": "REQUESTED",
                "completed": False,
                "provider_action": "UNSUPPORTED",
                "message": "The installed Prava contract exposes no refund API; trust operations must reconcile through supported merchant rails.",
            },
        }
        return _remember(db, auth, "refund.request", key, payload, "dispute", item.id, response)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/webhooks/prava")
def prava_webhook_contract():
    return JSONResponse(
        status_code=501,
        content={
            "code": "PRAVA_WEBHOOK_CONTRACT_UNDOCUMENTED",
            "message": "The installed official Prava integration defines authenticated result polling, not webhook signatures. No unverified webhook is accepted.",
        },
    )


@router.post("/webhooks/subscriptions", status_code=201)
def create_subscription(
    request: WebhookSubscriptionCreate,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    try:
        item, secret = create_webhook_subscription(
            db, auth, str(request.callback_url), request.events
        )
        return {
            "subscription": serialize_webhook_subscription(db, item),
            "secret": secret,
            "secret_display": "shown_once",
        }
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/webhooks/subscriptions")
def list_subscriptions(auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)):
    auth.require("webhooks:manage")
    rows = list(
        db.scalars(
            select(WebhookSubscription)
            .where(WebhookSubscription.application_id == auth.application_id)
            .order_by(WebhookSubscription.created_at.desc())
        )
    )
    return {"items": [serialize_webhook_subscription(db, row) for row in rows]}


@router.delete("/webhooks/subscriptions/{subscription_id}", status_code=204)
def delete_subscription(
    subscription_id: str, auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)
):
    item = db.get(WebhookSubscription, subscription_id)
    if item is None:
        raise _error(404, "WEBHOOK_SUBSCRIPTION_NOT_FOUND", "Webhook subscription not found")
    if item.application_id != auth.application_id:
        raise _error(403, "FORBIDDEN", "Webhook subscription belongs to another application")
    item.status = "DISABLED"
    db.commit()
    return None


@router.post("/webhooks/subscriptions/{subscription_id}/test")
async def test_subscription(
    subscription_id: str,
    request: WebhookTestRequest,
    background: BackgroundTasks,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    item = db.get(WebhookSubscription, subscription_id)
    if item is None or item.application_id != auth.application_id:
        raise _error(404, "WEBHOOK_SUBSCRIPTION_NOT_FOUND", "Webhook subscription not found")
    order = db.scalar(
        select(CommercialOrder)
        .join(BuyerAgent, CommercialOrder.buyer_agent_id == BuyerAgent.id)
        .where(BuyerAgent.application_id == auth.application_id)
        .limit(1)
    )
    if order is None:
        raise _error(409, "ORDER_REQUIRED", "Create an order before sending a signed test event")
    event = append_order_event(
        db,
        order,
        request.event_type,
        actor_type="DEVELOPER",
        actor_id=auth.application_id,
        payload={"test": True},
    )
    db.commit()
    background.add_task(deliver_order_event, event.id)
    return {"event_id": event.id, "queued": True}


@router.post("/webhooks/events/{event_id}/resend", status_code=202)
def resend_webhook_event(
    event_id: str,
    background: BackgroundTasks,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    auth.require("webhooks:manage")
    event = db.get(OrderEvent, event_id)
    if event is None:
        raise _error(404, "WEBHOOK_EVENT_NOT_FOUND", "Webhook event not found")
    require_order_owner(db, auth, _order(db, event.order_id))
    background.add_task(deliver_order_event, event.id, force=True)
    return {"event_id": event.id, "queued": True, "manual_resend": True}


@router.get("/developer-applications")
def list_developer_apps(auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(DeveloperApplication).where(
                DeveloperApplication.owner_principal_id == auth.principal_id
            )
        )
    )
    return {"items": [serialize_application(db, row) for row in rows]}


@router.post("/developer-applications", status_code=201)
def create_developer_app(
    request: DeveloperApplicationCreate,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    auth.require("developer:manage")
    item = create_developer_application(
        db,
        request.name,
        request.owner,
        [str(value) for value in request.redirect_uris],
        [str(value) for value in request.webhook_allowlist],
    )
    return serialize_application(db, item)


@router.post("/developer-applications/{application_id}/api-keys", status_code=201)
def create_key(
    application_id: str,
    request: APIKeyCreate,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    auth.require("developer:manage")
    application = db.get(DeveloperApplication, application_id)
    if application is None or application.owner_principal_id != auth.principal_id:
        raise _error(404, "DEVELOPER_APPLICATION_NOT_FOUND", "Developer application not found")
    key, secret = issue_api_key(
        db, application, name=request.name, scopes=request.scopes, expires_at=request.expires_at
    )
    db.commit()
    return {
        "id": key.id,
        "prefix": key.prefix,
        "name": key.name,
        "scopes": request.scopes,
        "secret": secret,
        "secret_display": "shown_once",
    }


@router.delete("/developer-applications/{application_id}/api-keys/{key_id}", status_code=204)
def revoke_key(
    application_id: str,
    key_id: str,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    auth.require("developer:manage")
    application = db.get(DeveloperApplication, application_id)
    key = db.get(APIKey, key_id)
    if (
        application is None
        or key is None
        or key.application_id != application.id
        or application.owner_principal_id != auth.principal_id
    ):
        raise _error(404, "API_KEY_NOT_FOUND", "API key not found")
    key.revoked_at = datetime.now(UTC)
    db.commit()
    return None


@router.get("/operator/orders")
def operator_orders(auth: AuthContext = Depends(auth_context), db: Session = Depends(get_db)):
    auth.require("services:write")
    rows = list(db.scalars(select(CommercialOrder).order_by(CommercialOrder.created_at.desc())))
    return {"items": [serialize_order(db, row, detail=True, internal=True) for row in rows]}


@router.post("/operator/services/{service_id}/versions", status_code=201)
def new_service_version(
    service_id: str,
    request: ServiceVersionCreate,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    auth.require("services:write")
    offer = db.get(ServiceOffer, service_id)
    if offer is None:
        raise _error(404, "SERVICE_NOT_FOUND", "Service offer not found")
    return serialize_service(create_service_version(db, offer, request.model_dump()), detail=True)


@router.patch("/operator/services/{service_id}/status")
def update_service_status(
    service_id: str,
    request: ServiceStatusUpdate,
    auth: AuthContext = Depends(auth_context),
    db: Session = Depends(get_db),
):
    auth.require("services:write")
    offer = db.get(ServiceOffer, service_id)
    if offer is None:
        raise _error(404, "SERVICE_NOT_FOUND", "Service offer not found")
    return serialize_service(set_service_status(db, offer, request.status), detail=True)
