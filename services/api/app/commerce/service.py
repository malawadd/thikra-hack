"""Shared commercial services used by REST, MCP, SvelteKit, and demos."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.commerce.catalog import SERVICE_DEFINITIONS
from app.commerce.models import (
    APIKey,
    BuyerAgent,
    BuyerPrincipal,
    CommercialOrder,
    Deliverable,
    DeveloperApplication,
    Dispute,
    FulfillmentJob,
    OrderEvent,
    Quote,
    ServiceOffer,
    WebhookDelivery,
    WebhookSubscription,
)
from app.commerce.pricing import StaticDevelopmentPricingAdapter, quote_breakdown
from app.commerce.schemas import AgentDeclaration, PrincipalDeclaration, QuoteCreate
from app.commerce.security import AuthContext, hash_secret
from app.commerce.state_machine import append_order_event, transition_order
from app.commerce.webhooks import (
    SUPPORTED_EVENTS,
    validate_callback_url,
    webhook_secret,
    webhook_secret_hash,
)
from app.config import settings
from app.thikra.audit import append_event, canonical_json
from app.thikra.models import PaymentRecord, RedressCase
from app.thikra.service import workspace


def _load(value: str | None, fallback: Any = None) -> Any:
    return json.loads(value) if value else fallback


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


def serialize_service(offer: ServiceOffer, *, detail: bool = False) -> dict:
    data = {
        "id": offer.id,
        "slug": offer.slug,
        "version": offer.version,
        "name": offer.name,
        "short_description": offer.short_description,
        "status": offer.status,
        "category": offer.category,
        "supported_modalities": _load(offer.supported_modalities_json, []),
        "pricing_model": offer.pricing_model,
        "base_price_minor": offer.base_price_minor,
        "minimum_price_minor": offer.minimum_price_minor,
        "maximum_price_minor": offer.maximum_price_minor,
        "currency": offer.currency,
        "estimated_delivery_seconds_min": offer.estimated_delivery_seconds_min,
        "estimated_delivery_seconds_max": offer.estimated_delivery_seconds_max,
        "maximum_retries_included": offer.maximum_retries_included,
        "verification_included": offer.verification_included,
        "human_review_available": offer.human_review_available,
        "published_at": _iso(offer.published_at),
        "machine_identifier": f"thikra.service.{offer.slug}.v{offer.version}",
    }
    if detail:
        data |= {
            "long_description": offer.long_description,
            "input_schema": _load(offer.input_schema_json, {}),
            "output_schema": _load(offer.output_schema_json, {}),
            "commercial_use_policy": offer.commercial_use_policy,
            "provider_policy": _load(offer.provider_policy_json, {}),
            "default_mandate_template": _load(offer.default_mandate_template_json, {}),
        }
    return data


def active_services(db: Session) -> list[ServiceOffer]:
    return list(
        db.scalars(
            select(ServiceOffer)
            .where(ServiceOffer.status == "ACTIVE")
            .order_by(ServiceOffer.category, ServiceOffer.name)
        )
    )


def get_service(db: Session, slug: str, *, include_inactive: bool = False) -> ServiceOffer:
    stmt = select(ServiceOffer).where(ServiceOffer.slug == slug)
    if not include_inactive:
        stmt = stmt.where(ServiceOffer.status == "ACTIVE")
    offer = db.scalar(stmt.order_by(ServiceOffer.version.desc()).limit(1))
    if offer is None:
        raise LookupError("Service offer not found")
    return offer


def create_service_version(db: Session, offer: ServiceOffer, changes: dict) -> ServiceOffer:
    latest = (
        db.scalar(select(func.max(ServiceOffer.version)).where(ServiceOffer.slug == offer.slug))
        or offer.version
    )
    values = {
        column.name: getattr(offer, column.name)
        for column in ServiceOffer.__table__.columns
        if column.name not in {"id", "created_at", "updated_at", "version", "published_at"}
    }
    mapping = {
        "input_schema": "input_schema_json",
        "output_schema": "output_schema_json",
    }
    for key, value in changes.items():
        if value is None:
            continue
        target = mapping.get(key, key)
        values[target] = canonical_json(value) if target.endswith("_json") else value
    clone = ServiceOffer(**values, version=latest + 1, status="DRAFT", published_at=None)
    db.add(clone)
    db.flush()
    append_event(
        db,
        workspace_id=workspace(db).id,
        run_id=None,
        event_type="service.version_created",
        actor_type="OPERATOR",
        actor_id="commerce-operator",
        payload={"slug": clone.slug, "version": clone.version},
        related_object_ids=[offer.id, clone.id],
    )
    db.commit()
    return clone


def set_service_status(db: Session, offer: ServiceOffer, status: str) -> ServiceOffer:
    if status == "ACTIVE":
        for previous in db.scalars(
            select(ServiceOffer).where(
                ServiceOffer.slug == offer.slug,
                ServiceOffer.status == "ACTIVE",
                ServiceOffer.id != offer.id,
            )
        ):
            previous.status = "RETIRED"
    offer.status = status
    if status == "ACTIVE" and offer.published_at is None:
        offer.published_at = datetime.now(UTC)
    event = {
        "ACTIVE": "service.published",
        "PAUSED": "service.paused",
        "RETIRED": "service.retired",
    }[status]
    append_event(
        db,
        workspace_id=workspace(db).id,
        run_id=None,
        event_type=event,
        actor_type="OPERATOR",
        actor_id="commerce-operator",
        payload={"slug": offer.slug, "version": offer.version},
        related_object_ids=[offer.id],
    )
    db.commit()
    return offer


def _principal(db: Session, declaration: PrincipalDeclaration) -> BuyerPrincipal:
    item = None
    if declaration.external_reference:
        item = db.scalar(
            select(BuyerPrincipal).where(
                BuyerPrincipal.external_reference == declaration.external_reference
            )
        )
    if item is None and declaration.email:
        item = db.scalar(
            select(BuyerPrincipal).where(BuyerPrincipal.email == str(declaration.email))
        )
    if item is None:
        item = BuyerPrincipal(
            type=declaration.type,
            display_name=declaration.display_name,
            email=str(declaration.email) if declaration.email else None,
            organisation=declaration.organisation,
            external_reference=declaration.external_reference,
            verification_status="UNVERIFIED",
        )
        db.add(item)
        db.flush()
    return item


def _agent(
    db: Session,
    auth: AuthContext,
    principal: BuyerPrincipal,
    declaration: AgentDeclaration,
) -> BuyerAgent:
    stmt = select(BuyerAgent).where(BuyerAgent.application_id == auth.application_id)
    if declaration.external_agent_id:
        stmt = stmt.where(BuyerAgent.external_agent_id == declaration.external_agent_id)
    else:
        stmt = stmt.where(BuyerAgent.name == declaration.name)
    item = db.scalar(stmt.limit(1))
    now = datetime.now(UTC)
    if item is None:
        item = BuyerAgent(
            principal_id=principal.id,
            application_id=auth.application_id,
            name=declaration.name,
            description=declaration.description,
            developer_name=declaration.developer_name,
            operator_name=declaration.operator_name,
            framework=declaration.framework,
            model_name=declaration.model_name,
            model_version=declaration.model_version,
            external_agent_id=declaration.external_agent_id,
            agent_card_url=str(declaration.agent_card_url) if declaration.agent_card_url else None,
            authentication_method="API_KEY",
            # Agent metadata is still self-declared. The application identity is
            # authenticated separately by `application_id` + `API_KEY`.
            trust_status="DECLARED",
            first_seen_at=now,
            last_seen_at=now,
            metadata_json=canonical_json(declaration.metadata),
        )
        db.add(item)
        db.flush()
        append_event(
            db,
            workspace_id=workspace(db).id,
            run_id=None,
            event_type="agent.authenticated",
            actor_type="SYSTEM",
            actor_id="agent-gateway",
            payload={"agent_id": item.id, "metadata_status": "DECLARED"},
            related_object_ids=[item.id, principal.id, auth.application_id],
        )
    else:
        item.last_seen_at = now
    return item


async def create_quote(db: Session, auth: AuthContext, request: QuoteCreate) -> Quote:
    auth.require("quotes:create")
    offer = get_service(db, request.service)
    errors = sorted(
        Draft202012Validator(_load(offer.input_schema_json, {})).iter_errors(request.input),
        key=lambda error: list(error.path),
    )
    if errors:
        path = ".".join(str(part) for part in errors[0].path) or "$"
        raise ValueError(f"Input schema validation failed at {path}: {errors[0].message}")
    required = set(request.input.get("requiredProviders", []))
    forbidden = set(request.input.get("forbiddenProviders", []))
    if required & forbidden:
        raise ValueError("A provider cannot be both required and forbidden")
    principal = _principal(db, request.buyer_principal)
    agent = _agent(db, auth, principal, request.buyer_agent)
    adapter = StaticDevelopmentPricingAdapter()
    estimates = []
    for modality in _load(offer.supported_modalities_json, []):
        provider_id = sorted(required)[0] if required else "catalog-default"
        estimates.append(await adapter.estimate(provider_id, "default", modality, request.input))
    provider_estimate = max(offer.base_price_minor, sum(item.amount_minor for item in estimates))
    breakdown = quote_breakdown(
        provider_estimate,
        min(
            int(request.input.get("maximumRetries", offer.maximum_retries_included)),
            offer.maximum_retries_included,
        ),
    )
    if breakdown["total_minor"] > offer.maximum_price_minor:
        raise ValueError("Requested scope exceeds this service version's maximum price")
    if request.maximum_budget:
        if request.maximum_budget.currency != offer.currency:
            raise ValueError("Maximum budget currency must match the service currency")
        if breakdown["total_minor"] > request.maximum_budget.amount_minor:
            raise ValueError("Deterministic quote exceeds the buyer's maximum budget")
    if request.callback_url:
        validate_callback_url(str(request.callback_url))
    now = datetime.now(UTC)
    quote = Quote(
        service_offer_id=offer.id,
        service_version=offer.version,
        buyer_agent_id=agent.id,
        buyer_principal_id=principal.id,
        currency=offer.currency,
        subtotal_minor=provider_estimate,
        verification_fee_minor=breakdown["verification_fee_minor"],
        storage_fee_minor=breakdown["storage_fee_minor"],
        retry_reserve_minor=breakdown["retry_reserve_minor"],
        platform_fee_minor=breakdown["platform_fee_minor"],
        tax_minor=breakdown["tax_minor"],
        total_minor=breakdown["total_minor"],
        provider_cost_estimate_minor=provider_estimate,
        pricing_breakdown_json=canonical_json(
            breakdown
            | {
                "estimated": True,
                "confidence": min((item.confidence for item in estimates), default="LOW"),
                "sources": [item.source for item in estimates],
            }
        ),
        input_payload_json=canonical_json(request.input),
        mandate_preview_json=canonical_json(
            {
                "service": offer.slug,
                "service_version": offer.version,
                "objective": request.input.get("brief"),
                "allowed_modalities": _load(offer.supported_modalities_json, []),
                "required_providers": sorted(required),
                "forbidden_providers": sorted(forbidden),
                "maximum_retries": min(
                    int(request.input.get("maximumRetries", offer.maximum_retries_included)),
                    offer.maximum_retries_included,
                ),
                "verification_required": True,
                "commercial_use_policy": offer.commercial_use_policy,
            }
        ),
        expires_at=now + timedelta(seconds=settings.thikra_quote_ttl_seconds),
        status="ACTIVE",
    )
    db.add(quote)
    db.flush()
    append_event(
        db,
        workspace_id=workspace(db).id,
        run_id=None,
        event_type="quote.created",
        actor_type="AGENT",
        actor_id=agent.id,
        payload={
            "quote_id": quote.id,
            "service": offer.slug,
            "total_minor": quote.total_minor,
            "currency": quote.currency,
        },
        related_object_ids=[quote.id, offer.id, agent.id, principal.id],
    )
    db.commit()
    return quote


def refresh_quote_status(db: Session, quote: Quote) -> None:
    if quote.status == "ACTIVE" and _aware(quote.expires_at) <= datetime.now(UTC):
        quote.status = "EXPIRED"
        append_event(
            db,
            workspace_id=workspace(db).id,
            run_id=None,
            event_type="quote.expired",
            actor_type="SYSTEM",
            actor_id="quote-engine",
            payload={"quote_id": quote.id},
            related_object_ids=[quote.id],
        )
        db.commit()


def serialize_quote(db: Session, quote: Quote) -> dict:
    refresh_quote_status(db, quote)
    offer = db.get(ServiceOffer, quote.service_offer_id)
    return {
        "id": quote.id,
        "service": offer.slug,
        "service_offer_id": offer.id,
        "service_version": quote.service_version,
        "status": quote.status,
        "currency": quote.currency,
        "subtotal_minor": quote.subtotal_minor,
        "verification_fee_minor": quote.verification_fee_minor,
        "storage_fee_minor": quote.storage_fee_minor,
        "retry_reserve_minor": quote.retry_reserve_minor,
        "platform_fee_minor": quote.platform_fee_minor,
        "tax_minor": quote.tax_minor,
        "total_minor": quote.total_minor,
        "pricing_breakdown": _load(quote.pricing_breakdown_json, {}),
        "input": _load(quote.input_payload_json, {}),
        "mandate_preview": _load(quote.mandate_preview_json, {}),
        "expires_at": _iso(quote.expires_at),
        "created_at": _iso(quote.created_at),
        "accepted_at": _iso(quote.accepted_at),
        "required_next_action": "accept_quote" if quote.status == "ACTIVE" else None,
    }


def owns_quote(db: Session, auth: AuthContext, quote: Quote) -> bool:
    agent = db.get(BuyerAgent, quote.buyer_agent_id)
    return bool(agent and agent.application_id == auth.application_id)


def accept_quote(db: Session, auth: AuthContext, quote: Quote) -> Quote:
    auth.require("quotes:create")
    if not owns_quote(db, auth, quote):
        raise PermissionError("Quote belongs to another developer application")
    refresh_quote_status(db, quote)
    if quote.status == "ACCEPTED":
        return quote
    if quote.status != "ACTIVE":
        raise ValueError("Only an active, unexpired quote can be accepted")
    quote.status = "ACCEPTED"
    quote.accepted_at = datetime.now(UTC)
    append_event(
        db,
        workspace_id=workspace(db).id,
        run_id=None,
        event_type="quote.accepted",
        actor_type="AGENT",
        actor_id=quote.buyer_agent_id,
        payload={"quote_id": quote.id, "total_minor": quote.total_minor},
        related_object_ids=[quote.id],
    )
    db.commit()
    return quote


def create_order(
    db: Session,
    auth: AuthContext,
    quote: Quote,
    *,
    callback_url: str | None,
    external_reference: str | None,
) -> CommercialOrder:
    auth.require("orders:create")
    if not owns_quote(db, auth, quote):
        raise PermissionError("Quote belongs to another developer application")
    refresh_quote_status(db, quote)
    if quote.status != "ACCEPTED":
        raise ValueError("Quote must be accepted before order creation")
    existing = db.scalar(select(CommercialOrder).where(CommercialOrder.quote_id == quote.id))
    if existing:
        return existing
    if callback_url:
        validate_callback_url(callback_url)
    offer = db.get(ServiceOffer, quote.service_offer_id)
    number = f"THK-{datetime.now(UTC):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
    order = CommercialOrder(
        public_order_number=number,
        quote_id=quote.id,
        service_offer_id=offer.id,
        service_version=quote.service_version,
        buyer_agent_id=quote.buyer_agent_id,
        buyer_principal_id=quote.buyer_principal_id,
        status="QUOTED",
        currency=quote.currency,
        quoted_total_minor=quote.total_minor,
        input_payload_json=quote.input_payload_json,
        callback_url=callback_url,
        external_reference=external_reference,
    )
    db.add(order)
    db.flush()
    append_order_event(
        db,
        order,
        "order.created",
        actor_type="AGENT",
        actor_id=order.buyer_agent_id,
        payload={"quote_id": quote.id, "service": offer.slug, "status": "QUOTED"},
    )
    db.commit()
    return order


def owns_order(db: Session, auth: AuthContext, order: CommercialOrder) -> bool:
    agent = db.get(BuyerAgent, order.buyer_agent_id)
    return bool(agent and agent.application_id == auth.application_id)


def require_order_owner(db: Session, auth: AuthContext, order: CommercialOrder) -> None:
    if not owns_order(db, auth, order):
        raise PermissionError("Order belongs to another developer application")


def serialize_order(
    db: Session, order: CommercialOrder, *, detail: bool = False, internal: bool = False
) -> dict:
    offer = db.get(ServiceOffer, order.service_offer_id)
    payment = db.scalar(select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id))
    job = db.scalar(
        select(FulfillmentJob)
        .where(FulfillmentJob.order_id == order.id)
        .order_by(FulfillmentJob.attempt_number.desc())
        .limit(1)
    )
    deliverables = list(db.scalars(select(Deliverable).where(Deliverable.order_id == order.id)))
    events = list(
        db.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == order.id)
            .order_by(OrderEvent.created_at, OrderEvent.id)
        )
    )
    data = {
        "id": order.id,
        "public_order_number": order.public_order_number,
        "service": offer.slug,
        "service_version": order.service_version,
        "status": order.status,
        "currency": order.currency,
        "quoted_total_minor": order.quoted_total_minor,
        "authorized_total_minor": order.authorized_total_minor,
        "paid_total_minor": order.paid_total_minor,
        "payment_state": payment.payment_state if payment else "NOT_STARTED",
        "payment_authorization_state": payment.authorization_state if payment else "NOT_STARTED",
        "fulfillment_state": job.status if job else "NOT_STARTED",
        "progress": _order_progress(order.status),
        "deliverable_count": len(deliverables),
        "created_at": _iso(order.created_at),
        "updated_at": _iso(order.updated_at),
        "paid_at": _iso(order.paid_at),
        "delivered_at": _iso(order.delivered_at),
        "latest_event": events[-1].event_type if events else None,
        "user_action_required": _next_action(order.status),
    }
    if detail:
        data |= {
            "quote_id": order.quote_id,
            "buyer_agent_id": order.buyer_agent_id,
            "buyer_principal_id": order.buyer_principal_id,
            "input": _load(order.input_payload_json, {}),
            "callback_url": order.callback_url,
            "external_reference": order.external_reference,
            "payment_id": payment.id if payment else None,
            "fulfillment_job_id": job.id if job else None,
            "generation_run_id": job.generation_run_id if job else None,
            "retry_attempt": job.attempt_number if job else 0,
            "events": [serialize_order_event(event) for event in events],
        }
    if internal:
        quote = db.get(Quote, order.quote_id)
        data["economics"] = {
            "quoted_revenue_minor": order.quoted_total_minor,
            "collected_revenue_minor": order.paid_total_minor,
            "estimated_provider_cost_minor": quote.provider_cost_estimate_minor,
            "verification_cost_minor": quote.verification_fee_minor,
            "storage_cost_estimate_minor": quote.storage_fee_minor,
            "retry_reserve_minor": quote.retry_reserve_minor,
            "estimated_gross_margin_minor": max(
                0,
                order.quoted_total_minor
                - quote.provider_cost_estimate_minor
                - quote.verification_fee_minor
                - quote.storage_fee_minor,
            ),
            "estimate_notice": "Provider, storage, verification, and margin values are operational estimates, not final accounting.",
        }
    return data


def _order_progress(status: str) -> int:
    ordered = [
        "QUOTED",
        "PAYMENT_AUTHORIZATION_PENDING",
        "PAYMENT_AUTHORIZED",
        "PAYMENT_PENDING",
        "PAID",
        "ACCEPTED",
        "FULFILLMENT_PENDING",
        "FULFILLING",
        "VERIFYING",
        "REVIEW_REQUIRED",
        "READY",
        "DELIVERED",
        "COMPLETED",
    ]
    return round(
        max(0, ordered.index(status) if status in ordered else 0) / (len(ordered) - 1) * 100
    )


def _next_action(status: str) -> str | None:
    return {
        "QUOTED": "create_payment_authorization",
        "PAYMENT_AUTHORIZATION_PENDING": "approve_payment",
        "PAYMENT_AUTHORIZED": "complete_payment",
        "PAID": "start_fulfillment",
        "REVIEW_REQUIRED": "retry_or_review",
        "READY": "retrieve_delivery",
    }.get(status)


def serialize_order_event(event: OrderEvent) -> dict:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "payload": _load(event.payload_json, {}),
        "previous_event_hash": event.previous_event_hash,
        "event_hash": event.event_hash,
        "created_at": _iso(event.created_at),
    }


def create_developer_application(
    db: Session,
    name: str,
    owner: PrincipalDeclaration,
    redirect_uris: list[str],
    allowlist: list[str],
) -> DeveloperApplication:
    principal = _principal(db, owner)
    application = DeveloperApplication(
        name=name,
        owner_principal_id=principal.id,
        status="ACTIVE",
        redirect_uris_json=canonical_json(redirect_uris),
        webhook_allowlist_json=canonical_json(allowlist),
    )
    db.add(application)
    db.flush()
    append_event(
        db,
        workspace_id=workspace(db).id,
        run_id=None,
        event_type="developer_app.created",
        actor_type="USER",
        actor_id=principal.id,
        payload={"application_id": application.id, "name": name},
        related_object_ids=[application.id, principal.id],
    )
    db.commit()
    return application


def serialize_application(db: Session, application: DeveloperApplication) -> dict:
    keys = list(db.scalars(select(APIKey).where(APIKey.application_id == application.id)))
    subscriptions = list(
        db.scalars(
            select(WebhookSubscription).where(WebhookSubscription.application_id == application.id)
        )
    )
    return {
        "id": application.id,
        "name": application.name,
        "owner_principal_id": application.owner_principal_id,
        "status": application.status,
        "redirect_uris": _load(application.redirect_uris_json, []),
        "webhook_allowlist": _load(application.webhook_allowlist_json, []),
        "api_keys": [
            {
                "id": key.id,
                "name": key.name,
                "prefix": key.prefix,
                "scopes": _load(key.scopes_json, []),
                "last_used_at": _iso(key.last_used_at),
                "expires_at": _iso(key.expires_at),
                "revoked_at": _iso(key.revoked_at),
            }
            for key in keys
        ],
        "webhook_subscriptions": len(subscriptions),
        "created_at": _iso(application.created_at),
    }


def create_webhook_subscription(
    db: Session, auth: AuthContext, callback_url: str, events: list[str]
) -> tuple[WebhookSubscription, str]:
    auth.require("webhooks:manage")
    validate_callback_url(callback_url)
    unknown = set(events) - SUPPORTED_EVENTS
    if unknown:
        raise ValueError(f"Unsupported webhook events: {', '.join(sorted(unknown))}")
    subscription = WebhookSubscription(
        application_id=auth.application_id,
        callback_url=callback_url,
        secret_hash="pending",
        events_json=canonical_json(sorted(set(events))),
        status="ACTIVE",
    )
    db.add(subscription)
    db.flush()
    secret = webhook_secret(subscription.id)
    subscription.secret_hash = webhook_secret_hash(secret)
    append_event(
        db,
        workspace_id=workspace(db).id,
        run_id=None,
        event_type="webhook.subscription_created",
        actor_type="DEVELOPER",
        actor_id=auth.application_id,
        payload={"subscription_id": subscription.id, "events": sorted(set(events))},
        related_object_ids=[subscription.id, auth.application_id],
    )
    db.commit()
    return subscription, secret


def serialize_webhook_subscription(db: Session, item: WebhookSubscription) -> dict:
    deliveries = list(
        db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.subscription_id == item.id)
            .order_by(WebhookDelivery.created_at.desc())
        )
    )
    return {
        "id": item.id,
        "callback_url": item.callback_url,
        "events": _load(item.events_json, []),
        "status": item.status,
        "created_at": _iso(item.created_at),
        "deliveries": [
            {
                "id": delivery.id,
                "event_id": delivery.event_id,
                "attempt": delivery.attempt,
                "status_code": delivery.status_code,
                "next_retry_at": _iso(delivery.next_retry_at),
                "delivered_at": _iso(delivery.delivered_at),
                "response_body_excerpt": delivery.response_body_excerpt,
            }
            for delivery in deliveries[:20]
        ],
    }


def create_dispute(
    db: Session,
    auth: AuthContext,
    order: CommercialOrder,
    reason_code: str,
    description: str,
    deliverable_id: str | None,
) -> Dispute:
    auth.require("disputes:create")
    require_order_owner(db, auth, order)
    existing = db.scalar(
        select(Dispute).where(Dispute.order_id == order.id, Dispute.status == "OPEN")
    )
    if existing:
        return existing
    if order.status not in {"DELIVERED", "COMPLETED", "REDRESS_OPEN", "DISPUTED"}:
        raise ValueError("A dispute can be opened only after delivery or redress")
    payment = db.scalar(select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id))
    job = db.scalar(select(FulfillmentJob).where(FulfillmentJob.order_id == order.id))
    case = RedressCase(
        workspace_id=workspace(db).id,
        mandate_id=job.mandate_id,
        run_id=job.generation_run_id,
        payment_id=payment.id if payment else None,
        reason=description,
        severity="MEDIUM",
        evidence_snapshot_json=canonical_json({"order_id": order.id, "reason_code": reason_code}),
        recommended_next_action="Review evidence and request a refund only through documented payment functionality.",
        owner="Trust operations",
        status="OPEN",
    )
    db.add(case)
    db.flush()
    dispute = Dispute(
        order_id=order.id,
        payment_record_id=payment.id if payment else None,
        deliverable_id=deliverable_id,
        redress_case_id=case.id,
        opened_by=order.buyer_agent_id,
        reason_code=reason_code,
        description=description,
        status="OPEN",
    )
    db.add(dispute)
    db.flush()
    if order.status in {"DELIVERED", "COMPLETED"}:
        transition_order(
            db,
            order,
            "DISPUTED",
            actor_type="AGENT",
            actor_id=order.buyer_agent_id,
            payload={"dispute_id": dispute.id},
        )
    append_event(
        db,
        workspace_id=workspace(db).id,
        run_id=job.generation_run_id,
        event_type="dispute.opened",
        actor_type="AGENT",
        actor_id=order.buyer_agent_id,
        payload={"order_id": order.id, "dispute_id": dispute.id, "reason_code": reason_code},
        related_object_ids=[dispute.id, case.id, order.id],
    )
    db.commit()
    return dispute


def serialize_dispute(dispute: Dispute) -> dict:
    return {
        "id": dispute.id,
        "order_id": dispute.order_id,
        "payment_record_id": dispute.payment_record_id,
        "deliverable_id": dispute.deliverable_id,
        "redress_case_id": dispute.redress_case_id,
        "opened_by": dispute.opened_by,
        "reason_code": dispute.reason_code,
        "description": dispute.description,
        "status": dispute.status,
        "resolution": dispute.resolution,
        "refund_reference": dispute.refund_reference,
        "created_at": _iso(dispute.created_at),
        "resolved_at": _iso(dispute.resolved_at),
    }


def seed_commerce(db: Session) -> None:
    ws = workspace(db)
    if not db.scalar(select(ServiceOffer).limit(1)):
        now = datetime.now(UTC)
        for definition in SERVICE_DEFINITIONS:
            offer = ServiceOffer(
                slug=definition["slug"],
                version=1,
                name=definition["name"],
                short_description=definition["short_description"],
                long_description=definition["long_description"],
                status="ACTIVE",
                category=definition["category"],
                supported_modalities_json=canonical_json(definition["modalities"]),
                input_schema_json=canonical_json(definition["input_schema"]),
                output_schema_json=canonical_json(definition["output_schema"]),
                pricing_model="DETERMINISTIC_SCOPE",
                base_price_minor=definition["base_price_minor"],
                currency=settings.thikra_default_currency,
                minimum_price_minor=definition["minimum_price_minor"],
                maximum_price_minor=definition["maximum_price_minor"],
                estimated_delivery_seconds_min=definition["delivery_min"],
                estimated_delivery_seconds_max=definition["delivery_max"],
                maximum_retries_included=definition["maximum_retries"],
                verification_included=True,
                human_review_available=True,
                commercial_use_policy="Commercial use is permitted only when the selected provider plan and supplied materials support it; provider terms remain recorded evidence.",
                provider_policy_json=canonical_json(
                    {"selection": "per-run catalog routing", "forbidden_provider_enforced": True}
                ),
                default_mandate_template_json=canonical_json(
                    {
                        "verification": definition["verification"],
                        "human_approval_on_uncertainty": True,
                    }
                ),
                published_at=now,
            )
            db.add(offer)
            db.flush()
            append_event(
                db,
                workspace_id=ws.id,
                run_id=None,
                event_type="service.published",
                actor_type="SYSTEM",
                actor_id="commerce-seed",
                payload={"slug": offer.slug, "version": 1},
                related_object_ids=[offer.id],
            )
    if not db.scalar(select(DeveloperApplication).limit(1)):
        owner = BuyerPrincipal(
            type="HUMAN",
            display_name="Demo buyer principal",
            email="buyer.agent@thikra.demo",
            organisation="Thikra demo",
            external_reference="demo-buyer-principal",
            verification_status="UNVERIFIED",
        )
        db.add(owner)
        db.flush()
        application = DeveloperApplication(
            name="Thikra external buyer-agent demo",
            owner_principal_id=owner.id,
            status="ACTIVE",
            redirect_uris_json=canonical_json([settings.public_web_url]),
            webhook_allowlist_json="[]",
        )
        db.add(application)
        db.flush()
        demo_secret = settings.thikra_demo_api_key
        db.add(
            APIKey(
                application_id=application.id,
                prefix=demo_secret[:20],
                hashed_secret=hash_secret(demo_secret),
                name="Local demo key",
                scopes_json=canonical_json(
                    [
                        "services:read",
                        "quotes:create",
                        "orders:create",
                        "orders:read",
                        "payments:create",
                        "deliverables:read",
                        "disputes:create",
                        "webhooks:manage",
                        "developer:manage",
                        "services:write",
                    ]
                ),
            )
        )
        append_event(
            db,
            workspace_id=ws.id,
            run_id=None,
            event_type="api_key.issued",
            actor_type="SYSTEM",
            actor_id="commerce-seed",
            payload={
                "application_id": application.id,
                "prefix": demo_secret[:20],
                "demo_only": True,
            },
            related_object_ids=[application.id],
        )
    db.commit()
