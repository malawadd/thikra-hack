"""Add local-first Studio projects and workflow execution records.

Revision ID: 20260803_0003
Revises: 20260801_0002
"""

import sqlalchemy as sa
from alembic import op

from app.studio import models as studio_models  # noqa: F401
from app.thikra.database import Base

revision = "20260803_0003"
down_revision = "20260801_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("workflow_executions")}
    if "target_node_ids_json" not in columns:
        op.add_column(
            "workflow_executions",
            sa.Column("target_node_ids_json", sa.Text(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    for table in [
        "studio_execution_events",
        "studio_node_executions",
        "workflow_executions",
        "studio_annotations",
        "studio_assets",
        "studio_agent_proposals",
        "workflow_revisions",
        "studio_projects",
    ]:
        op.drop_table(table)
