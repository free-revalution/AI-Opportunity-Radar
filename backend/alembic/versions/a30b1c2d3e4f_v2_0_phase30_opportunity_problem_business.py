"""v2.0 Phase 30 — Opportunity 持久化 screening 原文

Phase 30 把数据流改为"先存储再清洗筛选"(`docs/fancy-spinning-muffin.md`)。
为了让后续的详情 docx(用户点按钮生成)能直接展示 screening 的原文
而**不重新跑 LLM**,Opportunity 表新增三列:

  - ``problem``             — ScreeningResult.problem
  - ``potential_business``  — ScreeningResult.potential_business
  - ``keywords_json``       — ScreeningResult.keywords 列表(JSON 形式)

老的 screening 行这三列都是 NULL,渲染时按"section 缺失"处理,不影响
文档其余部分。

数据契约见 plan §3.1。

Revision ID: a30b1c2d3e4f
Revises: 2e3f4a5b6c7d
Create Date: 2026-08-31 09:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a30b1c2d3e4f"
down_revision: Union[str, None] = "2e3f4a5b6c7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "opportunities",
        sa.Column("problem", sa.Text(), nullable=True),
    )
    op.add_column(
        "opportunities",
        sa.Column("potential_business", sa.Text(), nullable=True),
    )
    op.add_column(
        "opportunities",
        sa.Column("keywords_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("opportunities", "keywords_json")
    op.drop_column("opportunities", "potential_business")
    op.drop_column("opportunities", "problem")
