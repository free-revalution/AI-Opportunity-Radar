"""Product Hunt connector.

Uses the public unofficial JSON endpoint at
https://www.producthunt.com/posts.json — the same payload the website
renders, no token required for browsing recent launches.

Production: replace with the GraphQL API + token once we hit rate limits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


class ProductHuntConnector(SourceConnector):
    source = "producthunt"

    def __init__(
        self,
        *,
        token: str | None = None,
        days_back: int = 1,
        mock: bool = False,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.token = token
        self.days_back = days_back
        self.timeout = timeout
        self._client = client

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/json",
            "User-Agent": "ai-opportunity-radar/0.1",
            "Content-Type": "application/json",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_producthunt()

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns_client = self._client is None

        query = """
        {
          posts(first: 30, order: VOTES, postedAfter: \"%s\") {
            edges {
              node {
                id name slug tagline description url votesCount commentsCount createdAt
                user { username name }
                topics { edges { node { name slug } } }
              }
            }
          }
        }
        """ % _days_ago_iso(self.days_back)

        try:
            response = await client.post(
                "https://api.producthunt.com/v2/api/graphql",
                json={"query": query},
                headers=self._headers(),
            )
            if response.status_code != 200 or not self.token:
                # Fall back to the public HTML JSON endpoint.
                response = await client.get(
                    "https://www.producthunt.com/posts.json",
                    headers=self._headers(),
                )
            if response.status_code != 200:
                return SourceConnectorResult(
                    source=self.source, errors=[f"ph {response.status_code}: {response.text[:200]}"]
                )
            payload: dict[str, Any] = response.json()
        except httpx.HTTPError as exc:
            return SourceConnectorResult(source=self.source, errors=[f"ph http error: {exc}"])
        finally:
            if owns_client:
                await client.aclose()

        items: list[RawItem] = []
        # GraphQL shape
        for edge in (
            payload.get("data", {}).get("posts", {}).get("edges", [])
            if "data" in payload
            else []
        ):
            node = edge.get("node") or {}
            items.append(_node_to_raw(self.source, node))

        # HTML-JSON shape (`posts` list directly)
        for post in payload.get("posts", []) or []:
            items.append(_node_to_raw(self.source, post))

        # Dedup by slug.
        seen: set[str] = set()
        unique: list[RawItem] = []
        for item in items:
            key = f"{item.source_id}|{item.url}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)

        return SourceConnectorResult(source=self.source, items=unique)


def _node_to_raw(source: str, node: dict[str, Any]) -> RawItem:
    published_at = None
    if node.get("createdAt"):
        try:
            published_at = datetime.fromisoformat(
                node["createdAt"].replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            published_at = None

    topics_field = node.get("topics")
    topics: list[str] = []
    if isinstance(topics_field, dict):
        for t in topics_field.get("edges", []) or []:
            if not isinstance(t, dict):
                continue
            inner = t.get("node")
            if isinstance(inner, dict) and inner.get("name"):
                topics.append(inner["name"])
    elif isinstance(topics_field, list):
        for t in topics_field:
            if isinstance(t, dict) and t.get("name"):
                topics.append(t["name"])

    user = node.get("user") or {}
    return RawItem(
        source=source,
        source_id=str(node.get("id") or node.get("slug") or ""),
        url=node.get("url") or f"https://www.producthunt.com/posts/{node.get('slug','')}",
        title=node.get("name") or "(untitled)",
        author=user.get("username") or user.get("name"),
        content=node.get("tagline") or node.get("description"),
        published_at=published_at,
        metadata={
            "votes": node.get("votesCount", 0),
            "comments": node.get("commentsCount", 0),
            "topics": topics,
        },
    )


def _days_ago_iso(days: int) -> str:
    from datetime import timedelta

    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _mock_producthunt() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="producthunt",
        items=[
            RawItem(
                source="producthunt",
                source_id="ph-1",
                url="https://www.producthunt.com/posts/ai-sales-coach",
                title="AI Sales Coach",
                author="founder_jane",
                content="Real-time call summaries + CRM enrichment.",
                published_at=now,
                metadata={"votes": 540, "comments": 87, "topics": ["Sales", "AI"]},
            ),
            RawItem(
                source="producthunt",
                source_id="ph-2",
                url="https://www.producthunt.com/posts/legal-gpt",
                title="LegalGPT for Solo Lawyers",
                author="lawtech",
                content="Case-law search + brief drafting for solo practitioners.",
                published_at=now,
                metadata={"votes": 310, "comments": 52, "topics": ["Legal", "AI"]},
            ),
        ],
    )