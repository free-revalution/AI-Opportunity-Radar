"""v2.0 Source compliance metadata — Phase 12D.

Per docs/下一阶段开发技术方案.md §22-23:

> Data Source Registry 新增:
>   compliance_level (A/B/C/D/E),
>   terms_url, robots_url,
>   commercial_use_status (allowed | conditional | forbidden | unknown),
>   access_method (official_api | public_page | rss | search_api |
>                  crawler | unknown),
>   rate_limit (requests / minute),
>   last_compliance_check,
>   retention_policy

> A → allow
> B → allow_with_limits
> C → manual_review
> D → block
> E → block

All fields are additive. Existing sources default to level "E" (block)
with "unknown" commercial use until a human reviews them — this is the
safe default documented in `evaluate_source_policy`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7f8a9b6c1d2e"
down_revision = "6e4c2d9b3a5f"  # v2.0 signal_sources + ContentOpportunity
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "compliance_level",
            sa.String(length=1),
            nullable=False,
            server_default="E",
        ),
    )
    op.add_column("sources", sa.Column("terms_url", sa.String(length=512), nullable=True))
    op.add_column("sources", sa.Column("robots_url", sa.String(length=512), nullable=True))
    op.add_column(
        "sources",
        sa.Column(
            "commercial_use_status",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "access_method",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "sources",
        sa.Column("rate_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("last_compliance_check", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column(
            "retention_policy",
            sa.String(length=64),
            nullable=False,
            server_default="session",
        ),
    )
    op.add_column(
        "sources",
        sa.Column("source_block_reason", sa.String(length=64), nullable=True),
    )

    op.create_index("ix_sources_compliance_level", "sources", ["compliance_level"])


def downgrade() -> None:
    op.drop_index("ix_sources_compliance_level", table_name="sources")
    op.drop_column("sources", "source_block_reason")
    op.drop_column("sources", "retention_policy")
    op.drop_column("sources", "last_compliance_check")
    op.drop_column("sources", "rate_limit")
    op.drop_column("sources", "access_method")
    op.drop_column("sources", "commercial_use_status")
    op.drop_column("sources", "robots_url")
    op.drop_column("sources", "terms_url")
    op.drop_column("sources", "compliance_level")