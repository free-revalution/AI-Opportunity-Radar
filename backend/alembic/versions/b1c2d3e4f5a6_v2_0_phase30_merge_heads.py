"""Phase 30 — merge a30b1c2d3e4f + a7c2e8f1d4b3 into a single head.

Two parallel feature branches landed before the merge revision was
authored:

  * ``a30b1c2d3e4f`` — Phase 30 opportunity columns
    (problem / potential_business / keywords_json).
  * ``a7c2e8f1d4b3`` — Phase 26 daily_digest_docs table.

This empty revision merges them so ``alembic upgrade head`` resolves
to a single head again.

Revision ID: b1c2d3e4f5a6
Revises: a30b1c2d3e4f, a7c2e8f1d4b3
Create Date: 2026-08-31 10:30:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, tuple[str, ...], None] = (
    "a30b1c2d3e4f",
    "a7c2e8f1d4b3",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # — Pure merge; no schema changes. Both parents already ran.
    pass


def downgrade() -> None:
    # — Splitting the merge reverses back into the parent branch —
    # we don't need to undo any column work here.
    pass
