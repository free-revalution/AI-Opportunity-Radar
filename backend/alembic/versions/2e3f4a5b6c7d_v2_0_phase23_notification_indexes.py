"""v2.0 Phase 23 — indexes on notifications table for the admin /admin/messages viewer.

The `Notification` rows are queried in two distinct ways:

  * `GET /api/admin/notifications` — ORDER BY created_at DESC with
    optional `channel` filter (admin `/messages` viewer).
  * Cooldown check on the renewal-reminder cron — `WHERE channel='feishu'
    AND payload.kind='subscription_renewal_reminder' AND created_at >= ...`.

Without indexes, both paths trigger a full table scan. Phase 23 adds
`ix_notifications_created_at` (powers the DESC sort + since-since-window
filter) and `ix_notifications_channel` (powers the cooldown + filter
chip). Schema is otherwise unchanged — `payload` stays free-form JSON.

Revision ID: 2e3f4a5b6c7d
Revises: f7a2c9d4e8b1
Create Date: 2026-08-30 12:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2e3f4a5b6c7d"
down_revision: Union[str, None] = "f7a2c9d4e8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_notifications_created_at",
        "notifications",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_channel",
        "notifications",
        ["channel"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_channel", table_name="notifications")
    op.drop_index("ix_notifications_created_at", table_name="notifications")