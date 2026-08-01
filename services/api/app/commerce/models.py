"""Persistent commercial objects for agent-accessible creative services."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.thikra.database import Base
from app.thikra.models import Record


class ServiceOffer(Record, Base):
    __tablename__ = "service_offers"
    slug: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(240))
    short_description: Mapped[str] = mapped_column(String(500))
    long_description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    supported_modalities_json: Mapped[str] = mapped_column(Text)
    input_schema_json: Mapped[str] = mapped_column(Text)
    output_schema_json: Mapped[str] = mapped_column(Text)
    pricing_model: Mapped[str] = mapped_column(String(80))
    base_price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    minimum_price_minor: Mapped[int] = mapped_column(Integer)
    maximum_price_minor: Mapped[int] = mapped_column(Integer)
    estimated_delivery_seconds_min: Mapped[int] = mapped_column(Integer)
    estimated_delivery_seconds_max: Mapped[int] = mapped_column(Integer)
    maximum_retries_included: Mapped[int] = mapped_column(Integer)
    verification_included: Mapped[bool] = mapped_column(Boolean, default=True)
    human_review_available: Mapped[bool] = mapped_column(Boolean, default=True)
    commercial_use_policy: Mapped[str] = mapped_column(Text)
    provider_policy_json: Mapped[str] = mapped_column(Text)
    default_mandate_template_json: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("slug", "version", name="uq_service_slug_version"),)


class BuyerPrincipal(Record, Base):
    __tablename__ = "buyer_principals"
    type: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    display_name: Mapped[str] = mapped_column(String(240))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    organisation: Mapped[str | None] = mapped_column(String(240))
    external_reference: Mapped[str | None] = mapped_column(String(240), index=True)
    verification_status: Mapped[str] = mapped_column(String(30), default="UNVERIFIED")


class DeveloperApplication(Record, Base):
    __tablename__ = "developer_applications"
    name: Mapped[str] = mapped_column(String(240))
    owner_principal_id: Mapped[str] = mapped_column(ForeignKey("buyer_principals.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    redirect_uris_json: Mapped[str] = mapped_column(Text, default="[]")
    webhook_allowlist_json: Mapped[str] = mapped_column(Text, default="[]")


class BuyerAgent(Record, Base):
    __tablename__ = "buyer_agents"
    principal_id: Mapped[str] = mapped_column(ForeignKey("buyer_principals.id"), index=True)
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("developer_applications.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    developer_name: Mapped[str | None] = mapped_column(String(240))
    operator_name: Mapped[str | None] = mapped_column(String(240))
    framework: Mapped[str | None] = mapped_column(String(120))
    model_name: Mapped[str | None] = mapped_column(String(180))
    model_version: Mapped[str | None] = mapped_column(String(80))
    public_key: Mapped[str | None] = mapped_column(Text)
    external_agent_id: Mapped[str | None] = mapped_column(String(240), index=True)
    agent_card_url: Mapped[str | None] = mapped_column(String(2048))
    authentication_method: Mapped[str] = mapped_column(String(40), default="DECLARED")
    trust_status: Mapped[str] = mapped_column(String(30), default="UNVERIFIED", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class APIKey(Record, Base):
    __tablename__ = "api_keys"
    application_id: Mapped[str] = mapped_column(ForeignKey("developer_applications.id"), index=True)
    prefix: Mapped[str] = mapped_column(String(32), index=True)
    hashed_secret: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    scopes_json: Mapped[str] = mapped_column(Text)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Quote(Record, Base):
    __tablename__ = "commerce_quotes"
    service_offer_id: Mapped[str] = mapped_column(ForeignKey("service_offers.id"), index=True)
    service_version: Mapped[int] = mapped_column(Integer)
    buyer_agent_id: Mapped[str] = mapped_column(ForeignKey("buyer_agents.id"), index=True)
    buyer_principal_id: Mapped[str] = mapped_column(ForeignKey("buyer_principals.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    subtotal_minor: Mapped[int] = mapped_column(Integer)
    verification_fee_minor: Mapped[int] = mapped_column(Integer)
    storage_fee_minor: Mapped[int] = mapped_column(Integer)
    retry_reserve_minor: Mapped[int] = mapped_column(Integer)
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    tax_minor: Mapped[int] = mapped_column(Integer)
    total_minor: Mapped[int] = mapped_column(Integer)
    provider_cost_estimate_minor: Mapped[int] = mapped_column(Integer)
    pricing_breakdown_json: Mapped[str] = mapped_column(Text)
    input_payload_json: Mapped[str] = mapped_column(Text)
    mandate_preview_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommercialOrder(Record, Base):
    __tablename__ = "commercial_orders"
    public_order_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("commerce_quotes.id"), unique=True)
    service_offer_id: Mapped[str] = mapped_column(ForeignKey("service_offers.id"), index=True)
    service_version: Mapped[int] = mapped_column(Integer)
    buyer_agent_id: Mapped[str] = mapped_column(ForeignKey("buyer_agents.id"), index=True)
    buyer_principal_id: Mapped[str] = mapped_column(ForeignKey("buyer_principals.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="QUOTED", index=True)
    currency: Mapped[str] = mapped_column(String(3))
    quoted_total_minor: Mapped[int] = mapped_column(Integer)
    authorized_total_minor: Mapped[int] = mapped_column(Integer, default=0)
    paid_total_minor: Mapped[int] = mapped_column(Integer, default=0)
    input_payload_json: Mapped[str] = mapped_column(Text)
    callback_url: Mapped[str | None] = mapped_column(String(2048))
    external_reference: Mapped[str | None] = mapped_column(String(240), index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderEvent(Record, Base):
    __tablename__ = "order_events"
    order_id: Mapped[str] = mapped_column(ForeignKey("commercial_orders.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor_type: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(180))
    payload_json: Mapped[str] = mapped_column(Text)
    previous_event_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), unique=True)


class IdempotencyRecord(Record, Base):
    __tablename__ = "idempotency_records"
    application_id: Mapped[str] = mapped_column(ForeignKey("developer_applications.id"), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(180))
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(36))
    response_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("application_id", "operation", "key", name="uq_idempotency_scope"),
    )


class FulfillmentJob(Record, Base):
    __tablename__ = "fulfillment_jobs"
    order_id: Mapped[str] = mapped_column(ForeignKey("commercial_orders.id"), index=True)
    mandate_id: Mapped[str | None] = mapped_column(ForeignKey("mandates.id"), index=True)
    generation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_runs.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(40), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    failure_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("order_id", "attempt_number", name="uq_job_attempt"),)


class Deliverable(Record, Base):
    __tablename__ = "deliverables"
    order_id: Mapped[str] = mapped_column(ForeignKey("commercial_orders.id"), index=True)
    fulfillment_job_id: Mapped[str] = mapped_column(ForeignKey("fulfillment_jobs.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(40))
    content_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    b2_object_key: Mapped[str] = mapped_column(String(700))
    verification_status: Mapped[str] = mapped_column(String(40), index=True)


class DeliveryReceipt(Record, Base):
    __tablename__ = "delivery_receipts"
    order_id: Mapped[str] = mapped_column(ForeignKey("commercial_orders.id"), unique=True)
    payment_record_id: Mapped[str] = mapped_column(ForeignKey("payment_records.id"))
    fulfillment_job_id: Mapped[str] = mapped_column(ForeignKey("fulfillment_jobs.id"))
    manifest_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    verification_report_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    receipt_payload_json: Mapped[str] = mapped_column(Text)
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    signing_key_id: Mapped[str] = mapped_column(String(100))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WebhookSubscription(Record, Base):
    __tablename__ = "webhook_subscriptions"
    application_id: Mapped[str] = mapped_column(ForeignKey("developer_applications.id"), index=True)
    callback_url: Mapped[str] = mapped_column(String(2048))
    secret_hash: Mapped[str] = mapped_column(String(64))
    events_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)


class WebhookDelivery(Record, Base):
    __tablename__ = "webhook_deliveries"
    subscription_id: Mapped[str] = mapped_column(ForeignKey("webhook_subscriptions.id"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("order_events.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_body_excerpt: Mapped[str | None] = mapped_column(String(1000))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("subscription_id", "event_id", "attempt", name="uq_webhook_attempt"),
    )


class Dispute(Record, Base):
    __tablename__ = "commerce_disputes"
    order_id: Mapped[str] = mapped_column(ForeignKey("commercial_orders.id"), index=True)
    payment_record_id: Mapped[str | None] = mapped_column(ForeignKey("payment_records.id"))
    deliverable_id: Mapped[str | None] = mapped_column(ForeignKey("deliverables.id"))
    redress_case_id: Mapped[str | None] = mapped_column(ForeignKey("redress_cases.id"))
    opened_by: Mapped[str] = mapped_column(String(180))
    reason_code: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="OPEN", index=True)
    resolution: Mapped[str | None] = mapped_column(Text)
    refund_reference: Mapped[str | None] = mapped_column(String(180))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_open_dispute_per_order",
            "order_id",
            "status",
            unique=True,
        ),
    )
