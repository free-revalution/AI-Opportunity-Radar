"""Tests for the error hierarchy + HTTP status mapping."""

from __future__ import annotations

from app.utils.errors import (
    AppError,
    AuthenticationError,
    ExternalServiceError,
    RateLimitError,
    RetryableError,
    TimeoutError_,
    ValidationError,
)


def test_default_http_status_per_error_type() -> None:
    assert AppError("x").http_status == 500
    assert ValidationError("x").http_status == 422
    assert AuthenticationError("x").http_status == 401
    assert RateLimitError("x").http_status == 429
    assert TimeoutError_("x").http_status == 504
    assert ExternalServiceError("x").http_status == 502
    assert RetryableError("x").http_status == 503


def test_retryable_marker() -> None:
    assert RetryableError("x").retryable is True
    assert RateLimitError("x").retryable is True
    assert TimeoutError_("x").retryable is True
    assert ExternalServiceError("x").retryable is True
    assert ValidationError("x").retryable is False
    assert AuthenticationError("x").retryable is False


def test_context_is_preserved() -> None:
    err = ExternalServiceError("firecrawl failed", provider="firecrawl", code=500)
    assert err.context == {"provider": "firecrawl", "code": 500}
    assert "firecrawl failed" in str(err)


def test_inheritance_chain() -> None:
    err = ExternalServiceError("x")
    assert isinstance(err, AppError)
    assert isinstance(err, Exception)