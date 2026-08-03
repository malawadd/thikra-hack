"""Add resumable execution lineage and per-node cost accounting.

Revision ID: 20260803_0005
Revises: 20260803_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260803_0005"
down_revision = "20260803_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execution_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("workflow_executions")
    }
    if "resumed_from_execution_id" not in execution_columns:
        with op.batch_alter_table("workflow_executions") as batch:
            batch.add_column(sa.Column("resumed_from_execution_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_workflow_execution_resume",
                "workflow_executions",
                ["resumed_from_execution_id"],
                ["id"],
            )
            batch.create_index(
                "ix_workflow_executions_resumed_from_execution_id",
                ["resumed_from_execution_id"],
            )

    node_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("studio_node_executions")
    }
    if "charged_minor" not in node_columns:
        with op.batch_alter_table("studio_node_executions") as batch:
            batch.add_column(
                sa.Column("charged_minor", sa.Integer(), nullable=False, server_default="0")
            )


def downgrade() -> None:
    with op.batch_alter_table("studio_node_executions") as batch:
        batch.drop_column("charged_minor")
    with op.batch_alter_table("workflow_executions") as batch:
        batch.drop_index("ix_workflow_executions_resumed_from_execution_id")
        batch.drop_constraint("fk_workflow_execution_resume", type_="foreignkey")
        batch.drop_column("resumed_from_execution_id")
