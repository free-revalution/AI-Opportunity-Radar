"""Public notifications feed — read-only view for the dashboard.

The internal `/api/internal/notifications/history` endpoint is HMAC-
guarded and is meant for cron / n8n. The dashboard needs a public
read-only view that surfaces the same rows so the activity feed can
show "last digest sent" / "last alert delivered" / "last failure".

Auth model: relies on the same dev-mode passthrough as the rest of the
public API (no token when `APP_SECRET_KEY` is empty). In production the
dashboard would be deployed behind an authenticated proxy.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Notification

router = APIRouter()


@router.get("/notifications/recent", summary="Recent notification attempts")
async def list_recent_notifications(
    limit: int = Query(20, ge=1, le=100),
    channel: Optional[str] = Query(None, description="Filter by channel"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return up to `limit` recent `Notification` rows for the dashboard."""
    result = await session.execute(
        select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    )
    rows = list(result.scalars().all())
    if channel:
        rows = [r for r in rows if r.channel == channel]
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "channel": r.channel,
                "payload": r.payload,
                "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
                "error": r.error,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
