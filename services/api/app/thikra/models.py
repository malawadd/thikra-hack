"""Normalized SQLAlchemy models for the Thikra accountability record."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.thikra.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Workspace(Record, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(180))
    environment: Mapped[str] = mapped_column(String(20), default="DEMO")


class User(Record, Base):
    __tablename__ = "users"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(180))


class CreativeBrief(Record, Base):
    __tablename__ = "creative_briefs"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    campaign_name: Mapped[str] = mapped_column(String(240), index=True)
    objective: Mapped[str] = mapped_column(Text)
    source_json: Mapped[str] = mapped_column(Text)


class Mandate(Record, Base):
    __tablename__ = "mandates"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    brief_id: Mapped[str] = mapped_column(ForeignKey("creative_briefs.id"), index=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="PROPOSED", index=True)


class MandateVersion(Record, Base):
    __tablename__ = "mandate_versions"
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    schema_json: Mapped[str] = mapped_column(Text)
    edit_summary: Mapped[str] = mapped_column(Text, default="Initial compilation")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_mandate_version_unique", "mandate_id", "version", unique=True),)


class ProviderQuote(Record, Base):
    __tablename__ = "provider_quotes"
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), index=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str] = mapped_column(String(180))
    modality: Mapped[str] = mapped_column(String(30), index=True)
    estimated_cost_minor: Mapped[int] = mapped_column(Integer)
    estimated_latency_sec: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text)


class ProviderDecision(Record, Base):
    __tablename__ = "provider_decisions"
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    selection_json: Mapped[str] = mapped_column(Text)
    candidate_scores_json: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)


class PaymentRecord(Record, Base):
    __tablename__ = "payment_records"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    mandate_id: Mapped[str | None] = mapped_column(ForeignKey("mandates.id"), index=True)
    commercial_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("commercial_orders.id"), unique=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    gateway: Mapped[str] = mapped_column(String(30))
    environment: Mapped[str] = mapped_column(String(20))
    external_session_id: Mapped[str | None] = mapped_column(String(180), unique=True)
    external_order_id: Mapped[str | None] = mapped_column(String(180))
    merchant: Mapped[str] = mapped_column(String(180))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    maximum_amount_minor: Mapped[int] = mapped_column(Integer)
    invoked_amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    authorization_state: Mapped[str] = mapped_column(String(40), index=True)
    payment_state: Mapped[str] = mapped_column(String(40), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redress_state: Mapped[str] = mapped_column(String(40), default="NONE")
    direction: Mapped[str] = mapped_column(String(40), default="PROVIDER_PROCUREMENT")
    paid_amount_minor: Mapped[int] = mapped_column(Integer, default=0)


class PaymentEvent(Record, Base):
    __tablename__ = "payment_events"
    payment_id: Mapped[str] = mapped_column(ForeignKey("payment_records.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    sanitized_payload_json: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)


class GenerationRun(Record, Base):
    __tablename__ = "generation_runs"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    brief_id: Mapped[str] = mapped_column(ForeignKey("creative_briefs.id"), index=True)
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), index=True)
    mandate_version: Mapped[int] = mapped_column(Integer)
    payment_record_id: Mapped[str | None] = mapped_column(ForeignKey("payment_records.id"))
    campaign_name: Mapped[str] = mapped_column(String(240), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    current_stage: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    budget_cap_minor: Mapped[int] = mapped_column(Integer)
    authorized_minor: Mapped[int] = mapped_column(Integer)
    spent_minor: Mapped[int] = mapped_column(Integer, default=0)
    retry_reserved_minor: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    maximum_retries: Mapped[int] = mapped_column(Integer, default=0)
    provider_selection_json: Mapped[str] = mapped_column(Text)
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    human_escalation: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)


class Scene(Record, Base):
    __tablename__ = "scenes"
    run_id: Mapped[str] = mapped_column(ForeignKey("generation_runs.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    narration: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str] = mapped_column(String(180))
    cost_minor: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    verification_state: Mapped[str] = mapped_column(String(40), default="PENDING")


class Asset(Record, Base):
    __tablename__ = "assets"
    run_id: Mapped[str] = mapped_column(ForeignKey("generation_runs.id"), index=True)
    scene_id: Mapped[str | None] = mapped_column(ForeignKey("scenes.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str] = mapped_column(String(180))
    object_key: Mapped[str] = mapped_column(String(700), unique=True)
    content_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    manifest_object_key: Mapped[str | None] = mapped_column(String(700))
    payment_record_id: Mapped[str | None] = mapped_column(ForeignKey("payment_records.id"))
    approval_state: Mapped[str] = mapped_column(String(40), index=True)
    cost_minor: Mapped[int] = mapped_column(Integer, default=0)


class AssetRelation(Record, Base):
    __tablename__ = "asset_relations"
    parent_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    child_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(50))


class Evaluation(Record, Base):
    __tablename__ = "evaluations"
    run_id: Mapped[str] = mapped_column(ForeignKey("generation_runs.id"), index=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    layer: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40))


class EvaluationResult(Record, Base):
    __tablename__ = "evaluation_results"
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("evaluations.id"), index=True)
    check_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), index=True)
    explanation: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text)
    confidence_basis: Mapped[str] = mapped_column(String(120))


class AuditEvent(Record, Base):
    __tablename__ = "audit_events"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor_type: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(180))
    payload_json: Mapped[str] = mapped_column(Text)
    related_object_ids_json: Mapped[str] = mapped_column(Text)
    previous_event_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), unique=True)


class RedressCase(Record, Base):
    __tablename__ = "redress_cases"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("generation_runs.id"), index=True)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payment_records.id"))
    reason: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    evidence_snapshot_json: Mapped[str] = mapped_column(Text)
    recommended_next_action: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(30), index=True)
    resolution: Mapped[str | None] = mapped_column(Text)
    refund_reference: Mapped[str | None] = mapped_column(String(180))


class CaseNote(Record, Base):
    __tablename__ = "case_notes"
    case_id: Mapped[str] = mapped_column(ForeignKey("redress_cases.id"), index=True)
    author: Mapped[str] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(Text)


class IntegrationHealth(Record, Base):
    __tablename__ = "integration_health"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    integration: Mapped[str] = mapped_column(String(80), index=True)
    configured: Mapped[bool] = mapped_column(Boolean)
    healthy: Mapped[bool] = mapped_column(Boolean)
    supported_modalities_json: Mapped[str] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text)
