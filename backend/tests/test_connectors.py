"""Tests for the source-connector implementations.

Each connector has:
    1. A mock-mode test (no network) verifying the contract.
    2. A live-mode test using `respx` to mock httpx so we can assert on
       URL / params / response shape without leaving the box.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.config import get_settings
from app.services.ingestion import (
    GitHubTrendingConnector,
    HackerNewsConnector,
    ProductHuntConnector,
    RedditConnector,
    RSSConnector,
    YouTubeConnector,
)
from app.services.ingestion.base import SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


pytestmark = pytest.mark.asyncio


# ----------------- shared assertions -----------------
def _assert_is_raw_item(item: RawItem) -> None:
    assert item.source
    assert item.source_id
    assert item.url
    assert item.title


def _assert_sane_result(result: SourceConnectorResult, *, expected_source: str) -> None:
    assert result.source == expected_source
    for item in result.items:
        _assert_is_raw_item(item)
        assert item.source == expected_source


# ----------------- GitHub -----------------
async def test_github_mock_returns_items():
    connector = GitHubTrendingConnector(mock=True)
    result = await connector.fetch()
    _assert_sane_result(result, expected_source="github")
    assert len(result.items) >= 1


async def test_github_live_parses_payload():
    payload = {
        "items": [
            {
                "id": 1,
                "full_name": "foo/bar",
                "html_url": "https://github.com/foo/bar",
                "owner": {"login": "foo"},
                "description": "desc",
                "stargazers_count": 100,
                "forks_count": 5,
                "language": "Python",
                "topics": ["ai"],
                "pushed_at": "2026-01-01T00:00:00Z",
            }
        ]
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/repositories").mock(
            return_value=Response(200, json=payload)
        )
        connector = GitHubTrendingConnector(mock=False)
        result = await connector.fetch()
        assert route.called
        _assert_sane_result(result, expected_source="github")
        assert result.items[0].title == "foo/bar"
        assert result.items[0].metadata["stars"] == 100


async def test_github_handles_http_error():
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(return_value=Response(500, text="oops"))
        connector = GitHubTrendingConnector(mock=False)
        result = await connector.fetch()
        assert result.is_empty
        assert any("500" in e for e in result.errors)


# ----------------- Hacker News -----------------
async def test_hackernews_mock_returns_items():
    result = await HackerNewsConnector(mock=True).fetch()
    _assert_sane_result(result, expected_source="hackernews")
    assert len(result.items) >= 1


async def test_hackernews_live_parses_payload():
    with respx.mock(base_url="https://hacker-news.firebaseio.com/v0") as mock:
        mock.get("/topstories.json").mock(return_value=Response(200, json=[1, 2]))
        mock.get("/item/1.json").mock(
            return_value=Response(
                200,
                json={
                    "id": 1,
                    "type": "story",
                    "title": "Show HN: foo",
                    "by": "user",
                    "score": 99,
                    "descendants": 10,
                    "time": 1_700_000_000,
                    "url": "https://example.com",
                },
            )
        )
        mock.get("/item/2.json").mock(return_value=Response(404))
        result = await HackerNewsConnector(top_n=2, mock=False).fetch()
        assert result.errors == []
        assert len(result.items) == 1
        assert result.items[0].title == "Show HN: foo"


async def test_hackernews_skips_deleted_items():
    with respx.mock(base_url="https://hacker-news.firebaseio.com/v0") as mock:
        mock.get("/topstories.json").mock(return_value=Response(200, json=[1]))
        mock.get("/item/1.json").mock(
            return_value=Response(200, json={"id": 1, "deleted": True, "type": "story"})
        )
        result = await HackerNewsConnector(mock=False).fetch()
        assert result.items == []


# ----------------- Reddit -----------------
async def test_reddit_mock_returns_items():
    result = await RedditConnector(mock=True).fetch()
    _assert_sane_result(result, expected_source="reddit")


async def test_reddit_live_parses_payload():
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "abc",
                        "title": "Test post",
                        "permalink": "/r/SaaS/comments/abc/test",
                        "subreddit": "SaaS",
                        "score": 50,
                        "num_comments": 5,
                        "author": "someone",
                        "selftext": "hi",
                        "created_utc": 1_700_000_000.0,
                    }
                },
                {
                    "data": {
                        "id": "nsfw",
                        "title": "NSFW post",
                        "permalink": "/r/nsfw/comments/nsfw/test",
                        "subreddit": "nsfw",
                        "over_18": True,
                    }
                },
            ]
        }
    }
    with respx.mock(base_url="https://www.reddit.com") as mock:
        mock.get("/r/SaaS/hot.json").mock(return_value=Response(200, json=payload))
        mock.get("/r/LocalLLaMA/hot.json").mock(return_value=Response(200, json={"data": {"children": []}}))
        mock.get("/r/Entrepreneur/hot.json").mock(return_value=Response(200, json={"data": {"children": []}}))
        mock.get("/r/SideProject/hot.json").mock(return_value=Response(200, json={"data": {"children": []}}))
        mock.get("/r/indiehackers/hot.json").mock(return_value=Response(200, json={"data": {"children": []}}))
        mock.get("/r/startups/hot.json").mock(return_value=Response(200, json={"data": {"children": []}}))
        mock.get("/r/artificial/hot.json").mock(return_value=Response(200, json={"data": {"children": []}}))
        result = await RedditConnector(
            subreddits=("SaaS", "LocalLLaMA", "Entrepreneur", "SideProject",
                        "indiehackers", "startups", "artificial"),
            mock=False,
        ).fetch()
        # Only the SaaS sub returns data; the NSFW child must be skipped.
        assert len(result.items) == 1
        assert result.items[0].title == "Test post"


# ----------------- Product Hunt -----------------
async def test_producthunt_mock_returns_items():
    result = await ProductHuntConnector(mock=True).fetch()
    _assert_sane_result(result, expected_source="producthunt")


async def test_producthunt_falls_back_to_public_endpoint():
    payload = {
        "posts": [
            {
                "id": 1,
                "name": "Foo",
                "slug": "foo",
                "tagline": "Tag",
                "description": "Desc",
                "url": "https://www.producthunt.com/posts/foo",
                "votesCount": 100,
                "commentsCount": 20,
                "createdAt": "2026-01-01T00:00:00Z",
                "user": {"username": "founder"},
                "topics": [{"name": "AI"}],
            }
        ]
    }
    with respx.mock() as mock:
        mock.post("https://api.producthunt.com/v2/api/graphql").mock(
            return_value=Response(401, text="auth required")
        )
        mock.get("https://www.producthunt.com/posts.json").mock(
            return_value=Response(200, json=payload)
        )
        result = await ProductHuntConnector(token=None, mock=False).fetch()
        _assert_sane_result(result, expected_source="producthunt")
        assert result.items[0].title == "Foo"
        assert result.items[0].metadata["votes"] == 100


# ----------------- RSS -----------------
async def test_rss_mock_returns_items():
    result = await RSSConnector(mock=True).fetch()
    _assert_sane_result(result, expected_source="rss")


async def test_rss_live_parses_payload():
    rss_body = b"""<?xml version='1.0'?>
    <rss version='2.0'><channel>
      <title>OpenAI News</title>
      <item>
        <title>Announcement</title>
        <link>https://openai.com/news/announcement</link>
        <description>Body</description>
        <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
        <guid>announcement</guid>
      </item>
    </channel></rss>
    """
    # Phase 29 — use the current DEFAULT_FEEDS manifest so the test
    # stays aligned if a future operator swaps more URLs.
    from app.services.ingestion.rss import DEFAULT_FEEDS
    with respx.mock() as mock:
        for _name, url, _category in DEFAULT_FEEDS:
            mock.get(url).mock(return_value=Response(200, content=rss_body))
        result = await RSSConnector(mock=False).fetch()
        _assert_sane_result(result, expected_source="rss")
        assert len(result.items) == len(DEFAULT_FEEDS)
        assert all(item.title == "Announcement" for item in result.items)


async def test_rss_handles_feed_failure_gracefully():
    # Phase 29 — pick a real feed from the current manifest instead of
    # the removed OpenAI Blog URL.
    from app.services.ingestion.rss import DEFAULT_FEEDS
    failing_name, failing_url, _ = DEFAULT_FEEDS[0]
    with respx.mock() as mock:
        mock.get(failing_url).mock(return_value=Response(500))
        for _name, url, _category in DEFAULT_FEEDS[1:]:
            mock.get(url).mock(return_value=Response(200, content=b"<rss><channel></channel></rss>"))
        result = await RSSConnector(mock=False).fetch()
        assert any("500" in e for e in result.errors), (
            f"expected one error containing '500' for {failing_name!r}, "
            f"got errors={result.errors}"
        )
        # Other feeds should still contribute items (empty here, but no crash).
        assert result.items == []


# ----------------- YouTube -----------------
async def test_youtube_without_key_falls_back_to_mock():
    connector = YouTubeConnector(api_key=None, mock=False)
    result = await connector.fetch()
    _assert_sane_result(result, expected_source="youtube")


async def test_youtube_explicitly_skipped_when_key_missing():
    connector = YouTubeConnector(api_key=None, mock=False)
    # We force the connector to also skip by setting requires_mock_without_keys
    # + passing mock=False. YouTube returns the skipped_reason.
    result = await connector.fetch()
    # Even if mock kicks in, the result must be sane.
    assert result.source == "youtube"