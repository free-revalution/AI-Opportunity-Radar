"""Reddit connector.

Uses the public JSON endpoints (`.json` suffix) which require only a
descriptive User-Agent. Since 2023-06 Reddit blocks unauthenticated
JSON scraping with HTTP 403 "Blocked"; passing ``client_id`` +
``client_secret`` enables OAuth2 client-credentials flow and unlocks
the same endpoints at the documented 100 req/min quota.

Mock mode returns fixtures.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem
from app.utils import get_logger

logger = get_logger(__name__)


DEFAULT_SUBREDDITS = (
    "artificial",
    "LocalLLaMA",
    "SaaS",
    "Entrepreneur",
    "SideProject",
    "indiehackers",
    "startups",
)

_REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_TOKEN_REFRESH_BUFFER_SEC = 60  # refresh 60s before expiry
_TOKEN_LOCK: asyncio.Lock | None = None


def _get_token_lock() -> asyncio.Lock:
    """Lazy-create a module-level asyncio.Lock (event-loop aware)."""
    global _TOKEN_LOCK
    if _TOKEN_LOCK is None:
        _TOKEN_LOCK = asyncio.Lock()
    return _TOKEN_LOCK


class RedditConnector(SourceConnector):
    source = "reddit"

    def __init__(
        self,
        *,
        user_agent: str = "ai-opportunity-radar/0.1 (by /u/yourname)",
        client_id: str | None = None,
        client_secret: str | None = None,
        subreddits: tuple[str, ...] = DEFAULT_SUBREDDITS,
        listing: str = "hot",  # 'hot' | 'new' | 'top'
        limit_per_sub: int = 25,
        mock: bool = False,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.user_agent = user_agent
        self.client_id = client_id
        self.client_secret = client_secret
        self.subreddits = subreddits
        self.listing = listing
        self.limit_per_sub = limit_per_sub
        self.timeout = timeout
        self._client = client
        # Cached OAuth bearer token + expiry epoch seconds.
        self._bearer_token: str | None = None
        self._bearer_token_expires_at: float = 0.0

    def _has_oauth(self) -> bool:
        return bool(self.client_id) and bool(self.client_secret)

    async def _ensure_oauth_token(self, client: httpx.AsyncClient) -> str | None:
        """Fetch + cache an OAuth bearer token. Returns None if OAuth
        credentials are not configured (caller will then fall back to
        the unauthenticated path, which Reddit currently 403s)."""
        if not self._has_oauth():
            return None
        import time

        now = time.time()
        if self._bearer_token and now < self._bearer_token_expires_at - _TOKEN_REFRESH_BUFFER_SEC:
            return self._bearer_token

        async with _get_token_lock():
            # Re-check inside the lock — another coroutine may have
            # refreshed while we were waiting.
            now = time.time()
            if self._bearer_token and now < self._bearer_token_expires_at - _TOKEN_REFRESH_BUFFER_SEC:
                return self._bearer_token

            creds = f"{self.client_id}:{self.client_secret}".encode("utf-8")
            basic = base64.b64encode(creds).decode("ascii")
            try:
                resp = await client.post(
                    _REDDIT_TOKEN_URL,
                    headers={
                        "Authorization": f"Basic {basic}",
                        "User-Agent": self.user_agent,
                    },
                    data={"grant_type": "client_credentials"},
                )
            except httpx.HTTPError as exc:
                logger.warning("reddit_oauth_token_fetch_failed", error=str(exc))
                return None

            if resp.status_code != 200:
                logger.warning(
                    "reddit_oauth_token_non_200",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return None

            payload: dict[str, Any] = resp.json()
            token = payload.get("access_token")
            expires_in = float(payload.get("expires_in") or 3600)
            if not token:
                logger.warning("reddit_oauth_token_missing_in_response")
                return None

            self._bearer_token = str(token)
            self._bearer_token_expires_at = time.time() + expires_in
            logger.info("reddit_oauth_token_acquired", expires_in=int(expires_in))
            return self._bearer_token

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_reddit()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout, headers={"User-Agent": self.user_agent}
        )
        owns_client = self._client is None
        items: list[RawItem] = []
        errors: list[str] = []

        # Pre-fetch OAuth token once (cached on instance) if creds set.
        bearer: str | None = None
        if self._has_oauth():
            bearer = await self._ensure_oauth_token(client)

        try:
            for sub in self.subreddits:
                url = f"https://www.reddit.com/r/{sub}/{self.listing}.json"
                headers = {"User-Agent": self.user_agent}
                if bearer:
                    headers["Authorization"] = f"Bearer {bearer}"
                try:
                    resp = await client.get(
                        url, params={"limit": self.limit_per_sub}, headers=headers
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