"""Deterministic Feishu provider mock — used by tests + offline dev.

Records every `send_card` call in an in-memory deque. The `should_fail`
flag injects an `ExternalServiceError` so error paths can be exercised
without spinning up a real server.

This mirrors `MockTelegramProvider` so the test patterns stay uniform
across notification channels.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional

from app.services.feishu.base import (
    FeishuCard,
    FeishuProvider,
    FeishuSendResult,
)
from app.utils import ExternalServiceError


class MockFeishuProvider(FeishuProvider):
    """Captures `send_card` calls. Deterministic — no time-based ids."""

    name = "mock-feishu"

    def __init__(
        self,
        *,
        max_history: int = 1000,
        should_fail: bool = False,
        failure_message: str = "synthetic_failure",
    ) -> None:
        self._sent: deque[FeishuCard] = deque(maxlen=max_history)
        self._should_fail = should_fail
        self._failure_message = failure_message

    @property
    def sent(self) -> list[FeishuCard]:
        """Snapshot of every card sent so far (oldest first)."""
        return list(self._sent)

    def reset(self) -> None:
        """Clear recorded sends. Useful between tests."""
        self._sent.clear()
        self._should_fail = False

    def set_should_fail(self, value: bool, *, message: str = "synthetic_failure") -> None:
        """Flip the failure-injection switch mid-test."""
        self._should_fail = value
        self._failure_message = message

    async def send_card(self, card: FeishuCard) -> FeishuSendResult:
        if self._should_fail:
            raise ExternalServiceError(
                f"mock_feishu:{self._failure_message}", provider=self.name
            )
        self._sent.append(card)
        body_chars = _approx_body_chars(card.body)
        return FeishuSendResult(
            ok=True,
            title=card.title or "",
            body_chars=body_chars,
            provider=self.name,
            response={"data": {"mock": True}},
        )


def _approx_body_chars(body: Any) -> int:
    """Approximate the JSON-encoded size of a card body without importing json.

    Used for the `body_chars` field of the result — a rough-but-cheap
    way for callers to know how much they pushed.
    """
    # Lazy import — `json` is heavy in cold-start paths.
    import json

    return len(json.dumps(body, ensure_ascii=False))


__all__ = ["MockFeishuProvider"]