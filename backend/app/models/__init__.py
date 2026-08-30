"""SQLAlchemy ORM models.

Schema mirrors the README §10 design — kept in a single file so it's easy
to keep the canonical contract in one place. Phase 2 will add Alembic
migrations generated from this metadata.
"""

from __future__ import annotations

from datetime import date as Date  # Phase 25 v2.1 — daily_digest_docs PK
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Date as SqlaDate,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base — all models inherit from here."""

    type_annotation_map = {dict[str, Any]: JSON}


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # ---- Phase 15A v2.0 — User preferences + Feishu binding -----------
    # Per docs/下一阶段开发技术方案.md §31 / §86-87 / §130:
    #   - `feishu_open_id` ties the legacy email/password User to the
    #     Feishu identity used by the bot commands (Subscription has its
    #     own `feishu_open_id` index — this is a denormalised shortcut
    #     so the bot can look up preferences in one query).
    #   - `vertical / niche / platform / audience / tone / language /
    #     preferences_json` are the personalisation inputs for the
    #     ContentRadarAgent prompt context (Phase 16 will wire them in).
    #   - `subscription_status / subscription_expires_at` mirror the
    #     canonical Subscription row so /preferences can show "you're on
    #     pro until 2026-10-12" without joining two tables.
    feishu_open_id: Mapped[Optional[str]] = mapped_column(
        String(128), unique=True, nullable=True, index=True
    )
    vertical: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    niche: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    platform: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    audience: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    preferences_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    subscription_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    subscription_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
# Per docs/下一阶段开发技术方案.md §22, ``Source`` carries compliance
# metadata (compliance_level, terms/robots URLs, commercial_use_status,
# access_method, rate_limit, last_compliance_check, retention_policy,
# source_block_reason) so the Compliance Engine can gate every Signal
# ingestion by source posture. New sources default to compliance_level
# "E" (block) with "unknown" commercial-use until a human reviews them
# — this is the safe default documented in ``evaluate_source_policy``.
class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    crawl_interval: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ---- V2 compliance posture (Phase 12D) -------------------------------
    compliance_level: Mapped[str] = mapped_column(
        String(1), default="E", nullable=False, index=True
    )
    terms_url: Mapped[Optional[str]] = mapped_column(String(512))
    robots_url: Mapped[Optional[str]] = mapped_column(String(512))
    commercial_use_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    access_method: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    rate_limit: Mapped[Optional[int]] = mapped_column(Integer)
    last_compliance_check: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    retention_policy: Mapped[str] = mapped_column(
        String(64), default="session", nullable=False
    )
    source_block_reason: Mapped[Optional[str]] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# raw_items
# ---------------------------------------------------------------------------
class RawItem(Base):
    __tablename__ = "raw_items"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_source_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    author: Mapped[Optional[str]] = mapped_column(String(255))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------
# Per docs/下一阶段开发技术方案.md §6 / §7, Signal carries an enriched set
# of fields beyond the original velocity/engagement/relevance trio:
#
#   - title / summary: human-readable headline + 2-3 sentence synopsis.
#   - source_count:   number of independent sources backing this Signal.
#   - sub-scores:     Freshness, Velocity, Evidence, Novelty, Commercial,
#                     Actionability, Scarcity (all 0..100).
#   - signal_score:   weighted aggregate (0..100), bands at 50/70/85.
#   - lifecycle:      status state machine
#                     (DISCOVERED → VALIDATING → VERIFIED → ANALYZING →
#                      PUBLISHED → EXPIRED; or REJECTED).
#   - compliance:     risk_score + compliance_status + reason.
#   - retention:      retention_until for data-lifecycle control.
#
# All V2 fields are additive — old Opportunity pipeline keeps running
# while the new Signal surface comes online. See migration
# ``5d3b1f7a8c2e_v2_0_signal_v2_fields.py``.
class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    keyword: Mapped[Optional[str]] = mapped_column(String(128))
    category: Mapped[Optional[str]] = mapped_column(String(64))
    velocity_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ---- V2 metadata -----------------------------------------------------
    title: Mapped[Optional[str]] = mapped_column(String(512))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    source_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # ---- V2 sub-scores (0..100) -----------------------------------------
    freshness_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    novelty_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    commercial_value_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actionability_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    scarcity_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # ---- V2 aggregate (Signal Score 0..100) -----------------------------
    signal_score: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False, index=True
    )

    # ---- V2 lifecycle timestamps ----------------------------------------
    detected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expiration_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # ---- V2 status state machine ----------------------------------------
    #   discovered → validating → verified → analyzing → published → expired
    #                                                       (or rejected)
    status: Mapped[str] = mapped_column(
        String(32), default="discovered", nullable=False, index=True
    )

    # ---- V2 compliance posture ------------------------------------------
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    compliance_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    compliance_reason: Mapped[Optional[str]] = mapped_column(String(255))

    # ---- V2 retention ---------------------------------------------------
    retention_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# opportunities
# ---------------------------------------------------------------------------
class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    slug: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    market: Mapped[Optional[str]] = mapped_column(String(64))
    target_user: Mapped[Optional[str]] = mapped_column(String(255))
    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    trend_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    demand_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    monetization_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    competition_gap_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    china_gap_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    execution_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, index=True)

    # ---- commercial enrichment (Phase 1 v2.0) ------------------------
    # Filled by the content_generator pipeline after Deep Research.
    # `target_customer` and `target_user` are intentionally separate:
    #   * target_user      — the *user* the product serves (existing field)
    #   * target_customer  — the *persona* / paying customer description
    #     used by sales copy (e.g. "海外 SaaS 创始人, 月营收 10k-50k USD")
    target_customer: Mapped[Optional[str]] = mapped_column(String(512))
    market_size: Mapped[Optional[str]] = mapped_column(String(64))  # e.g. "100M-500M USD"
    mvp_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    difficulty: Mapped[Optional[str]] = mapped_column(String(32))   # easy / medium / hard
    monetization_model: Mapped[Optional[str]] = mapped_column(String(128))
    china_gap: Mapped[Optional[str]] = mapped_column(Text)          # free-text gap description
    # content_status    — lifecycle for the auto-generated sales copy.
    #   new          — never run a content generator
    #   generated    — content written, not yet posted anywhere
    #   published    — at least one channel posted
    #   sold         — converted to a paying customer
    content_status: Mapped[str] = mapped_column(
        String(32), default="new", nullable=False, index=True
    )
    # commercial_status — sales-pipeline qualifier (Phase 4 will add
    # payment/delivery fields next to it).
    #   unqualified  — doesn't meet our basic thresholds
    #   qualified    — meets thresholds, worth generating content for
    #   promising    — strong signal, prioritised
    commercial_status: Mapped[str] = mapped_column(
        String(32), default="unqualified", nullable=False, index=True
    )

    # Phase 8 (v2.0) — per-channel publish tracking. JSON map:
    #   {"wechat_article": "2026-08-28T12:34:56+00:00",
    #    "xianyu":         "2026-08-28T12:35:01+00:00"}
    # Drives the Content Center's per-channel ✓ / ○ badges so the
    # operator can see "wechat 已发, xianyu 还没发" without losing the
    # `content_status` high-water mark (which flips to `published` the
    # moment ANY channel is marked). Missing keys = not yet published
    # on that channel.
    channel_published: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(32), default="detected", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    sources: Mapped[list["OpportunitySource"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# opportunity_sources (link table)
# ---------------------------------------------------------------------------
class OpportunitySource(Base):
    __tablename__ = "opportunity_sources"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "raw_item_id", name="uq_opp_raw"),
    )

    opportunity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True
    )
    raw_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_items.id", ondelete="CASCADE"), primary_key=True
    )
    relevance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    opportunity: Mapped[Opportunity] = relationship(back_populates="sources")


# ---------------------------------------------------------------------------
# signal_sources (Phase 12C v2.0 — multi-source verification)
# ---------------------------------------------------------------------------
class SignalSource(Base):
    """Many-to-many between Signals and RawItems.

    Per docs/下一阶段开发技术方案.md §9-10, a Signal can be backed by
    multiple independent RawItems — this table is the formal record of
    which sources contributed. ``relevance`` is a per-source confidence
    (0..1) and ``evidence_type`` carries a free-form tag (e.g. "news",
    "discussion", "release-notes") that downstream agents can use.
    """

    __tablename__ = "signal_sources"
    __table_args__ = (
        UniqueConstraint("signal_id", "raw_item_id", name="uq_signal_raw"),
    )

    signal_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("signals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    raw_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("raw_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relevance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_type: Mapped[Optional[str]] = mapped_column(String(32))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# content_opportunities (Phase 12C v2.0 — vertical interpretation)
# ---------------------------------------------------------------------------
class ContentOpportunity(Base):
    """Vertical interpretation of a Signal — what it means for a creator.

    Per docs/下一阶段开发技术方案.md §11, this table separates "what
    changed in the world" (Signal) from "what a creator can do about it"
    (ContentOpportunity). One Signal can produce many ContentOpportunities
    — one per platform / audience / niche combo.

    Status state machine: draft → approved → published → archived.
    """

    __tablename__ = "content_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("signals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(
        String(32), default="general", nullable=False
    )
    audience: Mapped[Optional[str]] = mapped_column(String(255))
    niche: Mapped[Optional[str]] = mapped_column(String(128))
    tone: Mapped[Optional[str]] = mapped_column(String(64))
    content_angle: Mapped[Optional[str]] = mapped_column(Text)
    hook: Mapped[Optional[str]] = mapped_column(Text)
    title_candidates: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    material_ideas: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    script_outline: Mapped[Optional[str]] = mapped_column(Text)
    recommended_length: Mapped[Optional[int]] = mapped_column(Integer)
    cta: Mapped[Optional[str]] = mapped_column(Text)
    risk_warning: Mapped[Optional[str]] = mapped_column(Text)
    content_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="draft", nullable=False, index=True
    )
    # ---- V2 Phase 17 ----------------------------------------------------
    # Per-row bag for compliance gate verdicts + admin review metadata.
    # ``server_default='{}'`` keeps old rows readable. See migration
    # ``f7a2c9d4e8b1_v2_0_content_opportunity_metadata.py``.
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# research_jobs
# ---------------------------------------------------------------------------
class ResearchJob(Base):
    __tablename__ = "research_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(64))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# research_reports
# ---------------------------------------------------------------------------
class ResearchReport(Base):
    __tablename__ = "research_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    executive_summary: Mapped[Optional[str]] = mapped_column(Text)
    market_analysis: Mapped[Optional[str]] = mapped_column(Text)
    competition_analysis: Mapped[Optional[str]] = mapped_column(Text)
    china_analysis: Mapped[Optional[str]] = mapped_column(Text)
    monetization_analysis: Mapped[Optional[str]] = mapped_column(Text)
    mvp_analysis: Mapped[Optional[str]] = mapped_column(Text)
    risk_analysis: Mapped[Optional[str]] = mapped_column(Text)
    recommendation: Mapped[Optional[str]] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sources_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# notifications
# ---------------------------------------------------------------------------
class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# runs (MVP — simplify §37)
# ---------------------------------------------------------------------------
# Per AI Opportunity Radar MVP 大幅裁剪与代码重构方案.md §37, every
# collection/AI/Feishu pipeline execution records exactly one Run row so
# the `/status` Feishu command can show what the system is doing right
# now and what it last did.
#
# Lifecycle:
#   pending   → row inserted, pipeline hasn't started yet
#   running   → pipeline is mid-flight
#   success   → pipeline finished, no error
#   failed    → pipeline raised; `error` carries the message
#
# `trigger` records who / what kicked off the run:
#   - "scheduler" — the daily n8n cron
#   - "manual"    — an operator hit `/api/internal/pipeline/run`
#   - "bot_run"   — a user typed `/run` in Feishu
class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False, index=True
    )
    trigger: Mapped[str] = mapped_column(
        String(16), default="manual", nullable=False
    )
    # Counts captured at finish time. All nullable so a "running" row
    # has no counts yet.
    raw_count: Mapped[Optional[int]] = mapped_column(Integer)
    new_count: Mapped[Optional[int]] = mapped_column(Integer)
    signal_count: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Phase 25 v2.1 — daily digest Docx index
# ---------------------------------------------------------------------------
class DailyDigestDoc(Base):
    """Index row for the daily digest Docx written to 飞书云盘.

    One row per calendar day (the ``date`` PK is the day the Docx
    represents, not when the write happened — so a midnight-spanning
    write still associates with the correct day). Used by:

      * ``/api/internal/docs/daily?date=YYYY-MM-DD`` — read the
        latest docx for a given day.
      * The "📚 信息源" section of the drive org tree — to back-link
        from a daily doc to its run id.
    """

    __tablename__ = "daily_digest_docs"

    date: Mapped["Date"] = mapped_column(SqlaDate, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_url: Mapped[str] = mapped_column(String(512), nullable=False)
    folder_token: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("runs.id"), nullable=True, index=True
    )
    raw_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    signal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# subscriptions (Phase 12E v2.0 — subscription tiers)
# ---------------------------------------------------------------------------
class Subscription(Base):
    """Subscription record for a user OR a Feishu-bound open id.

    Per docs/下一阶段开发技术方案.md §44 / §47:
      plans:  free | basic | pro | creator
      status: active | expired | suspended | cancelled

    One row per active subscription — multiple rows per user_id are
    allowed (history). The `source_channel` records where the
    subscription came from (xianyu / wechat / direct / feishu).
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="active", nullable=False, index=True
    )
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    source_channel: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# activation_codes (Phase 12E v2.0 — Xianyu-style invite codes)
# ---------------------------------------------------------------------------
class ActivationCode(Base):
    """One-time activation code issued when a customer buys via Xianyu.

    Per docs §52:
      - SHA-256(code + server_pepper) stored as code_hash.
      - Status: unused | active | expired | revoked.
      - bound_feishu_open_id: the Feishu user this code was bound to.

    Codes are written as a hash only — the plaintext is shown to the
    customer once on issue and never persisted server-side.
    """

    __tablename__ = "activation_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="basic", nullable=False)
    order_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="unused", nullable=False, index=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    bound_feishu_open_id: Mapped[Optional[str]] = mapped_column(String(128))
    bound_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# audit_logs (Phase 12E v2.0 — compliance audit trail)
# ---------------------------------------------------------------------------
class AuditLog(Base):
    """Append-only audit trail for publish / research / compliance actions.

    Per docs §65-66:
      actor_type: admin | system | user | bot
      action:     publish | reject | research | refresh | score | activate
      result:     success | failure | blocked | partial
      metadata_json: free-form per-action context (kept small — a few KB).

    Always INSERT, never UPDATE — every action is a new row so the trail
    is tamper-evident via ``created_at`` ordering.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String(64))
    resource_id: Mapped[Optional[str]] = mapped_column(String(128))
    result: Mapped[str] = mapped_column(
        String(32), default="success", nullable=False
    )
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# system_jobs
# ---------------------------------------------------------------------------
class SystemJob(Base):
    __tablename__ = "system_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retry: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# orders (Phase 4 v2.0)
# ---------------------------------------------------------------------------
# A real-world sale of the auto-generated content (or any other revenue
# event tied to an opportunity). Kept intentionally lightweight — only
# the three fields the spec calls out (`customer`, `payment`,
# `delivery_status`) plus the small set of metadata needed for the
# /orders dashboard (channel, snapshot of the opportunity's
# commercial_status at sale time, free-form notes).
#
# One opportunity can have many orders (repeat customers, different
# channels, refunds + re-sells). `delivery_status` mirrors a real
# fulfilment flow:
#   pending     — order recorded, goods not yet delivered
#   delivered   — digital asset / link sent to customer
#   confirmed   — customer acknowledged receipt
#   refunded    — money returned (closed-loop), opportunity stays 'sold'
#   cancelled   — operator aborted before delivery
#
# The opportunity's `content_status='sold'` is the high-water mark;
# individual orders live or die on `delivery_status`.
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # customer ---------------------------------------------------------
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_contact: Mapped[Optional[str]] = mapped_column(String(255))

    # payment ----------------------------------------------------------
    # Stored in CNY (yuan) because all current sales channels are
    # domestic platforms. Use Numeric(10, 2) for currency.
    amount_cny: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payment_method: Mapped[Optional[str]] = mapped_column(String(64))
    payment_reference: Mapped[Optional[str]] = mapped_column(String(255))

    # delivery ---------------------------------------------------------
    delivery_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True
    )

    # meta -------------------------------------------------------------
    # Snapshot of Opportunity.commercial_status at sale time so we can
    # build historical conversion reports even if the opportunity later
    # gets re-classified.
    commercial_status_snapshot: Mapped[Optional[str]] = mapped_column(String(32))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    opportunity: Mapped[Opportunity] = relationship(back_populates="orders")


__all__ = [
    "ActivationCode",
    "AuditLog",
    "Base",
    "ContentOpportunity",
    "Notification",
    "Opportunity",
    "OpportunitySource",
    "Order",
    "RawItem",
    "ResearchJob",
    "ResearchReport",
    "Run",
    "Signal",
    "SignalSource",
    "Source",
    "Subscription",
    "SystemJob",
    "User",
]