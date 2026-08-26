"""Deterministic Telegram provider mock — used by tests + offline dev.

Records every send in an in-memory list. Safe to use across requests
within a single process; not safe across worker restarts.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

from app.services.notification.telegram import (
    TelegramMessage,
    TelegramProvider,
    TelegramSendResult,
)


class MockTelegramProvider(TelegramProvider):
    """Captures `send_message` calls. Deterministic — no time-based ids."""

    name = "mock-telegram"

    def __init__(self, *, max_history: int = 1000, should_fail: bool = False) -> None:
        self._sent: deque[TelegramMessage] = deque(maxlen=max_history)
        self._should_fail = should_fail
        self._counter = 0

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str = "MarkdownV2",
        link_preview: bool = False,
    ) -> TelegramSendResult:
        if self._should_fail:
            return TelegramSendResult(
                ok=False,
                chat_id=chat_id,
                text_chars=len(text or ""),
                provider=self.name,
                error="synthetic_failure",
            )
        self._counter += 1
        msg = TelegramMessage(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            link_preview_options={"disable_web_page_preview": not link_preview},
            message_id=f"mock-{self._counter}",
        )
        self._sent.append(msg)
        return TelegramSendResult(
            ok=True,
            chat_id=chat_id,
            text_chars=len(text or ""),
            provider=self.name,
            message_id=msg.message_id,
        )

    # ------------------------------------------------------------------
    # inspection helpers (used by tests, not by the service)
    # ------------------------------------------------------------------
    @property
    def sent(self) -> list[TelegramMessage]:
        return list(self._sent)

    def clear(self) -> None:
        self._sent.clear()
        self._counter = 0


__all__ = ["MockTelegramProvider"]
