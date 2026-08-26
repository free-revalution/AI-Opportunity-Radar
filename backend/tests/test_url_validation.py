"""SSRF / URL validation tests."""

from __future__ import annotations

import pytest

from app.utils.url_validation import SSRFError, assert_safe_url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1:8080/",
        "http://127.0.0.53:53/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # AWS / GCP metadata
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
        "",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(SSRFError):
        assert_safe_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "https://github.com/anthropics/anthropic-sdk-python",
        "https://news.ycombinator.com/item?id=1",
        "http://example.com/path?query=1",
    ],
)
def test_safe_urls_are_accepted(url: str) -> None:
    assert assert_safe_url(url) == url


def test_literal_private_ipv6_is_blocked() -> None:
    with pytest.raises(SSRFError):
        assert_safe_url("http://[::1]/")