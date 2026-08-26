"""Reddit connector.

Uses the public JSON endpoints (`.json` suffix) which require only a
descriptive User-Agent. For higher quotas the operator can drop a
client_id/client_secret into the env to enable OAuth2 script flow.

Mock mode returns fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


DEFAULT_SUBREDDITS = (
    "artificial",
    "LocalLLaMA",
    "SaaS",
    "Entrepreneur",
    "SideProject",
    "indiehackers",
    "startups",
)


class RedditConnector(SourceConnector):
    source = "reddit"

    def __init__(
        self,
        *,
        user_agent: str = "ai-opportunity-radar/0.1 (by /u/yourname)",
        subreddits: tuple[str, ...] = DEFAULT_SUBREDDITS,
        listing: str = "hot",  # 'hot' | 'new' | 'top'
        limit_per_sub: int = 25,
        mock: bool = False,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.user_agent = user_agent
        self.subreddits = subreddits
        self.listing = listing
        self.limit_per_sub = limit_per_sub
        self.timeout = timeout
        self._client = client

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_reddit()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout, headers={"User-Agent": self.user_agent}
        )
        owns_client = self._client is None
        items: list[RawItem] = []
        errors: list[str] = []
        try:
            for sub in self.subreddits:
                url = f"https://www.reddit.com/r/{sub}/{self.listing}.json"
                try:
                    resp = await client.get(
                        url, params={"limit": self.limit_per_sub}
                    )
                except httpx.HTTPError as exc:
                    errors.append(f"reddit/{sub}: {exc}")
                    continue
                if resp.status_code != 200:
                    errors.append(f"reddit/{sub} {resp.status_code}")
                    continue
                payload: dict[str, Any] = resp.json()
                for child in payload.get("data", {}).get("children", []):
                    d = child.get("data") or {}
                    if d.get("over_18") or d.get("removed_by_category"):
                        continue
                    created = d.get("created_utc")
                    published_at = (
                        datetime.fromtimestamp(created, tz=timezone.utc) if created else None
                    )
                    items.append(
                        RawItem(
                            source=self.source,
                            source_id=str(d.get("id") or d.get("name")),
                            url="https://www.reddit.com" + (d.get("permalink") or ""),
                            title=d.get("title") or "(untitled)",
                            author=d.get("author"),
                            content=d.get("selftext") or None,
                            published_at=published_at,
                            metadata={
                                "subreddit": d.get("subreddit"),
                                "score": d.get("score", 0),
                                "num_comments": d.get("num_comments", 0),
                                "upvote_ratio": d.get("upvote_ratio"),
                                "url": d.get("url_overridden_by_dest") or d.get("url"),
                            },
                        )
                    )
        finally:
            if owns_client:
                await client.aclose()

        return SourceConnectorResult(source=self.source, items=items, errors=errors)


def _mock_reddit() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="reddit",
        items=[
            RawItem(
                source="reddit",
                source_id="r1",
                url="https://www.reddit.com/r/SaaS/comments/abc/i_built_an_ai_avatar_tool_to_4k_mrr/",
                title="I built an AI avatar tool to $4k MRR",
                author="indie_throwaway",
                content="Sharing learnings after 6 months.",
                published_at=now,
                metadata={"subreddit": "SaaS", "score": 412, "num_comments": 88},
            ),
            RawItem(
                source="reddit",
                source_id="r2",
                url="https://www.reddit.com/r/LocalLLaMA/comments/def/local_video_localization_pipeline/",
                title="Local video localization pipeline (open source)",
                author="ml_dev",
                content="Wav2Lip + Whisper + a small FastAPI server.",
                published_at=now,
                metadata={"subreddit": "LocalLLaMA", "score": 198, "num_comments": 32},
            ),
        ],
    )