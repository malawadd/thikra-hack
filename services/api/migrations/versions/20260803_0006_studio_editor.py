"""Add multi-track sequences, render jobs, and rich Studio asset metadata.

Revision ID: 20260803_0006
Revises: 20260803_0005
"""

import sqlalchemy as sa
from alembic import op

revision = "20260803_0006"
down_revision = "20260803_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    known_tables = set(inspector.get_table_names())

    def create_table(name: str, *columns, **kwargs) -> None:
        if name not in known_tables:
            op.create_table(name, *columns, **kwargs)
            known_tables.add(name)

    def create_index(name: str, table: str, columns: list[str]) -> None:
        if name not in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}:
            op.create_index(name, table, columns)

    columns = {item["name"] for item in inspector.get_columns("studio_assets")}
    asset_indexes = {item["name"] for item in inspector.get_indexes("studio_assets")}
    additions = [
        sa.Column("source_kind", sa.String(30), nullable=False, server_default="IMPORTED"),
        sa.Column("width", sa.Integer()), sa.Column("height", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()), sa.Column("frame_rate", sa.String(40)),
        sa.Column("has_audio", sa.Boolean()),
        sa.Column("analysis_status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("thumbnail_path", sa.String(1200)), sa.Column("proxy_path", sa.String(1200)),
        sa.Column("waveform_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("origin_execution_id", sa.String(36)),
        sa.Column("origin_node_id", sa.String(120)),
    ]
    with op.batch_alter_table("studio_assets") as batch:
        for column in additions:
            if column.name not in columns:
                batch.add_column(column)
        if "ix_studio_assets_source_kind" not in asset_indexes:
            batch.create_index("ix_studio_assets_source_kind", ["source_kind"])
        if "ix_studio_assets_analysis_status" not in asset_indexes:
            batch.create_index("ix_studio_assets_analysis_status", ["analysis_status"])
        if "ix_studio_assets_origin_execution_id" not in asset_indexes:
            batch.create_index("ix_studio_assets_origin_execution_id", ["origin_execution_id"])
        if "ix_studio_assets_origin_node_id" not in asset_indexes:
            batch.create_index("ix_studio_assets_origin_node_id", ["origin_node_id"])

    create_table(
        "studio_sequences",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("studio_projects.id"), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("current_revision_id", sa.String(36)),
        sa.Column("current_revision_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("view_state_json", sa.Text(), nullable=False, server_default='{"playhead_ms":0,"zoom":80,"selection":[]}'),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index("ix_studio_sequences_project_id", "studio_sequences", ["project_id"])
    create_index("ix_studio_sequences_current_revision_id", "studio_sequences", ["current_revision_id"])
    create_index("ix_studio_sequences_created_at", "studio_sequences", ["created_at"])
    create_table(
        "studio_sequence_revisions",
        sa.Column("sequence_id", sa.String(36), sa.ForeignKey("studio_sequences.id"), nullable=False),
        sa.Column("parent_revision_id", sa.String(36), sa.ForeignKey("studio_sequence_revisions.id")),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("timeline_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sequence_id", "number", name="ix_sequence_revision_number"),
    )
    for name, cols in [
        ("ix_studio_sequence_revisions_sequence_id", ["sequence_id"]),
        ("ix_studio_sequence_revisions_parent_revision_id", ["parent_revision_id"]),
        ("ix_studio_sequence_revisions_content_hash", ["content_hash"]),
        ("ix_studio_sequence_revisions_created_at", ["created_at"]),
    ]:
        create_index(name, "studio_sequence_revisions", cols)
    create_table(
        "studio_sequence_agent_proposals",
        sa.Column("sequence_id", sa.String(36), sa.ForeignKey("studio_sequences.id"), nullable=False),
        sa.Column("base_revision_id", sa.String(36), sa.ForeignKey("studio_sequence_revisions.id"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False), sa.Column("operations_json", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="PROPOSED"),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_table(
        "studio_renders",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("studio_projects.id"), nullable=False),
        sa.Column("sequence_id", sa.String(36), sa.ForeignKey("studio_sequences.id"), nullable=False),
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("studio_sequence_revisions.id"), nullable=False),
        sa.Column("preset", sa.String(40), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("render_hash", sa.String(64), nullable=False),
        sa.Column("output_asset_id", sa.String(36), sa.ForeignKey("studio_assets.id")),
        sa.Column("srt_asset_id", sa.String(36), sa.ForeignKey("studio_assets.id")),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resumed_from_render_id", sa.String(36)), sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_table(
        "studio_render_events",
        sa.Column("render_id", sa.String(36), sa.ForeignKey("studio_renders.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False), sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("render_id", "sequence", name="ix_studio_render_event_sequence"),
    )
    create_table(
        "studio_generation_jobs",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("studio_projects.id"), nullable=False),
        sa.Column("sequence_id", sa.String(36), sa.ForeignKey("studio_sequences.id")),
        sa.Column("kind", sa.String(20), nullable=False), sa.Column("vendor", sa.String(80), nullable=False),
        sa.Column("model", sa.String(180), nullable=False), sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("reference_asset_id", sa.String(36), sa.ForeignKey("studio_assets.id")),
        sa.Column("variants", sa.Integer(), nullable=False), sa.Column("duration_ms", sa.Integer()),
        sa.Column("estimate_hash", sa.String(64), nullable=False),
        sa.Column("estimated_cost_minor", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False), sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("result_asset_ids_json", sa.Text(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resumed_from_job_id", sa.String(36)), sa.Column("checkpoint_json", sa.Text(), nullable=False),
        sa.Column("error", sa.Text()), sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_table(
        "studio_caption_jobs",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("studio_projects.id"), nullable=False),
        sa.Column("sequence_id", sa.String(36), sa.ForeignKey("studio_sequences.id"), nullable=False),
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("studio_sequence_revisions.id"), nullable=False),
        sa.Column("model", sa.String(180), nullable=False), sa.Column("language", sa.String(20)),
        sa.Column("estimate_hash", sa.String(64), nullable=False),
        sa.Column("estimated_cost_minor", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False), sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("cues_json", sa.Text(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text()), sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_table(
        "studio_job_events",
        sa.Column("job_kind", sa.String(20), nullable=False), sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False), sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_kind", "job_id", "sequence", name="ix_studio_job_event_sequence"),
    )


def downgrade() -> None:
    op.drop_table("studio_job_events")
    op.drop_table("studio_caption_jobs")
    op.drop_table("studio_generation_jobs")
    op.drop_table("studio_render_events")
    op.drop_table("studio_renders")
    op.drop_table("studio_sequence_agent_proposals")
    op.drop_table("studio_sequence_revisions")
    op.drop_table("studio_sequences")
    with op.batch_alter_table("studio_assets") as batch:
        for name in ["origin_node_id", "origin_execution_id", "waveform_json", "proxy_path", "thumbnail_path", "metadata_json", "analysis_status", "has_audio", "frame_rate", "duration_ms", "height", "width", "source_kind"]:
            batch.drop_column(name)
