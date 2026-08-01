"""Add the external-agent commercial layer.

Revision ID: 20260801_0002
Revises: 20260801_0001
"""

from alembic import op
from sqlalchemy import Column, ForeignKey, Integer, String, inspect

from app.commerce import models as commerce_models  # noqa: F401
from app.thikra import models as thikra_models  # noqa: F401
from app.thikra.database import Base

revision = "20260801_0002"
down_revision = "20260801_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    columns = {column["name"] for column in inspect(bind).get_columns("payment_records")}
    with op.batch_alter_table("payment_records") as batch:
        if "commercial_order_id" not in columns:
            batch.add_column(
                Column(
                    "commercial_order_id",
                    String(36),
                    ForeignKey("commercial_orders.id"),
                    nullable=True,
                )
            )
            batch.create_index(
                "ix_payment_records_commercial_order_id",
                ["commercial_order_id"],
                unique=True,
            )
        if "direction" not in columns:
            batch.add_column(
                Column(
                    "direction", String(40), nullable=False, server_default="PROVIDER_PROCUREMENT"
                )
            )
        if "paid_amount_minor" not in columns:
            batch.add_column(
                Column("paid_amount_minor", Integer, nullable=False, server_default="0")
            )
        batch.alter_column("mandate_id", existing_type=String(36), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("payment_records")}
    with op.batch_alter_table("payment_records") as batch:
        if "commercial_order_id" in columns:
            batch.drop_index("ix_payment_records_commercial_order_id")
            batch.drop_column("commercial_order_id")
        if "direction" in columns:
            batch.drop_column("direction")
        if "paid_amount_minor" in columns:
            batch.drop_column("paid_amount_minor")
    for table in [
        "commerce_disputes",
        "webhook_deliveries",
        "webhook_subscriptions",
        "delivery_receipts",
        "deliverables",
        "fulfillment_jobs",
        "idempotency_records",
        "order_events",
        "commercial_orders",
        "commerce_quotes",
        "api_keys",
        "buyer_agents",
        "developer_applications",
        "buyer_principals",
        "service_offers",
    ]:
        op.drop_table(table)
