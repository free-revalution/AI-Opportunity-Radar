"""SQLAlchemy ORM models.

Schema mirrors the README §10 design — kept in a single file so it's easy
to keep the canonical contract in one place. Phase 2 will add Alembic
migrations generated from this metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
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


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
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


__all__ = [
    "Base",
    "User",
    "Source",
    "RawItem",
    "Signal",
    "Opportunity",
    "OpportunitySource",
    "ResearchJob",
    "ResearchReport",
    "Notification",
    "SystemJob",
]