"""Tests for the readiness probe (`/api/health/ready`)."""

from __future__ import annotations

import pytest

from app.api import readiness as readiness_module


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patch(monkeypatch, *, pg, redis) -> None:
    """Patch the helpers the readiness module imported at call-time."""
    async def fake_pg(session):
        return pg

    async def fake_redis(client=None):
        return redis

    monkeypatch.setattr(readiness_module, "_check_postgres", fake_pg)
    monkeypatch.setattr(readiness_module, "_check_redis_with_client", fake_redis)


def test_readiness_200_when_postgres_and_redis_healthy(client, monkeypatch) -> None:
    _patch(
        monkeypatch,
        pg={"status": "healthy"},
        redis={"status": "healthy"},
    )

    response = client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["components"]) == {"postgres", "redis"}


def test_readiness_503_when_postgres_down(client, monkeypatch) -> None:
    _patch(
        monkeypatch,
        pg={"status": "down", "error": "connection refused"},
        redis={"status": "healthy"},
    )

    response = client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["components"]["postgres"]["status"] == "down"


def test_readiness_503_when_redis_down(client, monkeypatch) -> None:
    _patch(
        monkeypatch,
        pg={"status": "healthy"},
        redis={"status": "down", "error": "eof"},
    )

    response = client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["components"]["redis"]["status"] == "down"


def test_readiness_503_when_postgres_degraded(client, monkeypatch) -> None:
    """Strict: even `degraded` is not ready (no degraded-allowed path)."""
    _patch(
        monkeypatch,
        pg={"status": "degraded", "note": "slow"},
        redis={"status": "healthy"},
    )

    response = client.get("/api/health/ready")
    assert response.status_code == 503
