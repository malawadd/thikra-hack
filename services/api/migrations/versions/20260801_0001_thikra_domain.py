"""Create the complete Thikra accountability domain.

Revision ID: 20260801_0001
Revises: none
"""

from alembic import op

from app.thikra import models  # noqa: F401
from app.thikra.database import Base

revision = "20260801_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
