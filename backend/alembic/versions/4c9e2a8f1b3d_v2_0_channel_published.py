"""v2.0 — per-channel publish tracking (Phase 8)

Adds `opportunities.channel_published` (JSON) so the Content Center can
answer "已发布到哪些渠道" without losing the `content_status` single
high-water mark. The shape is `{"<channel>": "<iso8601 timestamp>"}`
and is filled by `POST /api/internal/content/{id}/mark_published`.

Revision ID: 4c9e2a8f1b3d
Revises: 3b7c9d2a1f4e
Create Date: 2026-08-28 18:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4c9e2a8f1b3d"
down_revision: Union[str, None] = "3b7c9d2a1f4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "opportunities",
        sa.Column("channel_published", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("opportunities", "channel_published")