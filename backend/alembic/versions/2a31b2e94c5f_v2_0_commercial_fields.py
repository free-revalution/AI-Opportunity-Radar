"""v2.0 commercial fields on opportunities

Phase 1 of the upgrade from SaaS to "AI 商业情报生产系统". These fields
turn each Opportunity into a self-contained sales asset:
- `target_customer` / `market_size` / `monetization_model` —
  the inputs every content generator needs to write sales copy.
- `mvp_days` / `difficulty` / `china_gap` — the cost/risk picture
  surfaced in every report.
- `content_status` / `commercial_status` — two orthogonal state
  machines. content_status tracks the auto-generated sales copy
  lifecycle (new → generated → published → sold). commercial_status
  tracks the human sales pipeline (Phase 4 will add payment/delivery
  fields alongside it).

All columns are nullable or have non-null defaults so this migration
is safe to apply to a populated database — every existing row just
gets the defaults.

Revision ID: 2a31b2e94c5f
Revises: b715c3f3259b
Create Date: 2026-08-27 20:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2a31b2e94c5f"
down_revision: Union[str, None] = "b715c3f3259b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "opportunities",
        sa.Column("target_customer", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "opportunities",
        sa.Column("market_size", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "opportunities",
        sa.Column(
            "mvp_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "opportunities",
        sa.Column("difficulty", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "opportunities",
        sa.Column("monetization_model", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "opportunities",
        sa.Column("china_gap", sa.Text(), nullable=True),
    )
    op.add_column(
        "opportunities",
        sa.Column(
            "content_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
    )
    op.add_column(
        "opportunities",
        sa.Column(
            "commercial_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unqualified'"),
        ),
    )
    # Indexes for the two status columns — both are used as filter
    # predicates by the content_generator service ("where
    # content_status = 'new' and commercial_status in ('qualified',
    # 'promising')") so a non-unique btree index keeps that scan cheap
    # as the table grows.
    op.create_index(
        "ix_opportunities_content_status",
        "opportunities",
        ["content_status"],
        unique=False,
    )
    op.create_index(
        "ix_opportunities_commercial_status",
        "opportunities",
        ["commercial_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_opportunities_commercial_status", table_name="opportunities")
    op.drop_index("ix_opportunities_content_status", table_name="opportunities")
    op.drop_column("opportunities", "commercial_status")
    op.drop_column("opportunities", "content_status")
    op.drop_column("opportunities", "china_gap")
    op.drop_column("opportunities", "monetization_model")
    op.drop_column("opportunities", "difficulty")
    op.drop_column("opportunities", "mvp_days")
    op.drop_column("opportunities", "market_size")
    op.drop_column("opportunities", "target_customer")