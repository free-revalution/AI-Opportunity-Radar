"""v2.0 orders (Phase 4)

Tracks real-world sales of the auto-generated content (or any other
revenue event tied to an opportunity). This is the third state-machine
the spec calls out, sitting beside `opportunities.content_status`
("is this opp commercially converted?") and `opportunities.commercial_status`
("does this opp meet our sales thresholds?") — orders capture the
transactional detail of each conversion.

One opportunity can have many orders (repeat buyers, different channels,
refunds + re-sells). The opportunity's `content_status='sold'` is the
high-water mark; per-order `delivery_status` reflects the actual
fulfilment of each individual transaction.

Revision ID: 3b7c9d2a1f4e
Revises: 2a31b2e94c5f
Create Date: 2026-08-27 22:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3b7c9d2a1f4e"
down_revision: Union[str, None] = "2a31b2e94c5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("customer_name", sa.String(length=128), nullable=False),
        sa.Column("customer_contact", sa.String(length=255), nullable=True),
        sa.Column("amount_cny", sa.Numeric(10, 2), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("payment_method", sa.String(length=64), nullable=True),
        sa.Column("payment_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "delivery_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("commercial_status_snapshot", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="CASCADE",
            name="fk_orders_opportunity_id_opportunities",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
    )
    # Indexes — `channel` and `delivery_status` are filter predicates
    # on the /orders dashboard; `opportunity_id` is a join key for the
    # Content Center's "show orders for this opp" expansion.
    op.create_index("ix_orders_opportunity_id", "orders", ["opportunity_id"], unique=False)
    op.create_index("ix_orders_channel", "orders", ["channel"], unique=False)
    op.create_index("ix_orders_delivery_status", "orders", ["delivery_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_orders_delivery_status", table_name="orders")
    op.drop_index("ix_orders_channel", table_name="orders")
    op.drop_index("ix_orders_opportunity_id", table_name="orders")
    op.drop_table("orders")
