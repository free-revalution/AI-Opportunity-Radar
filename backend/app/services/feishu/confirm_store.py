"""Phase 26 — Redis-backed one-shot pending-action store.

Destroying a Feishu cloud document is irreversible. To prevent
"typo in `/docs rm` deletes the wrong thing", the bot answers
``/docs rm <name>`` with a 12-char confirmation token and only
executes the actual delete when the user re-sends
``/docs confirm <token>``. This module owns the lifecycle of those
tokens:

  * :meth:`ConfirmStore.create` — atomic SETEX (TTL 60s by default)
  * :meth:`ConfirmStore.consume` — atomic GETDEL — read **and**
    delete, so a token can only be used once. The atomicity
    prevents replay attacks ("operator fat-fingered the token,
    tried again, deleted again").
  * :meth:`ConfirmStore.peek` — GET only — used by ``/docs info``
    to render the pending payload before confirming.

Fail-open semantics
-------------------

When ``redis_client`` is ``None`` (Redis unreachable / not
configured) the store:

  * rejects new pending actions with :class:`ConfirmStoreUnavailable`
    so destructive operations are an explicit failure mode — not a
    silent bypass.
  * returns ``None`` on read/consume paths so callers can treat
    missing tokens as "expired / never existed" rather than crash.

When Redis errors at runtime (network glitch, transient outage)
the store warns + returns ``None`` rather than propagating. Same
"fail open, but loudly" pattern the rest of the codebase uses for
Redis (see ``_event_already_processed`` in ``inbound.py``).

Test seam
---------

``set_confirm_store_for_tests(instance)`` lets unit tests pin a
preloaded fake. ``get_confirm_store(redis_client)`` is the canonical
factory — it returns the singleton bound to that client.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ConfirmStoreUnavailable(RuntimeError):
    """Raised when ``create()`` is called without a usable Redis."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
#: Allowed kinds — kept in a small set so ``peers`` can render
#: human-readable descriptions without re-deriving them.
KNOWN_KINDS: frozenset[str] = frozenset(
    {
        "drive_delete",
        "drive_mv",
        "drive_rename",
        "bitable_rm",
    }
)


@dataclass(slots=True)
class PendingAction:
    """A destructive action awaiting confirmation."""

    action_id: str
    kind: str
    payload: dict[str, Any]
    created_at: float
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, blob: str) -> "PendingAction":
        data = json.loads(blob)
        return cls(
            action_id=str(data.get("action_id") or ""),
            kind=str(data.get("kind") or ""),
            payload=dict(data.get("payload") or {}),
            created_at=float(data.get("created_at") or 0.0),
            expires_at=float(data.get("expires_at") or 0.0),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class ConfirmStore:
    """Thin wrapper around a Redis client with one-shot semantics."""

    KEY_PREFIX = "radar:feishu:confirm:"
    DEFAULT_TTL_SEC = 60

    def __init__(self, redis_client: Any, *, ttl_sec: int = DEFAULT_TTL_SEC) -> None:
        self._redis = redis_client
        self._ttl_sec = int(ttl_sec)

    @property
    def is_available(self) -> bool:
        """True when Redis is reachable — controls whether ``create`` raises."""
        return self._redis is not None

    async def create(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        ttl_sec: Optional[int] = None,
    ) -> PendingAction:
        """Persist a new pending action; return it with its token id.

        Raises :class:`ConfirmStoreUnavailable` if Redis is not wired
        in (fail-loud — destructive operations must not silently
        bypass confirmation when Redis is down).
        """
        if self._redis is None:
            raise ConfirmStoreUnavailable(
                "confirm store unavailable — destructive actions disabled "
                "(Redis not configured / unreachable)"
            )
        if kind not in KNOWN_KINDS:
            # — Defense-in-depth — the docs_commands layer should
            # never pass unknown kinds, but if a future caller does
            # we want a loud failure.
            raise ValueError(f"unknown pending-action kind: {kind!r}")
        now = time.time()
        ttl = int(ttl_sec) if ttl_sec is not None else self._ttl_sec
        action = PendingAction(
            action_id=uuid.uuid4().hex[:12],
            kind=kind,
            payload=dict(payload),
            created_at=now,
            expires_at=now + ttl,
        )
        key = self._key(action.action_id)
        try:
            ok = await self._redis.set(key, action.to_json(), ex=ttl)
        except Exception as exc:  # noqa: BLE001 — fail open, log loudly
            logger.warning(
                "feishu_confirm_store_set_failed",
                error=str(exc)[:200],
            )
            raise ConfirmStoreUnavailable(
                f"redis set failed: {exc}"
            ) from exc
        if not ok:
            # — redis-py returns ``True``/``False`` for SET; ``None``
            # for SETNX-collision scenarios. We use plain SET, so
            # only False means failure.
            raise ConfirmStoreUnavailable(
                "redis set returned falsy — pending action NOT stored"
            )
        logger.info(
            "feishu_confirm_store_created",
            action_id=action.action_id,
            kind=kind,
            ttl_sec=ttl,
        )
        return action

    async def peek(self, action_id: str) -> Optional[PendingAction]:
        """Read a pending action without consuming it."""
        if not action_id or self._redis is None:
            return None
        try:
            blob = await self._redis.get(self._key(action_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feishu_confirm_store_peek_failed",
                action_id=action_id[:12],
                error=str(exc)[:200],
            )
            return None
        if not blob:
            return None
        try:
            return PendingAction.from_json(blob)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "feishu_confirm_store_peek_decode_failed",
                action_id=action_id[:12],
                error=str(exc)[:200],
            )
            return None

    async def consume(self, action_id: str) -> Optional[PendingAction]:
        """Atomic read+delete — the canonical "use once" path.

        Returns the pending action if it was present; ``None`` if the
        token was missing, already consumed, or expired. Expired
        tokens are also returned as ``None`` (Redis evicts them
        automatically on TTL).
        """
        if not action_id or self._redis is None:
            return None
        key = self._key(action_id)
        try:
            # — GETDEL is atomic; redis-py >= 4.0 supports it. We
            # accept older redis-py too: fall back to GET+DEL on
            # AttributeError (the project pins redis>=4, so this is
            # belt-and-suspenders).
            try:
                blob = await self._redis.getdel(key)
            except AttributeError:
                async with self._redis.pipeline(transaction=True) as pipe:
                    pipe.get(key)
                    pipe.delete(key)
                    blob, _deleted = await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feishu_confirm_store_consume_failed",
                action_id=action_id[:12],
                error=str(exc)[:200],
            )
            return None
        if not blob:
            return None
        try:
            action = PendingAction.from_json(blob)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "feishu_confirm_store_consume_decode_failed",
                action_id=action_id[:12],
                error=str(exc)[:200],
            )
            return None
        logger.info(
            "feishu_confirm_store_consumed",
            action_id=action_id[:12],
            kind=action.kind,
        )
        return action

    def _key(self, action_id: str) -> str:
        return f"{self.KEY_PREFIX}{action_id}"


# ---------------------------------------------------------------------------
# Singleton + test seam
# ---------------------------------------------------------------------------
_STORE: Optional[ConfirmStore] = None


def get_confirm_store(redis_client: Any, *, ttl_sec: int = 60) -> ConfirmStore:
    """Return the singleton :class:`ConfirmStore` bound to ``redis_client``.

    Re-uses the existing singleton when its Redis client matches
    ``redis_client``; otherwise builds a new one. Tests use
    :func:`set_confirm_store_for_tests` to swap in a fake.
    """
    global _STORE
    if _STORE is not None and _STORE._redis is redis_client:
        return _STORE
    _STORE = ConfirmStore(redis_client=redis_client, ttl_sec=ttl_sec)
    return _STORE


def set_confirm_store_for_tests(instance: Optional[ConfirmStore]) -> None:
    """Replace the singleton (or reset to ``None``) — test-only."""
    global _STORE
    _STORE = instance


__all__ = [
    "ConfirmStore",
    "ConfirmStoreUnavailable",
    "KNOWN_KINDS",
    "PendingAction",
    "get_confirm_store",
    "set_confirm_store_for_tests",
]
