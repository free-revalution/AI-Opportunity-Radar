"""Phase 17 — Signal HTTP endpoint.

``GET /api/signals`` returns recent signals with optional filters
(``status``, ``min_signal_score``) for the admin Content Center.

Auth: Phase 21 unified — ``require_admin`` from ``app/api/deps.py``
(webhook / admin secret / Feishu open_id).
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_session
from app.repositories.signals import SignalRepository


router = APIRouter()


def _to_utc_iso(dt: Any) -> Optional[str]:
    """Serialise a datetime as ISO-8601 in UTC.

    SQLite strips tzinfo from ``DateTime(timezone=True)`` columns on
    read, so attach UTC before serialising.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


@router.get("/signals", summary="List recent signals (admin)")
async def list_signals(
    status: Optional[str] = Query(
        default=None,
        description="Filter by lifecycle status "
        "(discovered | validating | verified | analyzing | published | expired | rejected)",
    ),
    min_signal_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=100.0,
        description="Lower bound on Signal.signal_score (0..100)",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    repo = SignalRepository(session)
    rows, total = await repo.list_recent(
        status=status,
        min_signal_score=min_signal_score,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [
            {
                "id": r.id,
                "raw_item_id": r.raw_item_id,
                "signal_type": r.signal_type,
                "keyword": r.keyword,
                "category": r.category,
                "title": r.title,
                "summary": r.summary,
                "signal_score": float(r.signal_score),
                "confidence_score": float(r.confidence_score),
                "status": r.status,
                "compliance_status": r.compliance_status,
                "risk_score": float(r.risk_score),
                "created_at": _to_utc_iso(r.created_at),
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
