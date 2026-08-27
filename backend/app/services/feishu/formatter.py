"""Feishu interactive-card formatter — turns opportunities into a card.

Feishu custom robots support two payload shapes:

  * `text`   — a single text message
  * `interactive` — a structured card with header, dividers, fields,
                    and action buttons. This is what we ship.

Card layout (top → bottom):

  ┌────────────────────────────────────────────┐
  │ Header: "🔥 AI 机会雷达日报 · <date>"        │  ← template="blue"
  ├────────────────────────────────────────────┤
  │ 今日发现 <N> 个机会,平均评分 <score>        │
  │ ──────────────────                          │
  │ TOP 1 · ⭐ 92                                │
  │ **<opportunity title>**                     │
  │ 市场:<mkt> · MVP:<days>天 · 难度:<diff>     │
  │ <one-line summary>                          │
  │ [查看详情](<url>)                           │
  │ ──────────────────                          │
  │ TOP 2 ...                                   │
  ├────────────────────────────────────────────┤
  │ Footer: 数据来自 7 个信号源 ...               │
  └────────────────────────────────────────────┘

The function takes any iterable of objects exposing the Opportunity
attributes (`title`, `summary`, `total_score`, `target_user`,
`market_size`, `mvp_days`, `difficulty`, etc.). We use `getattr` with
defaults so the formatter works against both real ORM rows and test
fixtures (`SimpleNamespace`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.feishu.base import FeishuCard


# ---------------------------------------------------------------------------
# Constants — keep public so tests can assert against them.
# ---------------------------------------------------------------------------
CARD_HEADER_TEMPLATE = "blue"
CARD_HEADER_EMOJI = "🔥"
CARD_TITLE_PREFIX = "AI 机会雷达日报"

# URL template for the per-opportunity CTA. The dashboard is on :3000
# in dev; operators may override via FEISHU_CTA_BASE_URL.
DEFAULT_CTA_BASE_URL = "http://localhost:3000/opportunities"

# Hard cap on the number of opportunities rendered — Feishu cards have
# a soft 30 KB total payload limit. We cap at 10 (the spec target) and
# truncate summaries to fit.
MAX_OPPORTUNITIES = 10
SUMMARY_CHAR_LIMIT = 140


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _score_emoji(score: float | int | None) -> str:
    """Map a 0-100 score to a single emoji. Used in the header list."""
    if score is None:
        return "⚪"
    try:
        v = float(score)
    except (TypeError, ValueError):
        return "⚪"
    if v >= 90:
        return "⭐"
    if v >= 80:
        return "🟢"
    if v >= 70:
        return "🟡"
    if v >= 60:
        return "🟠"
    return "🔴"


def _truncate(text: str, *, limit: int = SUMMARY_CHAR_LIMIT) -> str:
    if not text:
        return ""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _safe_attr(obj: Any, name: str, default: Any = "") -> Any:
    """`getattr` with a default — keeps the formatter tolerant of test
    fixtures that don't carry every commercial field.
    """
    return getattr(obj, name, default) or default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def format_daily_digest(
    opportunities: Iterable[Any],
    *,
    cta_base_url: str = DEFAULT_CTA_BASE_URL,
    title_prefix: str = CARD_TITLE_PREFIX,
    now: datetime | None = None,
) -> FeishuCard:
    """Build an interactive card for the top opportunities.

    Args:
      opportunities: any iterable of objects with `.title`, `.summary`,
                     `.total_score`, etc.
      cta_base_url:  base URL prepended to the per-opportunity button.
                     Default points at the local dashboard.
      title_prefix:  override the header text (for tests).
      now:           injected for tests; defaults to UTC "now".
    """
    opps = list(opportunities)[:MAX_OPPORTUNITIES]
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    date_str = moment.strftime("%Y-%m-%d")

    header_title = f"{CARD_HEADER_EMOJI} {title_prefix} · {date_str}"

    elements: list[dict[str, Any]] = []

    # ---- top-level summary line --------------------------------------
    if opps:
        scores = [
            float(_safe_attr(o, "total_score", 0.0))
            for o in opps
            if _safe_attr(o, "total_score") is not None
        ]
        avg = sum(scores) / len(scores) if scores else 0.0
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**今日发现 {len(opps)} 个机会**,"
                        f"平均评分 **{avg:.1f}** / 100"
                    ),
                },
            }
        )
        elements.append({"tag": "hr"})
    else:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "_今日暂无新机会,明日再来看看 👀_",
                },
            }
        )

    # ---- per-opportunity blocks --------------------------------------
    for idx, opp in enumerate(opps, start=1):
        score = float(_safe_attr(opp, "total_score", 0.0))
        title = str(_safe_attr(opp, "title", "Untitled"))
        summary = _truncate(str(_safe_attr(opp, "summary", "")))
        mvp_days = _safe_attr(opp, "mvp_days", None)
        difficulty = _safe_attr(opp, "difficulty", None)
        market_size = _safe_attr(opp, "market_size", None)
        opp_id = _safe_attr(opp, "id", idx)

        parts: list[str] = [
            f"**TOP {idx} · {_score_emoji(score)} {int(round(score))}**",
            f"**{title}**",
        ]
        meta_bits: list[str] = []
        if market_size:
            meta_bits.append(f"市场:{market_size}")
        if mvp_days:
            try:
                meta_bits.append(f"MVP:{int(mvp_days)} 天")
            except (TypeError, ValueError):
                pass
        if difficulty:
            meta_bits.append(f"难度:{difficulty}")
        if meta_bits:
            parts.append(" · ".join(meta_bits))
        if summary:
            parts.append(summary)

        cta_url = f"{cta_base_url.rstrip('/')}/{opp_id}"
        parts.append(f"[查看详情]({cta_url})")

        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(parts),
                },
            }
        )
        # Divider between blocks (not after the last).
        if idx < len(opps):
            elements.append({"tag": "hr"})

    # ---- footer note --------------------------------------------------
    if opps:
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": (
                            "数据来自 GitHub / Reddit / Hacker News / "
                            "Product Hunt / RSS / YouTube 7 个信号源"
                        ),
                    }
                ],
            }
        )

    card_payload: dict[str, Any] = {
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": CARD_HEADER_TEMPLATE,
        },
        "elements": elements,
    }

    return FeishuCard.from_card(card=card_payload, title=header_title)


__all__ = [
    "CARD_HEADER_EMOJI",
    "CARD_HEADER_TEMPLATE",
    "CARD_TITLE_PREFIX",
    "DEFAULT_CTA_BASE_URL",
    "MAX_OPPORTUNITIES",
    "SUMMARY_CHAR_LIMIT",
    "format_daily_digest",
]