"""YouTube connector — placeholder.

Phase 3 ships only a fixture-mode implementation. The real YouTube Data
    v3 API integration lands once we have a `YOUTUBE_API_KEY` and a curated
    list of channels. The interface is identical so swapping is a one-liner.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


class YouTubeConnector(SourceConnector):
    source = "youtube"
    requires_mock_without_keys = ("YOUTUBE_API_KEY",)

    def __init__(self, *, api_key: str | None = None, mock: bool = False) -> None:
        super().__init__(mock=mock or not api_key)
        self.api_key = api_key

    async def fetch(self) -> SourceConnectorResult:
        if self.mock or not self.api_key:
            return _mock_youtube()

        # Real YouTube Data v3 integration lives behind a feature flag and
        # lands once we curate the channel list.
        return SourceConnectorResult(
            source=self.source,
            skipped_reason="YOUTUBE_API_KEY not configured; live integration ships in Phase 3.1",
        )


def _mock_youtube() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="youtube",
        items=[
            RawItem(
                source="youtube",
                source_id="yt-1",
                url="https://www.youtube.com/watch?v=avatar-demo",
                title="I built an AI avatar tool to $4k MRR (storytime)",
                author="indiedev",
                published_at=now,
                metadata={"views": 84210, "likes": 2310, "channel": "indiedev"},
            ),
        ],
    )


__all__ = ["YouTubeConnector"]