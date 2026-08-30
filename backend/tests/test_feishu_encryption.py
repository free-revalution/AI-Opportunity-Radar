"""Tests for Phase 25 F.1 (Feishu AES-256-CBC decryption) + F.2 (Redis
SETNX event idempotency).

Both helpers live in ``app.services.feishu.inbound``. The tests run
fully offline — no DB / Redis / Feishu.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from app.config import Settings, get_settings
from app.services.feishu.inbound import (
    FeishuDecryptError,
    _decrypt_feishu_envelope,
    _event_already_processed,
    parse_event,
)


# ===========================================================================
# F.1 — Feishu AES-256-CBC decrypt
# ===========================================================================
def _make_encrypted_envelope(plaintext_obj: dict, encrypt_key: str) -> str:
    """Build a real Feishu-style ``{"encrypt": "..."}`` payload."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7
    from cryptography.hazmat.primitives import padding as _padding

    plaintext = json.dumps(plaintext_obj).encode("utf-8")
    padder = _padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    # Use a deterministic IV so the test is reproducible.
    iv = b"\x00" * 16
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    body_bytes = encryptor.update(padded) + encryptor.finalize()
    ciphertext = iv + body_bytes
    return base64.b64encode(ciphertext).decode("ascii")


def test_decrypt_envelope_round_trip():
    """Real AES-256-CBC round-trip — produces the original dict."""
    settings = Settings(feishu_encrypt_key="test-encrypt-key")
    plaintext = {"header": {"event_type": "im.message.receive_v1",
                             "event_id": "evt_42"}, "event": {}}
    envelope = _make_encrypted_envelope(plaintext, "test-encrypt-key")

    decrypted = _decrypt_feishu_envelope(envelope, settings=settings)
    assert decrypted == plaintext


def test_decrypt_envelope_raises_without_key():
    settings = Settings(feishu_encrypt_key="")
    with pytest.raises(FeishuDecryptError, match="not configured"):
        _decrypt_feishu_envelope("anything", settings=settings)


def test_decrypt_envelope_raises_on_bad_base64():
    settings = Settings(feishu_encrypt_key="k")
    with pytest.raises(FeishuDecryptError, match="invalid base64"):
        _decrypt_feishu_envelope("not!valid!base64!", settings=settings)


def test_decrypt_envelope_raises_on_wrong_key():
    """Wrong encrypt key → padding error → FeishuDecryptError."""
    plaintext = {"hello": "world"}
    envelope = _make_encrypted_envelope(plaintext, "correct-key")
    settings = Settings(feishu_encrypt_key="wrong-key")
    with pytest.raises(FeishuDecryptError):
        _decrypt_feishu_envelope(envelope, settings=settings)


def test_parse_event_decrypts_encrypted_envelope():
    """`parse_event` returns the inner event when body has ``encrypt``."""
    settings = Settings(feishu_encrypt_key="k")
    inner = {
        "header": {"event_type": "im.message.receive_v1",
                   "event_id": "evt_99"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_x"}},
            "message": {
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": '{"text": "/help"}',
            },
        },
    }
    body = {"encrypt": _make_encrypted_envelope(inner, "k")}

    parsed = parse_event(body, settings=settings)
    # Should be a FeishuEvent with the decrypted contents.
    from app.services.feishu.inbound import FeishuEvent

    assert isinstance(parsed, FeishuEvent)
    assert parsed.text == "/help"
    assert parsed.sender_open_id == "ou_x"


def test_parse_event_returns_none_on_decrypt_failure():
    """Bad key → parse_event returns None (caller acks Feishu)."""
    body = {"encrypt": "AAAA"}  # too short
    settings = Settings(feishu_encrypt_key="k")
    assert parse_event(body, settings=settings) is None


# ===========================================================================
# F.2 — Redis SETNX event idempotency
# ===========================================================================
class _FakeRedis:
    """Minimal fake of redis.asyncio for SET NX EX testing."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


async def test_event_already_processed_first_seen_returns_false():
    redis = _FakeRedis()
    already = await _event_already_processed("evt_1", redis_client=redis)
    assert already is False
    assert "radar:feishu:event:evt_1" in redis.store


async def test_event_already_processed_second_call_returns_true():
    redis = _FakeRedis()
    await _event_already_processed("evt_2", redis_client=redis)
    again = await _event_already_processed("evt_2", redis_client=redis)
    assert again is True


async def test_event_already_processed_empty_id_is_noop():
    redis = _FakeRedis()
    already = await _event_already_processed("", redis_client=redis)
    assert already is False
    assert redis.store == {}


async def test_event_already_processed_none_redis_fails_open():
    """When Redis is None, treat every event as first-seen (no dedup)."""
    already = await _event_already_processed("evt_3", redis_client=None)
    assert already is False


async def test_event_already_processed_redis_error_fails_open():
    class _BrokenRedis:
        async def set(self, *_args, **_kwargs):
            raise ConnectionError("redis down")

    already = await _event_already_processed(
        "evt_4", redis_client=_BrokenRedis()
    )
    assert already is False