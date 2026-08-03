"""Persistence for versioned Studio workflows and node executions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.thikra.database import Base
from app.thikra.models import Record


class StudioProject(Record, Base):
    __tablename__ = "studio_projects"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    budget_cap_minor: Mapped[int] = mapped_column(Integer, default=500)
    spent_minor: Mapped[int] = mapped_column(Integer, default=0)
    current_revision_id: Mapped[str | None] = mapped_column(String(36), index=True)
    current_revision_number: Mapped[int] = mapped_column(Integer, default=0)
    layout_json: Mapped[str] = mapped_column(Text, default="{}")
    viewport_json: Mapped[str] = mapped_column(Text, default='{"x":0,"y":0,"zoom":1}')


class WorkflowRevision(Record, Base):
    __tablename__ = "workflow_revisions"
    project_id: Mapped[str] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    parent_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_revisions.id"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    graph_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(String(500), default="Manual edit")
    source: Mapped[str] = mapped_column(String(30), default="MANUAL")

    __table_args__ = (
        Index("ix_workflow_revision_project_number", "project_id", "number", unique=True),
    )


class AgentProposal(Record, Base):
    __tablename__ = "studio_agent_proposals"
    project_id: Mapped[str] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    base_revision_id: Mapped[str] = mapped_column(ForeignKey("workflow_revisions.id"), index=True)
    prompt: Mapped[str] = mapped_column(Text)
    selected_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    operations_json: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    estimated_cost_impact_minor: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="PROPOSED", index=True)


class StudioAsset(Record, Base):
    __tablename__ = "studio_assets"
    project_id: Mapped[str] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(260))
    asset_type: Mapped[str] = mapped_column(String(30), index=True)
    content_type: Mapped[str] = mapped_column(String(120))
    local_path: Mapped[str | None] = mapped_column(String(1200), unique=True)
    remote_url: Mapped[str | None] = mapped_column(String(2048))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)


class StudioAnnotation(Record, Base):
    __tablename__ = "studio_annotations"
    project_id: Mapped[str] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("studio_assets.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    geometry_json: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    timestamp_ms: Mapped[int | None] = mapped_column(Integer)


class WorkflowExecution(Record, Base):
    __tablename__ = "workflow_executions"
    project_id: Mapped[str] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("workflow_revisions.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    estimated_cost_minor: Mapped[int] = mapped_column(Integer, default=0)
    estimate_hash: Mapped[str] = mapped_column(String(64))
    target_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    force_rerun: Mapped[bool] = mapped_column(Boolean, default=False)
    resumed_from_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_executions.id"), index=True
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)


class NodeExecution(Record, Base):
    __tablename__ = "studio_node_executions"
    execution_id: Mapped[str] = mapped_column(ForeignKey("workflow_executions.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(120), index=True)
    node_type: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    estimated_cost_minor: Mapped[int] = mapped_column(Integer, default=0)
    charged_minor: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_studio_node_execution_unique", "execution_id", "node_id", unique=True),
    )


class StudioExecutionEvent(Record, Base):
    __tablename__ = "studio_execution_events"
    execution_id: Mapped[str] = mapped_column(ForeignKey("workflow_executions.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str | None] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        Index("ix_studio_event_sequence", "execution_id", "sequence", unique=True),
    )
