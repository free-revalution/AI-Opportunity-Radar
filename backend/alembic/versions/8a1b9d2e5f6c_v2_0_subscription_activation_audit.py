"""v2.0 ActivationCode + Subscription + AuditLog — Phase 12E.

Per docs/下一阶段开发技术方案.md §44-53 / §65-67:

> Subscription { id, user_id, plan, status, starts_at, expires_at,
>               source_channel, created_at, updated_at }
>   status: ACTIVE / EXPIRED / SUSPENDED / CANCELLED
>
> ActivationCode { id, code_hash, plan, order_id, status, expires_at,
>                  bound_feishu_open_id, created_at, used_at }
>   status: UNUSED / ACTIVE / EXPIRED / REVOKED
>   不要明文存储 Code,存 SHA-256(code + server_pepper)
>
> AuditLog { actor_type, actor_id, action, resource_type, resource_id,
>            result, metadata, created_at }

Three independent tables, one migration — they're independent columns
but Phase 12G (Feishu RBAC) needs them all together to function.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8a1b9d2e5f6c"
down_revision = "7f8a9b6c1d2e"  # v2.0 source_compliance
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- subscriptions ---------------------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        # Feishu binding at the subscription level so we don't have to
        # also update users (which is still kept for email/password).
        sa.Column("feishu_open_id", sa.String(length=128), nullable=True, index=True),
        sa.Column(
            "plan", sa.String(length=32), nullable=False, server_default="free"
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
            index=True,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("source_channel", sa.String(length=32), nullable=True),
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

    # ---- activation_codes ------------------------------------------------
    op.create_table(
        "activation_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # SHA-256(code + server_pepper). Always 64 hex chars.
        sa.Column(
            "code_hash", sa.String(length=64), nullable=False, unique=True
        ),
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="basic"),
        sa.Column("order_id", sa.Integer(), nullable=True, index=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="unused",
            index=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bound_feishu_open_id", sa.String(length=128), nullable=True),
        sa.Column(
            "bound_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ---- audit_logs ------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "actor_type", sa.String(length=32), nullable=False, index=True
        ),  # admin / system / user / bot
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False, index=True),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        # result: success / failure / blocked / partial
        sa.Column(
            "result",
            sa.String(length=32),
            nullable=False,
            server_default="success",
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
    )
    op.create_index(
        "ix_audit_logs_action_created", "audit_logs", ["action", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_action_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("activation_codes")
    op.drop_table("subscriptions")