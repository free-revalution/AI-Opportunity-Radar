"""Publisher infrastructure (Phase 11 v2.0).

Why this module exists: Content Center generates sale copy for 4 channels
but the operator still has to manually copy-paste into each platform's
editor. Phase 11 lays the infrastructure for "one-click publish" — every
platform gets a `Publisher` subclass that owns its own auth flow + API
client. Real publishing credentials are operator-supplied (per-platform
env vars), so out of the box each platform returns a `PublishResult`
that says "skipped: credentials not configured".

Each publisher owns ONE channel:

  * `WechatMPPublisher`      — channel "wechat_article"
  * `XiaohongshuPublisher`   — channel "xiaohongshu" (蒲公英 API)
  * `XianyuPublisher`        — channel "xianyu"
  * `FeishuBotPublisher`     — channel "feishu" (the existing path)

The registry maps `channel → publisher` so callers just say
`publish(channel, piece)` and the right subclass handles the rest.

Public surface:

  * `Publisher` — ABC with `name`, `channel`, `async publish(piece) -> PublishResult`,
    and `is_configured()`.
  * `PublishResult` — dataclass: `success: bool`, `external_id: str | None`,
    `external_url: str | None`, `error: str | None`.
  * `get_publisher(channel)` — registry lookup, raises `KeyError`.
  * `publish_piece(channel, piece)` — convenience wrapper.
  * `batch_publish(pieces)` — fan-out; never aborts on one failure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public DTOs
# ---------------------------------------------------------------------------
@dataclass
class PublishResult:
    """Outcome of a single publish attempt.

    `external_id` / `external_url` are populated by the publisher when
    the platform returns a post / article / product ID. `skipped` is
    True when the publisher deliberately did nothing (e.g. credentials
    not configured) — distinguishes from `success=False, error="..."`
    which represents a real failure.
    """

    publisher: str
    channel: str
    success: bool
    skipped: bool = False
    external_id: str | None = None
    external_url: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------
class Publisher(ABC):
    """Abstract base. Concrete publishers live in sibling modules."""

    name: str = "abstract"
    channel: str = "abstract"

    def is_configured(self) -> bool:
        """Return True iff the platform's credentials are present.

        Subclasses MUST check the env / settings object — a publisher
        that returns `True` for an unconfigured platform will surface
        confusing 401s to the operator. Default returns False so a
        newly added subclass doesn't silently fail-open.
        """
        return False

    @abstractmethod
    async def publish(self, piece: Mapping[str, Any]) -> PublishResult:
        """Publish a single piece of generated content.

        `piece` is the raw notification payload (the same shape
        `ContentCenter` exposes) — keys: title, body, metadata,
        channel, generator, format, opportunity_id.
        """


# ---------------------------------------------------------------------------
# Concrete publishers — all start as stubs.
# Each one documents what real credentials it needs and returns
# "skipped" when those env vars are absent.
# ---------------------------------------------------------------------------
class _StubPublisher(Publisher):
    """Common implementation for "credentials not configured" stubs.

    Subclasses override `name`, `channel`, `required_env_vars` and
    optionally `publish()` if they have a working implementation. The
    base class returns a clean skipped envelope."""

    name: str = "stub"
    channel: str = "stub"
    required_env_vars: tuple[str, ...] = ()

    def is_configured(self) -> bool:
        import os

        return all(bool(os.environ.get(v)) for v in self.required_env_vars)

    async def publish(self, piece: Mapping[str, Any]) -> PublishResult:
        if not self.is_configured():
            logger.info(
                "publisher_skipped_no_credentials",
                publisher=self.name,
                channel=self.channel,
                required=list(self.required_env_vars),
            )
            return PublishResult(
                publisher=self.name,
                channel=self.channel,
                success=False,
                skipped=True,
                error=(
                    f"未配置凭据,需要设置环境变量: {', '.join(self.required_env_vars)}"
                ),
            )
        # Subclass with credentials should override `publish()` —
        # default impl raises so a half-wired subclass can't silently
        # no-op.
        raise NotImplementedError(
            f"{self.name}.publish() 未实现 — 配置完凭据后请覆盖 publish()"
        )


class WechatMPPublisher(_StubPublisher):
    """微信公众号 publisher (文章草稿箱 / 发布).

    Credentials required (env vars):
      * `WECHAT_MP_APP_ID`
      * `WECHAT_MP_APP_SECRET`

    Real implementation uses the official 微信公众平台 API:
      POST /cgi-bin/token   → access_token
      POST /cgi-bin/draft/add  → media_id (article draft)
      POST /cgi-bin/freepublish/submit → publish_id

    Phase 11 ships the stub — the operator still has to apply for the
    微信公众平台 API 权限 (which requires a verified 服务号). Until then
    the publish endpoint returns skipped with a friendly error.
    """

    name = "wechat_mp"
    channel = "wechat_article"
    required_env_vars = ("WECHAT_MP_APP_ID", "WECHAT_MP_APP_SECRET")


class XiaohongshuPublisher(_StubPublisher):
    """小红书 蒲公英 publisher.

    Credentials required:
      * `XHS_PGY_TOKEN` — 蒲公英平台 API token
      * `XHS_USER_ID`   — the 蒲公英 user id

    Real implementation posts to 蒲公英's open API:
      POST /api/open/v1/note/create

    Phase 11 ships the stub — 蒲公英 access is gated by 蒲公英 partner
    agreement (manual application).
    """

    name = "xiaohongshu_pgy"
    channel = "xiaohongshu"
    required_env_vars = ("XHS_PGY_TOKEN", "XHS_USER_ID")


class XianyuPublisher(_StubPublisher):
    """闲鱼 商品 publisher.

    Credentials required:
      * `XIANYU_APP_KEY`
      * `XIANYU_APP_SECRET`
      * `XIANYU_ACCESS_TOKEN`

    Real implementation uses 闲鱼开放平台:
      POST /api/v1/item/publish

    Phase 11 ships the stub — 闲鱼 开放平台 is invite-only.
    """

    name = "xianyu_open"
    channel = "xianyu"
    required_env_vars = (
        "XIANYU_APP_KEY",
        "XIANYU_APP_SECRET",
        "XIANYU_ACCESS_TOKEN",
    )


class FeishuBotPublisher(_StubPublisher):
    """飞书 bot publisher.

    Credentials required:
      * `FEISHU_APP_ID`
      * `FEISHU_APP_SECRET`

    Real implementation sends to existing `app/services/feishu/bot.py`
    which uses the app's tenant_access_token. The Phase 6 Feishu App
    inbound + outbound work covers this — Phase 11 just exposes it as
    a publisher so it goes through the same interface as the others.
    """

    name = "feishu_bot"
    channel = "feishu"
    required_env_vars = ("FEISHU_APP_ID", "FEISHU_APP_SECRET")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_PUBLISHERS: tuple[Publisher, ...] = (
    FeishuBotPublisher(),
    XianyuPublisher(),
    XiaohongshuPublisher(),
    WechatMPPublisher(),
)


def get_publisher(channel: str) -> Publisher:
    """Look up the publisher for `channel`. Raises `KeyError` if no
    publisher owns that channel — surfaces a 422 to the operator."""
    for p in _PUBLISHERS:
        if p.channel == channel:
            return p
    raise KeyError(f"no publisher registered for channel {channel!r}")


def channels() -> tuple[str, ...]:
    """Channels that have a registered publisher (for the /publish UI)."""
    return tuple(p.channel for p in _PUBLISHERS)


def is_channel_supported(channel: str) -> bool:
    return any(p.channel == channel for p in _PUBLISHERS)


async def publish_piece(channel: str, piece: Mapping[str, Any]) -> PublishResult:
    """Convenience wrapper. Same as `await get_publisher(channel).publish(piece)`."""
    return await get_publisher(channel).publish(piece)


async def batch_publish(
    pieces: list[tuple[str, Mapping[str, Any]]],
) -> list[PublishResult]:
    """Publish a list of (channel, piece) pairs. One failure does NOT
    abort the batch — every result is reported."""
    results: list[PublishResult] = []
    for channel, piece in pieces:
        try:
            publisher = get_publisher(channel)
        except KeyError as exc:
            results.append(
                PublishResult(
                    publisher="unknown",
                    channel=channel,
                    success=False,
                    skipped=False,
                    error=str(exc),
                )
            )
            continue
        try:
            results.append(await publisher.publish(piece))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "publisher_unhandled_exception",
                publisher=publisher.name,
                channel=channel,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            results.append(
                PublishResult(
                    publisher=publisher.name,
                    channel=channel,
                    success=False,
                    skipped=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return results


__all__ = [
    "Publisher",
    "PublishResult",
    "WechatMPPublisher",
    "XiaohongshuPublisher",
    "XianyuPublisher",
    "FeishuBotPublisher",
    "get_publisher",
    "channels",
    "is_channel_supported",
    "publish_piece",
    "batch_publish",
]