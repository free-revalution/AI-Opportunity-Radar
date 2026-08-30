"""Phase 25 v2.1 — daily_digest_docs (飞书云文档 4 段结构).

Per the simplify plan §27 the 4-section Drive Org structure (首页 /
今日 / 每日报告 / 信息源) was specified but never built. Phase 25
v2.1 implements the *每日报告* half — every day's digest gets
written into the Feishu cloud drive as a Docx, and this table
records the day → doc_id mapping so:

  * ``GET /api/internal/docs/daily?date=YYYY-MM-DD`` can resolve
    a calendar day to its Docx URL.
  * The "📚 信息源" / "📌 首页" sections can back-link from a
    daily doc to the run id that produced it.
  * Re-running the daily pipeline for the same day is idempotent
    (we look up by PK first).

Schema mirrors ``backend/app/models/__init__.py::DailyDigestDoc``:

  date          DATE  PK  (the calendar day the Docx represents)
  doc_id        VARCHAR(64)   NOT NULL
  doc_url       VARCHAR(512)  NOT NULL
  folder_token  VARCHAR(64)   NOT NULL  (the YYYY-MM-DD subfolder)
  run_id        INTEGER       REFERENCES runs(id)
  raw_count     INTEGER       NOT NULL DEFAULT 0
  signal_count  INTEGER       NOT NULL DEFAULT 0
  created_at    TIMESTAMPTZ   NOT NULL DEFAULT now()

Revision ID: a7c2e8f1d4b3
Revises: 3a4b5c6d7e8f
Create Date: 2026-08-30 18:40:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7c2e8f1d4b3"
down_revision: Union[str, None] = "3a4b5c6d7e8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_digest_docs",
        sa.Column("date", sa.Date(), primary_key=True, nullable=False),
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("doc_url", sa.String(length=512), nullable=False),
        sa.Column("folder_token", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column(
            "raw_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "signal_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name="fk_daily_digest_docs_run_id_runs",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_daily_digest_docs_run_id",
        "daily_digest_docs",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_daily_digest_docs_run_id", table_name="daily_digest_docs")
    op.drop_table("daily_digest_docs")
