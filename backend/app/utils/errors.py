"""Unified exception hierarchy.

External service failures must NOT crash the system. They raise one of these
typed errors, which the API / workers translate into structured responses
and (where appropriate) retries with exponential backoff.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application errors."""

    http_status: int = 500
    code: str = "app_error"
    retryable: bool = False

    def __init__(self, message: str = "", *, retryable: bool | None = None, **context: object) -> None:
        super().__init__(message or self.code)
        # Default to the class-level marker so subclasses that set
        # `retryable = True` keep that behaviour.
        self.retryable = type(self).retryable if retryable is None else retryable
        self.message = message or self.code
        self.context = context


class ValidationError(AppError):
    http_status = 422
    code = "validation_error"


class AuthenticationError(AppError):
    http_status = 401
    code = "authentication_error"


class RateLimitError(AppError):
    http_status = 429
    code = "rate_limit_error"
    retryable = True


class TimeoutError_(AppError):
    http_status = 504
    code = "timeout_error"
    retryable = True


class ExternalServiceError(AppError):
    """Wraps failures from third-party services (Firecrawl, Browser Use, LLM, ...)."""

    http_status = 502
    code = "external_service_error"
    retryable = True


class RetryableError(AppError):
    """Marker for transient errors that should be retried."""

    http_status = 503
    code = "retryable_error"
    retryable = True