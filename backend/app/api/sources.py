"""Source connector registry API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import SourceRepository
from app.services.ingestion import registry_as_dict

router = APIRouter()


@router.get("/sources", summary="List configured source connectors")
async def list_sources(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return the static registry merged with live DB state.

    DB state reflects the last successful crawl per source so the UI can
    show "last fetched 5 minutes ago" once Phase 4 wires up the worker.
    """
    static = registry_as_dict()
    repo = SourceRepository(session)
    db_sources = await repo.list_enabled()
    db_index = {s.name.lower(): s for s in db_sources}

    items = []
    for slug, spec in static.items():
        row = db_index.get(slug) or db_index.get(spec["name"].lower())
        items.append({
            **spec,
            "last_success_at": row.last_success_at.isoformat() if row and row.last_success_at else None,
            "last_error_at": row.last_error_at.isoformat() if row and row.last_error_at else None,
        })
    return {"items": items, "total": len(items)}