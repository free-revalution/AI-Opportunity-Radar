"""Fallback bot provider — primary → secondary automatic failover.

Product decision (Phase 6): when the primary provider (Feishu by
default) fails — webhook misconfigured, network error, transient API
issue — automatically retry the same message on a secondary channel
(Telegram by default). Operators keep an out-of-band signal channel
even when the primary is down.

Failure handling rules (per the Phase 6 design):

  * `primary.send(...)` returns `ok=False` → fall through to `secondary`
  * `primary.send(...)` raises `ExternalServiceError` → fall through
  * Both fail → return the SECOND's `BotSendResult` with `error` augmented
    to mention both failures (so the operator sees the full picture)
  * Retry count = 1 (no infinite loops) — same bug will recur on retry
    so one attempt is enough

Metrics:
  * `record_external_error(primary.name, ...)` ticks on primary failure
  * `record_external_error(secondary.name, ...)` ticks on secondary failure
  * The returned `BotSendResult.delivered_by` records which provider
    actually shipped the message (for `Notification.provider`).
"""

from __future__ import annotations

from typing import Optional

from app.metrics import record_external_error
from app.services.bots.base import (
    BotMessage,
    BotProvider,
    BotSendResult,
)
from app.utils.errors import ExternalServiceError


class FallbackBotProvider(BotProvider):
    """Composes a primary + secondary provider. Falls back on failure."""

    name = "fallback"
    # — the channel of the primary; if even the secondary is on the same
    # channel (rare but legal — e.g. two Feishu webhooks for different
    # groups), the upper layer still wants to record this as "feishu".
    channel = ""

    def __init__(
        self,
        primary: BotProvider,
        secondary: Optional[BotProvider] = None,
    ) -> None:
        if secondary is None:
            raise ValueError("FallbackBotProvider requires a secondary provider")
        self._primary = primary
        self._secondary = secondary
        # — default `channel` follows the primary.
        self.channel = primary.channel

    @property
    def primary(self) -> BotProvider:
        return self._primary

    @property
    def secondary(self) -> BotProvider:
        return self._secondary

    async def send(self, *, target: str, message: BotMessage) -> BotSendResult:
        primary_result = await self._try_primary(target=target, message=message)
        if primary_result.ok:
            primary_result.delivered_by = self._primary.name
            return primary_result

        # Primary failed → try the secondary.
        secondary_result = await self._try_secondary(
            target=target, message=message, primary_error=primary_result.error or "unknown"
        )
        secondary_result.delivered_by = (
            self._secondary.name if secondary_result.ok else ""
        )
        # — augment error so the operator sees the full chain.
        if not secondary_result.ok:
            secondary_result.error = (
                f"primary={self._primary.name} failed[{primary_result.error}]; "
                f"secondary={self._secondary.name} failed[{secondary_result.error}]"
            )
        return secondary_result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _try_primary(
        self, *, target: str, message: BotMessage
    ) -> BotSendResult:
        try:
            return await self._primary.send(target=target, message=message)
        except ExternalServiceError as exc:
            record_external_error(
                provider=self._primary.name, kind=type(exc).__name__
            )
            return BotSendResult(
                ok=False,
                channel=self._primary.channel,
                provider=self._primary.name,
                target=target,
                body_chars=len(message.text or ""),
                error=f"primary_raised:{exc}",
            )
        except Exception as exc:  # noqa: BLE001 — translate anything else
            record_external_error(
                provider=self._primary.name, kind=type(exc).__name__
            )
            return BotSendResult(
                ok=False,
                channel=self._primary.channel,
                provider=self._primary.name,
                target=target,
                body_chars=len(message.text or ""),
                error=f"primary_raised:{type(exc).__name__}: {exc}",
            )

    async def _try_secondary(
        self,
        *,
        target: str,
        message: BotMessage,
        primary_error: str,
    ) -> BotSendResult:
        del primary_error  # — referenced via the augmented error in `send`
        try:
            return await self._secondary.send(target=target, message=message)
        except ExternalServiceError as exc:
            record_external_error(
                provider=self._secondary.name, kind=type(exc).__name__
            )
            return BotSendResult(
                ok=False,
                channel=self._secondary.channel,
                provider=self._secondary.name,
                target=target,
                body_chars=len(message.text or ""),
                error=f"secondary_raised:{exc}",
            )
        except Exception as exc:  # noqa: BLE001
            record_external_error(
                provider=self._secondary.name, kind=type(exc).__name__
            )
            return BotSendResult(
                ok=False,
                channel=self._secondary.channel,
                provider=self._secondary.name,
                target=target,
                body_chars=len(message.text or ""),
                error=f"secondary_raised:{type(exc).__name__}: {exc}",
            )


def build_bot_provider_with_fallback(
    settings,
    *,
    primary_channel: Optional[str] = None,
) -> BotProvider:
    """Convenience builder: a `FallbackBotProvider` wrapping primary +
    secondary channels per the configured fallback list.

    Reads:
      * `settings.notification_default_channel` — the primary
      * `settings.notification_fallback_channels` — ordered list of
        fallbacks (first element used as secondary)
    """
    from app.services.bots.factory import build_bot_provider

    primary = build_bot_provider(settings, channel=primary_channel)
    fallbacks = getattr(settings, "notification_fallback_channels", []) or []
    # Pick the first configured fallback; if none, return the primary
    # alone (no fallback wrapping — caller might prefer this in tests).
    if not fallbacks:
        return primary
    secondary = build_bot_provider(settings, channel=fallbacks[0])
    return FallbackBotProvider(primary=primary, secondary=secondary)


__all__ = [
    "FallbackBotProvider",
    "build_bot_provider_with_fallback",
]