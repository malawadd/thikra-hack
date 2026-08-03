"""Bring existing Studio asset tables up to the remote-asset contract.

Revision ID: 20260803_0004
Revises: 20260803_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260803_0004"
down_revision = "20260803_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns("studio_assets")
    }
    with op.batch_alter_table("studio_assets") as batch:
        if "remote_url" not in columns:
            batch.add_column(sa.Column("remote_url", sa.String(2048), nullable=True))
        if not columns["local_path"]["nullable"]:
            batch.alter_column(
                "local_path",
                existing_type=sa.String(1200),
                nullable=True,
            )


def downgrade() -> None:
    columns = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns("studio_assets")
    }
    with op.batch_alter_table("studio_assets") as batch:
        if "remote_url" in columns:
            batch.drop_column("remote_url")
