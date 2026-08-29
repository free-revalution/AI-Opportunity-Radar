"""v2.0 User preferences + Feishu binding — Phase 15A.

Per docs/下一阶段开发技术方案.md §31 / §86-87 / §130:

  > User 增加字段: vertical, niche, platform, audience, tone, language,
  >   preferences_json(其余偏好)
  > User.feishu_open_id 唯一索引 — 让 /preferences 命令能用
  >   Feishu ID 直接定位偏好行,不必再加 UserPreference 副表。

新增 10 列,全部可空 — 老数据全为 NULL 不破坏。`feishu_open_id` 加唯一
索引(已有重复 NULL 的行 SQLite/Postgres 都允许,仅在真实数据时 enforce)。

down_revision = 8a1b9d2e5f6c (subscription_activation_audit — Phase 12E)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9e1c8a7f5b3d"
down_revision = "8a1b9d2e5f6c"  # v2.0 subscription_activation_audit
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- feishu_open_id (唯一索引列) ----------------------------------
    op.add_column(
        "users",
        sa.Column("feishu_open_id", sa.String(length=128), nullable=True),
    )
    # Postgres + SQLite 都允许 NULL 重复,unique 在这里只是数据规模增长
    # 后对真实值的保护。
    op.create_index(
        "ix_users_feishu_open_id",
        "users",
        ["feishu_open_id"],
        unique=True,
    )

    # ---- 个人偏好 6 列 --------------------------------------------------
    op.add_column(
        "users",
        sa.Column("vertical", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("niche", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("platform", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("audience", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("tone", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("language", sa.String(length=8), nullable=True),
    )

    # ---- preferences_json (任意键值) -----------------------------------
    op.add_column(
        "users",
        sa.Column("preferences_json", sa.JSON(), nullable=True),
    )

    # ---- Subscription 镜像字段(给 /preferences 一行读出来) --------------
    op.add_column(
        "users",
        sa.Column("subscription_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "subscription_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "subscription_expires_at")
    op.drop_column("users", "subscription_status")
    op.drop_column("users", "preferences_json")
    op.drop_column("users", "language")
    op.drop_column("users", "tone")
    op.drop_column("users", "audience")
    op.drop_column("users", "platform")
    op.drop_column("users", "niche")
    op.drop_column("users", "vertical")
    op.drop_index("ix_users_feishu_open_id", table_name="users")
    op.drop_column("users", "feishu_open_id")
