"""v2.0 Content Center — Phase 17 metadata_json on content_opportunities.

Per docs/下一阶段开发技术方案.md §87 / §130, the Content Center
back-end stores each ``/content <signal_id>`` invocation as a row in
``content_opportunities``. To support the admin review workflow the
row needs an unstructured per-row bag for:

  * ``compliance_blocked`` (bool) — set by the compliance gate.
  * ``compliance_risk_score`` (float) — mirror of ComplianceResult.risk_score.
  * ``compliance_risk_types`` (list[str]) — mirror of ComplianceResult.risk_types.
  * ``feishu_open_id`` (str) — who triggered the row.
  * ``agent_name`` (str) — which ContentRadarAgent produced it.

Phase 18 admin UI may add more keys without another migration; the
JSON column is the right knob for that. ``default=dict`` matches
``audit_logs.metadata_json`` and ``sources.metadata_json`` so old rows
written before this migration decode cleanly via JSON_EXTRACT.

down_revision = 9e1c8a7f5b3d (v2.0_user_preferences — Phase 15A)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f7a2c9d4e8b1"
down_revision: Union[str, None] = "9e1c8a7f5b3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "content_opportunities",
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("content_opportunities", "metadata_json")
