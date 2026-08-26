"""GitHub trending connector.

Public, no-auth data: https://github.com/trending + the GitHub Search API.
We only need `requests per hour < 60`, which the unauthenticated API
allows for the small query volumes the MVP uses.

Mock: returns fixture items when no GitHub token is configured.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


_GITHUB_API = "https://api.github.com"


class GitHubTrendingConnector(SourceConnector):
    source = "github"
    requires_mock_without_keys = ()  # unauthenticated API is sufficient

    def __init__(
        self,
        *,
        token: str | None = None,
        topics: tuple[str, ...] = ("ai", "llm", "rag", "agent"),
        language: str | None = "python",
        mock: bool = False,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.token = token
        self.topics = topics
        self.language = language
        self.timeout = timeout
        self._client = client

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "User-Agent": "ai-opportunity-radar/0.1"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _build_query(self) -> str:
        qualifiers = [f"topic:{t}" for t in self.topics]
        if self.language:
            qualifiers.append(f"language:{self.language}")
        # Stars in last 7 days, sorted by stars desc.
        qualifiers.append("created:>2024-01-01")
        return " ".join(qualifiers) + " sort:stars-desc"

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_github()

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns_client = self._client is None
        try:
            response = await client.get(
                f"{_GITHUB_API}/search/repositories",
                params={"q": self._build_query(), "per_page": 25},
                headers=self._headers(),
            )
            if response.status_code != 200:
                return SourceConnectorResult(
                    source=self.source,
                    errors=[f"github api {response.status_code}: {response.text[:200]}"],
                )
            payload: dict[str, Any] = response.json()
        except httpx.HTTPError as exc:
            return SourceConnectorResult(
                source=self.source, errors=[f"github http error: {exc}"]
            )
        finally:
            if owns_client:
                await client.aclose()

        items: list[RawItem] = []
        for repo in payload.get("items", []):
            pushed_at = repo.get("pushed_at") or repo.get("created_at")
            try:
                published = (
                    datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                    if pushed_at
                    else None
                )
            except ValueError:
                published = None

            metadata = {
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "language": repo.get("language"),
                "topics": repo.get("topics", []),
                "description": repo.get("description"),
            }
            items.append(
                RawItem(
                    source=self.source,
                    source_id=str(repo.get("id") or repo.get("full_name") or ""),
                    url=repo.get("html_url") or "",
                    title=repo.get("full_name") or repo.get("name") or "(unnamed repo)",
                    author=repo.get("owner", {}).get("login"),
                    content=repo.get("description"),
                    published_at=published,
                    metadata=metadata,
                )
            )

        return SourceConnectorResult(source=self.source, items=items)


def _mock_github() -> SourceConnectorResult:
    """Deterministic fixture used when mocking is requested."""
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="github",
        items=[
            RawItem(
                source="github",
                source_id="101",
                url="https://github.com/example/agent-llm",
                title="example/agent-llm",
                author="example",
                content="Browser-using LLM agent framework.",
                published_at=now,
                metadata={"stars": 8421, "forks": 510, "language": "Python", "topics": ["ai", "agent"]},
            ),
            RawItem(
                source="github",
                source_id="102",
                url="https://github.com/example/rag-runtime",
                title="example/rag-runtime",
                author="example",
                content="Production-grade RAG runtime.",
                published_at=now,
                metadata={"stars": 3914, "forks": 280, "language": "Python", "topics": ["ai", "rag"]},
            ),
            RawItem(
                source="github",
                source_id="103",
                url="https://github.com/example/diffusion-toolkit",
                title="example/diffusion-toolkit",
                author="example",
                content="Toolkit for product-image diffusion.",
                published_at=now,
                metadata={"stars": 1502, "forks": 95, "language": "Python", "topics": ["ai", "diffusion"]},
            ),
        ],
    )