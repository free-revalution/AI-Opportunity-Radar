"""Tests for the live health probes added in Phase 11.

The `_check_firecrawl` and `_check_browser_use` helpers now issue a
real GET against the configured base URL when an API key is set, so the
dashboard can detect "key set but service unreachable". These tests
monkey-patch `app.api.health._probe_url` to avoid hitting the network.
"""

from __future__ import annotations

import pytest

from app.api import health as health_module


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    health_module.get_settings.cache_clear()
    yield
    health_module.get_settings.cache_clear()


def test_check_browser_use_healthy_when_probe_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    s = health_module.get_settings()
    monkeypatch.setattr(s, "browser_use_api_key", "bu_test_key", raising=False)
    monkeypatch.setattr(s, "browser_use_api_url", "https://api.browser-use.com", raising=False)
    monkeypatch.setattr(health_module, "_probe_url", lambda url, timeout=3.0: (True, ""))
    out = health_module._check_browser_use()
    assert out == {"status": "healthy"}


def test_check_browser_use_degraded_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = health_module.get_settings()
    monkeypatch.setattr(s, "browser_use_api_key", "bu_test_key", raising=False)
    monkeypatch.setattr(s, "browser_use_api_url", "https://api.browser-use.com", raising=False)
    monkeypatch.setattr(
        health_module,
        "_probe_url",
        lambda url, timeout=3.0: (False, "HTTP 502: bad gateway"),
    )
    out = health_module._check_browser_use()
    assert out["status"] == "degraded"
    assert out["note"] == "browser_use probe failed"
    assert "502" in out["detail"]


def test_check_browser_use_degraded_when_key_absent() -> None:
    s = health_module.get_settings()
    monkeypatch_key = ""
    # The conftest already sets `mock_external_services=true`; we don't
    # touch it here. Just ensure key absence short-circuits.
    import os

    saved = os.environ.get("BROWSER_USE_API_KEY")
    os.environ["BROWSER_USE_API_KEY"] = monkeypatch_key
    health_module.get_settings.cache_clear()
    try:
        out = health_module._check_browser_use()
        assert out["status"] == "degraded"
        assert "not set" in out["note"]
    finally:
        if saved is None:
            os.environ.pop("BROWSER_USE_API_KEY", None)
        else:
            os.environ["BROWSER_USE_API_KEY"] = saved
        health_module.get_settings.cache_clear()


def test_check_firecrawl_healthy_when_probe_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = health_module.get_settings()
    monkeypatch.setattr(s, "firecrawl_api_key", "fc_test_key", raising=False)
    monkeypatch.setattr(s, "firecrawl_api_url", "https://api.firecrawl.dev", raising=False)
    monkeypatch.setattr(health_module, "_probe_url", lambda url, timeout=3.0: (True, ""))
    out = health_module._check_firecrawl()
    assert out == {"status": "healthy"}


def test_check_firecrawl_degraded_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = health_module.get_settings()
    monkeypatch.setattr(s, "firecrawl_api_key", "fc_test_key", raising=False)
    monkeypatch.setattr(s, "firecrawl_api_url", "https://api.firecrawl.dev", raising=False)
    monkeypatch.setattr(
        health_module,
        "_probe_url",
        lambda url, timeout=3.0: (False, "connection refused"),
    )
    out = health_module._check_firecrawl()
    assert out["status"] == "degraded"
    assert out["note"] == "firecrawl probe failed"
    assert "refused" in out["detail"]


def test_probe_url_returns_false_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_probe_url` must never raise — it always returns a tuple."""

    def _broken_get(*_args, **_kwargs):
        raise RuntimeError("network unreachable")

    import httpx

    monkeypatch.setattr(httpx, "Client", _broken_get)
    ok, detail = health_module._probe_url("http://nope.test/healthz")
    assert ok is False
    assert "unreachable" in detail


def test_probe_url_returns_true_on_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_probe_url` returns True for any 2xx status code."""

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def get(self, _url: str):
            class _Resp:
                status_code = 200

            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    ok, detail = health_module._probe_url("http://example.test/healthz")
    assert ok is True
    assert detail == ""
