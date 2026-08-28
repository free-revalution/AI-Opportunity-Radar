"""v2.0 signal_sources + ContentOpportunity — Phase 12C.

Per docs/下一阶段开发技术方案.md §10 / §11:

> Signal 与 RawItem 的关系 — Signal.raw_item_id 继续保留。
> 新增 signal_sources (signal_id, raw_item_id, relevance, evidence_type),
> 用于多源验证。
>
> 不要把所有内容创作字段塞进 Signal。新增 ContentOpportunity:
>   id, signal_id, platform, audience, content_angle, hook,
>   title_candidates, script_outline, recommended_length,
>   material_ideas, cta, risk_warning, content_score, status,
>   created_at, updated_at

The ContentOpportunity table is the *vertical interpretation* of a Signal
— what it means for a content creator (per docs §5).

Two changes in one migration because they're tightly coupled (signal_sources
is the foreign key target for ContentOpportunity.signal_id).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6e4c2d9b3a5f"
down_revision = "5d3b1f7a8c2e"  # v2.0 signal_v2_fields
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- signal_sources (many-to-many) ----------------------------------
    op.create_table(
        "signal_sources",
        sa.Column(
            "signal_id",
            sa.Integer(),
            sa.ForeignKey("signals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "raw_item_id",
            sa.Integer(),
            sa.ForeignKey("raw_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("relevance", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("evidence_type", sa.String(length=32), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_signal_sources_signal", "signal_sources", ["signal_id"])
    op.create_index("ix_signal_sources_raw_item", "signal_sources", ["raw_item_id"])

    # ---- content_opportunities -------------------------------------------
    op.create_table(
        "content_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "signal_id",
            sa.Integer(),
            sa.ForeignKey("signals.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # platform: douyin / xiaohongshu / bilibili / wechat / general
        sa.Column(
            "platform", sa.String(length=32), nullable=False, server_default="general"
        ),
        # audience / niche / tone — surfaced to ContentRadarAgent later.
        sa.Column("audience", sa.String(length=255), nullable=True),
        sa.Column("niche", sa.String(length=128), nullable=True),
        sa.Column("tone", sa.String(length=64), nullable=True),
        sa.Column("content_angle", sa.Text(), nullable=True),
        sa.Column("hook", sa.Text(), nullable=True),
        # title_candidates + material_ideas are arrays of strings.
        sa.Column("title_candidates", sa.JSON(), nullable=True),
        sa.Column("material_ideas", sa.JSON(), nullable=True),
        sa.Column("script_outline", sa.Text(), nullable=True),
        sa.Column("recommended_length", sa.Integer(), nullable=True),
        sa.Column("cta", sa.Text(), nullable=True),
        sa.Column("risk_warning", sa.Text(), nullable=True),
        sa.Column("content_score", sa.Float(), nullable=False, server_default="0.0"),
        # status: draft / approved / published / archived
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="draft"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_content_opportunities_status", "content_opportunities", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_content_opportunities_status", table_name="content_opportunities")
    op.drop_table("content_opportunities")
    op.drop_index("ix_signal_sources_raw_item", table_name="signal_sources")
    op.drop_index("ix_signal_sources_signal", table_name="signal_sources")
    op.drop_table("signal_sources")