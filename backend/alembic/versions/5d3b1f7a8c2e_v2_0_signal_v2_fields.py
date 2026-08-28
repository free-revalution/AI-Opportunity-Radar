"""v2.0 Signal V2 fields — Phase 12B.

Per docs/下一阶段开发技术方案.md §6 / §7:

> Signal 数据模型升级 — 在 velocity / engagement / relevance 之上增加
>   title, summary, source_count,
>   novelty_score, confidence_score, commercial_value_score,
>   actionability_score, signal_score,
>   detected_at, published_at, expiration_time,
>   status (DISCOVERED/VALIDATING/VERIFIED/ANALYZING/PUBLISHED/EXPIRED/REJECTED),
>   risk_score, compliance_status

> Signal Score = Freshness × 0.20 + Velocity × 0.20 + Evidence × 0.20
>              + Novelty × 0.15 + Commercial × 0.10 + Actionability × 0.10
>              + Scarcity × 0.05

This migration is additive — no existing column is dropped. Existing rows
default to safe values so the existing Opportunity pipeline keeps running
while the new Signal V2 surface comes online.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5d3b1f7a8c2e"
down_revision = "4c9e2a8f1b3d"  # v2.0 channel_published
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- V2 metadata -----------------------------------------------------
    op.add_column(
        "signals",
        sa.Column("title", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
    )

    # ---- sub-scores (all 0..100) -----------------------------------------
    op.add_column(
        "signals",
        sa.Column("freshness_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "signals",
        sa.Column("evidence_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "signals",
        sa.Column("novelty_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "signals",
        sa.Column("commercial_value_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "signals",
        sa.Column("actionability_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "signals",
        sa.Column("scarcity_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "signals",
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.0"),
    )

    # ---- aggregate Signal Score (0..100) --------------------------------
    op.add_column(
        "signals",
        sa.Column("signal_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_index("ix_signals_signal_score", "signals", ["signal_score"])

    # ---- lifecycle timestamps -------------------------------------------
    op.add_column(
        "signals",
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("expiration_time", sa.DateTime(timezone=True), nullable=True),
    )

    # ---- lifecycle status (state machine) -------------------------------
    # DISCOVERED → VALIDATING → VERIFIED → ANALYZING → PUBLISHED → EXPIRED
    #                                                          (or REJECTED)
    op.add_column(
        "signals",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="discovered",
        ),
    )
    op.create_index("ix_signals_status", "signals", ["status"])

    # ---- compliance posture (mirrors the engine's result) ---------------
    op.add_column(
        "signals",
        sa.Column("risk_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "signals",
        sa.Column(
            "compliance_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "signals",
        sa.Column("compliance_reason", sa.String(length=255), nullable=True),
    )

    # ---- soft delete / retention ----------------------------------------
    op.add_column(
        "signals",
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_index("ix_signals_status", table_name="signals")
    op.drop_index("ix_signals_signal_score", table_name="signals")
    op.drop_column("signals", "retention_until")
    op.drop_column("signals", "compliance_reason")
    op.drop_column("signals", "compliance_status")
    op.drop_column("signals", "risk_score")
    op.drop_column("signals", "status")
    op.drop_column("signals", "expiration_time")
    op.drop_column("signals", "published_at")
    op.drop_column("signals", "detected_at")
    op.drop_column("signals", "signal_score")
    op.drop_column("signals", "confidence_score")
    op.drop_column("signals", "scarcity_score")
    op.drop_column("signals", "actionability_score")
    op.drop_column("signals", "commercial_value_score")
    op.drop_column("signals", "novelty_score")
    op.drop_column("signals", "evidence_score")
    op.drop_column("signals", "freshness_score")
    op.drop_column("signals", "source_count")
    op.drop_column("signals", "summary")
    op.drop_column("signals", "title")