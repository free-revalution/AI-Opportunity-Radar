"""MVP — runs table for /status command (simplify §37).

Per AI Opportunity Radar MVP 大幅裁剪与代码重构方案.md §37, every
collection / AI / Feishu pipeline execution records exactly one Run row
so the Feishu `/status` command can show what the system is doing right
now and what it last did.

Lifecycle:
  pending   → row inserted, pipeline hasn't started yet (unused today)
  running   → pipeline is mid-flight
  success   → pipeline finished, no error
  failed    → pipeline raised; `error` carries the message

Schema mirrors ``backend/app/models/__init__.py::Run``:

  id           PK
  started_at   NOT NULL (server_default now)
  finished_at  NULL until pipeline ends
  status       NOT NULL default 'pending'
  trigger      NOT NULL default 'manual' (scheduler | manual | bot_run)
  raw_count    NULL until finish
  new_count    NULL until finish
  signal_count NULL until finish
  error        NULL on success

Indexes:
  ix_runs_started_at (powers "latest / recent" queries)

Revision ID: 3a4b5c6d7e8f
Revises: 2e3f4a5b6c7d
Create Date: 2026-08-30 15:45:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "3a4b5c6d7e8f"
down_revision: Union[str, None] = "2e3f4a5b6c7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "trigger",
            sa.String(length=16),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("raw_count", sa.Integer(), nullable=True),
        sa.Column("new_count", sa.Integer(), nullable=True),
        sa.Column("signal_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_runs_started_at",
        "runs",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        "ix_runs_status",
        "runs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_started_at", table_name="runs")
    op.drop_table("runs")
