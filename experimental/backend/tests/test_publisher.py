"""Phase 11 — Publisher infrastructure + endpoints."""

from __future__ import annotations

import os
from typing import Any

import pytest

from app.services.publisher import (
    FeishuBotPublisher,
    PublishResult,
    Publisher,
    WechatMPPublisher,
    XiaohongshuPublisher,
    XianyuPublisher,
    batch_publish,
    channels as _channels,
    get_publisher,
    is_channel_supported,
    publish_piece,
)


# ---------------------------------------------------------------------------
# Helpers — concrete subclass with configurable behaviour for testing
# ---------------------------------------------------------------------------
class _RecordingPublisher(Publisher):
    """Test double that records calls and returns a pre-canned result."""

    def __init__(
        self,
        name: str,
        channel: str,
        configured: bool = True,
        result: PublishResult | None = None,
    ) -> None:
        self.name = name
        self.channel = channel
        self._configured = configured
        self._result = result or PublishResult(
            publisher=name, channel=channel, success=True, external_id="abc123"
        )
        self.calls: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return self._configured

    async def publish(self, piece: Any) -> PublishResult:
        self.calls.append({"piece": dict(piece) if piece else None})
        return self._result


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------
class TestPublisherRegistry:
    def test_registry_has_all_four_channels(self) -> None:
        chs = set(_channels())
        assert chs == {"feishu", "xianyu", "xiaohongshu", "wechat_article"}

    def test_get_publisher_by_channel(self) -> None:
        for ch in ("feishu", "xianyu", "xiaohongshu", "wechat_article"):
            p = get_publisher(ch)
            assert p.channel == ch

    def test_get_publisher_unknown_channel_raises(self) -> None:
        with pytest.raises(KeyError):
            get_publisher("ghost_channel")

    def test_is_channel_supported(self) -> None:
        assert is_channel_supported("feishu")
        assert not is_channel_supported("ghost_channel")


class TestConcretePublisherStubs:
    def test_wechat_stub_not_configured_by_default(self) -> None:
        # Default env has none of these set in CI.
        p = WechatMPPublisher()
        assert p.channel == "wechat_article"
        assert p.is_configured() is False

    def test_xiaohongshu_stub_not_configured_by_default(self) -> None:
        p = XiaohongshuPublisher()
        assert p.channel == "xiaohongshu"
        assert p.is_configured() is False

    def test_xianyu_stub_not_configured_by_default(self) -> None:
        p = XianyuPublisher()
        assert p.channel == "xianyu"
        assert p.is_configured() is False

    def test_feishu_stub_not_configured_by_default(self) -> None:
        p = FeishuBotPublisher()
        assert p.channel == "feishu"
        assert p.is_configured() is False

    @pytest.mark.asyncio
    async def test_stub_returns_skipped_when_unconfigured(self) -> None:
        p = WechatMPPublisher()
        # Make sure no credentials are set during this test.
        for v in p.required_env_vars:
            os.environ.pop(v, None)
        result = await p.publish({"title": "T", "body": "B"})
        assert result.success is False
        assert result.skipped is True
        assert "WECHAT_MP_APP_ID" in (result.error or "")

    def test_stub_is_configured_when_env_vars_present(self) -> None:
        os.environ["WECHAT_MP_APP_ID"] = "test_app_id"
        os.environ["WECHAT_MP_APP_SECRET"] = "test_app_secret"
        try:
            p = WechatMPPublisher()
            assert p.is_configured() is True
        finally:
            os.environ.pop("WECHAT_MP_APP_ID", None)
            os.environ.pop("WECHAT_MP_APP_SECRET", None)

    @pytest.mark.asyncio
    async def test_stub_with_credentials_but_no_overridden_publish_raises(
        self,
    ) -> None:
        os.environ["WECHAT_MP_APP_ID"] = "x"
        os.environ["WECHAT_MP_APP_SECRET"] = "y"
        try:
            p = WechatMPPublisher()
            with pytest.raises(NotImplementedError):
                await p.publish({"title": "T", "body": "B"})
        finally:
            os.environ.pop("WECHAT_MP_APP_ID", None)
            os.environ.pop("WECHAT_MP_APP_SECRET", None)


class TestPublishResultDataclass:
    def test_as_dict_round_trip(self) -> None:
        r = PublishResult(
            publisher="wechat_mp",
            channel="wechat_article",
            success=True,
            external_id="abc",
            external_url="https://example.com/x",
        )
        d = r.as_dict()
        assert d["publisher"] == "wechat_mp"
        assert d["external_id"] == "abc"
        assert d["external_url"] == "https://example.com/x"

    def test_default_fields(self) -> None:
        r = PublishResult(
            publisher="x", channel="y", success=False
        )
        assert r.skipped is False
        assert r.external_id is None
        assert r.external_url is None
        assert r.error is None


class TestPublishPiece:
    @pytest.mark.asyncio
    async def test_publish_piece_routes_to_correct_publisher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Replace registry entries with recording stubs for this test.
        rec = _RecordingPublisher(
            name="wechat_rec",
            channel="wechat_article",
            configured=True,
            result=PublishResult(
                publisher="wechat_rec",
                channel="wechat_article",
                success=True,
                external_id="draft_42",
            ),
        )

        from app.services import publisher as pub_mod

        original = pub_mod._PUBLISHERS  # type: ignore[attr-defined]
        pub_mod._PUBLISHERS = (rec,)  # type: ignore[attr-defined]
        try:
            result = await publish_piece(
                "wechat_article",
                {"title": "T", "body": "B", "metadata": {}},
            )
        finally:
            pub_mod._PUBLISHERS = original  # type: ignore[attr-defined]

        assert result.success is True
        assert result.external_id == "draft_42"
        assert rec.calls and rec.calls[0]["piece"]["title"] == "T"


class TestBatchPublish:
    @pytest.mark.asyncio
    async def test_batch_publish_fans_out_to_all_channels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Three recording publishers, one per channel.
        recs = [
            _RecordingPublisher(
                name="wechat_rec",
                channel="wechat_article",
                configured=True,
                result=PublishResult(
                    publisher="wechat_rec",
                    channel="wechat_article",
                    success=True,
                    external_id="w_1",
                ),
            ),
            _RecordingPublisher(
                name="xhs_rec",
                channel="xiaohongshu",
                configured=True,
                result=PublishResult(
                    publisher="xhs_rec",
                    channel="xiaohongshu",
                    success=False,
                    error="rate limited",
                ),
            ),
            _RecordingPublisher(
                name="xianyu_rec",
                channel="xianyu",
                configured=False,
                result=PublishResult(
                    publisher="xianyu_rec",
                    channel="xianyu",
                    success=False,
                    skipped=True,
                    error="no credentials",
                ),
            ),
        ]
        from app.services import publisher as pub_mod

        original = pub_mod._PUBLISHERS  # type: ignore[attr-defined]
        pub_mod._PUBLISHERS = tuple(recs)  # type: ignore[attr-defined]
        try:
            results = await batch_publish(
                [
                    ("wechat_article", {"title": "W"}),
                    ("xiaohongshu", {"title": "X"}),
                    ("xianyu", {"title": "Y"}),
                ]
            )
        finally:
            pub_mod._PUBLISHERS = original  # type: ignore[attr-defined]

        assert len(results) == 3
        assert results[0].success is True
        assert results[1].success is False
        assert "rate limited" in (results[1].error or "")
        assert results[2].skipped is True

    @pytest.mark.asyncio
    async def test_batch_publish_continues_on_unknown_channel(self) -> None:
        results = await batch_publish(
            [
                ("ghost_channel", {"title": "X"}),
            ]
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "no publisher" in (results[0].error or "").lower()

    @pytest.mark.asyncio
    async def test_batch_publish_continues_on_publisher_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _BoomPublisher(Publisher):
            name = "boom"
            channel = "feishu"

            def is_configured(self) -> bool:
                return True

            async def publish(self, piece: Any) -> PublishResult:
                raise RuntimeError("kaboom")

        from app.services import publisher as pub_mod

        original = pub_mod._PUBLISHERS  # type: ignore[attr-defined]
        pub_mod._PUBLISHERS = (_BoomPublisher(),)  # type: ignore[attr-defined]
        try:
            results = await batch_publish([("feishu", {"title": "F"})])
        finally:
            pub_mod._PUBLISHERS = original  # type: ignore[attr-defined]

        assert len(results) == 1
        assert results[0].success is False
        assert "kaboom" in (results[0].error or "")
        assert results[0].skipped is False


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------
class TestPublishChannelsEndpoint:
    def test_returns_all_channels_with_configured_status(
        self, client
    ) -> None:
        r = client.get("/api/internal/publish/channels")
        assert r.status_code == 200
        body = r.json()
        assert set(body["channels"]) == {
            "feishu",
            "xianyu",
            "xiaohongshu",
            "wechat_article",
        }
        # No credentials in test env → all unconfigured.
        assert body["configured"] == []
        assert len(body["unconfigured"]) == 4


class TestPublishEndpoint:
    def test_publish_unknown_notification_returns_404(self, client) -> None:
        r = client.post("/api/internal/content/9999/publish")
        assert r.status_code == 404

    def test_publish_unknown_channel_returns_422(self, client, sqlite_engine) -> None:
        from app.models import Notification
        from sqlalchemy import insert

        import asyncio as _asyncio

        async def _seed() -> None:
            async with sqlite_engine.begin() as conn:
                await conn.execute(
                    insert(Notification).values(
                        channel="ghost_channel",
                        payload={"generator": "x", "opportunity_id": 1},
                    )
                )

        _asyncio.get_event_loop().run_until_complete(_seed())

        r = client.post("/api/internal/content/1/publish")
        assert r.status_code == 422

    def test_publish_skipped_for_unconfigured_channel(
        self, client, sqlite_engine
    ) -> None:
        from app.models import Notification
        from sqlalchemy import insert

        import asyncio as _asyncio

        async def _seed() -> None:
            async with sqlite_engine.begin() as conn:
                await conn.execute(
                    insert(Notification).values(
                        channel="wechat_article",
                        payload={
                            "generator": "wechat_article",
                            "opportunity_id": 1,
                            "title": "T",
                            "body": "B",
                            "metadata": {},
                        },
                    )
                )

        _asyncio.get_event_loop().run_until_complete(_seed())

        r = client.post("/api/internal/content/1/publish")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is False
        assert body["skipped"] is True
        assert body["publisher"] == "wechat_mp"
        assert body["marked_published"] is False
        assert "WECHAT_MP_APP_ID" in (body["error"] or "")

    def test_publish_succeeds_with_configured_publisher(
        self, client, sqlite_engine
    ) -> None:
        from app.models import Notification, Opportunity
        from sqlalchemy import insert
        import asyncio as _asyncio

        async def _seed() -> None:
            async with sqlite_engine.begin() as conn:
                await conn.execute(
                    insert(Opportunity).values(
                        id=1,
                        slug="opp-1",
                        title="T",
                        summary="S",
                        content_status="generated",
                        commercial_status="qualified",
                        channel_published={},
                    )
                )
                await conn.execute(
                    insert(Notification).values(
                        channel="wechat_article",
                        payload={
                            "generator": "wechat_article",
                            "opportunity_id": 1,
                            "title": "T",
                            "body": "B",
                            "metadata": {},
                        },
                    )
                )

        _asyncio.get_event_loop().run_until_complete(_seed())

        # Replace the registry with a recording publisher that succeeds.
        from app.services import publisher as pub_mod

        rec = _RecordingPublisher(
            name="wechat_rec",
            channel="wechat_article",
            configured=True,
            result=PublishResult(
                publisher="wechat_rec",
                channel="wechat_article",
                success=True,
                external_id="draft_42",
                external_url="https://mp.weixin.qq.com/s/draft_42",
            ),
        )
        original = pub_mod._PUBLISHERS  # type: ignore[attr-defined]
        pub_mod._PUBLISHERS = (rec,)  # type: ignore[attr-defined]
        try:
            r = client.post("/api/internal/content/1/publish")
        finally:
            pub_mod._PUBLISHERS = original  # type: ignore[attr-defined]

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["external_id"] == "draft_42"
        assert body["external_url"] == "https://mp.weixin.qq.com/s/draft_42"
        assert body["marked_published"] is True

        # Verify the opportunity got stamped + content_status flipped.
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(sqlite_engine, expire_on_commit=False)
        sess_maker = maker

        async def _reload() -> dict[str, Any]:
            async with sess_maker() as session:
                opp = (
                    await session.execute(select(Opportunity).where(Opportunity.id == 1))
                ).scalars().first()
                assert opp is not None
                return {
                    "content_status": opp.content_status,
                    "channel_published": dict(opp.channel_published or {}),
                }

        result = _asyncio.get_event_loop().run_until_complete(_reload())
        assert result["content_status"] == "published"
        assert "wechat_article" in result["channel_published"]


class TestBatchPublishEndpoint:
    def test_batch_publish_requires_notification_ids(self, client) -> None:
        r = client.post("/api/internal/content/batch_publish", json={})
        assert r.status_code == 422

    def test_batch_publish_empty_list_422(self, client) -> None:
        r = client.post(
            "/api/internal/content/batch_publish", json={"notification_ids": []}
        )
        assert r.status_code == 422

    def test_batch_publish_non_int_items_422(self, client) -> None:
        r = client.post(
            "/api/internal/content/batch_publish",
            json={"notification_ids": ["a", "b"]},
        )
        assert r.status_code == 422

    def test_batch_publish_skips_unknown_ids(
        self, client, sqlite_engine
    ) -> None:
        from app.models import Notification
        from sqlalchemy import insert
        import asyncio as _asyncio

        async def _seed() -> None:
            async with sqlite_engine.begin() as conn:
                await conn.execute(
                    insert(Notification).values(
                        channel="wechat_article",
                        payload={
                            "generator": "wechat_article",
                            "opportunity_id": 1,
                            "title": "T",
                            "body": "B",
                            "metadata": {},
                        },
                    )
                )

        _asyncio.get_event_loop().run_until_complete(_seed())

        # Mix of one known id + two unknown — endpoint should report the
        # known one, silently drop the unknowns.
        r = client.post(
            "/api/internal/content/batch_publish",
            json={"notification_ids": [1, 999, 1000]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["requested"] == 3
        # One result returned for the known id.
        assert len(body["results"]) == 1
        assert body["results"][0]["channel"] == "wechat_article"