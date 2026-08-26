"""RawItem — the canonical in-memory representation of a piece of content.

Connectors return these; the ingestion service normalises them and
writes them to the `raw_items` table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class RawItem:
    """A single piece of content fetched from a source connector."""

    source: str                        # 'github', 'reddit', 'hn', 'producthunt', 'rss', 'youtube'
    source_id: str                     # upstream identifier (slug, post id, etc.)
    url: str
    title: str
    author: str | None = None
    content: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(tz=_UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


# Module-level alias so dataclass field defaults don't shadow the import.
from datetime import timezone as _tz
_UTC = _tz.utc


__all__ = ["RawItem"]