"""Deterministic WebDataProvider mock for tests + offline development.

The mock derives a plausible document from the URL or query text:
  * the title is the URL hostname
  * the content is a short, predictable paragraph that mentions the
    query keywords (so retrieval-style assertions stay meaningful)

This keeps CI deterministic without requiring real API keys.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from app.services.research.web_data import SourceDoc, WebDataProvider


def _safe_host(url: str) -> str:
    try:
        host = urlparse(url).hostname or "example.com"
        return host.lower()
    except Exception:  # noqa: BLE001
        return "example.com"


def _fake_paragraph(query: str, host: str) -> str:
    cleaned = re.sub(r"\s+", " ", (query or "").strip())
    if not cleaned:
        cleaned = "this topic"
    return (
        f"A page on {host} discusses {cleaned}. "
        f"The article highlights recent momentum, key competitors, and "
        f"go-to-market considerations relevant to {cleaned}. "
        f"Founders cited note that pricing models vary widely across "
        f"the {cleaned} space, with seat-based plans dominant and "
        f"usage-based tiers emerging for power users."
    )


class MockWebDataProvider(WebDataProvider):
    """Heuristic mock — same query → same docs."""

    name = "mock"

    def __init__(self, *, deterministic: bool = True) -> None:
        self.deterministic = deterministic

    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        now = datetime.now(timezone.utc)
        base_hosts = [
            "techcrunch.com",
            "ycombinator.com",
            "producthunt.com",
            "reddit.com",
            "github.com",
        ]
        results: list[SourceDoc] = []
        seen_hosts: set[str] = set()
        for idx in range(limit):
            host = base_hosts[idx % len(base_hosts)]
            # Avoid duplicate hostnames in the first 5 results.
            if host in seen_hosts:
                host = f"{host}.alt"
            seen_hosts.add(host)
            url = f"https://{host}/{_slugify(query)}-{idx + 1}"
            title = f"{query.title()} — {host}" if query else host
            content = _fake_paragraph(query, host)
            results.append(
                SourceDoc(
                    url=url,
                    title=title,
                    content=content,
                    fetched_at=now,
                    via_provider=self.name,
                    metadata={"mock_index": idx},
                )
            )
        return results

    async def scrape(self, url: str) -> SourceDoc:
        host = _safe_host(url)
        return SourceDoc(
            url=url,
            title=f"Page on {host}",
            content=_fake_paragraph(host.split(".")[0], host),
            fetched_at=datetime.now(timezone.utc),
            via_provider=self.name,
            metadata={"host": host},
        )


def _slugify(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
        text = f"topic-{digest}"
    return text[:48] or "topic"


__all__ = ["MockWebDataProvider"]
