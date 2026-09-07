"""Phase 36+ — unhandled exception handler.

FastAPI's default 500 response is ``{"detail": "Internal Server Error"}``
with no exception context — useless for ops debugging. We override the
exception handler so:

  * prod: still returns generic ``Internal Server Error`` (no leak).
  * dev / staging: returns ``error: "<ExcType>: <first line>"`` +
    ``trace_tail`` (last 12 traceback lines) so the Feishu bot reply
    carries the actual failure without the operator needing to ssh
    in and `docker logs | grep`.

Tests mount a temporary router with one route that always raises,
then assert on the JSON response body in both ``APP_ENV`` modes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def app_with_raising_route():
    """Build a tiny app that registers a route which raises."""

    def _factory() -> FastAPI:
        # — Use create_app() so we get the real exception_handler
        # installed, then attach one extra test-only route.
        app = create_app()
        router = APIRouter()

        @router.get("/__test_boom")
        async def _boom() -> dict[str, Any]:
            raise ValueError("simulated upstream DB connection lost")

        app.include_router(router)
        return app

    return _factory


def test_unhandled_exception_in_dev_returns_exception_type_and_message(
    monkeypatch, app_with_raising_route
) -> None:
    """dev mode: bot should see ``error: ValueError: simulated upstream...``."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "dev")

    client = TestClient(app_with_raising_route(), raise_server_exceptions=False)
    resp = client.get("/__test_boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal Server Error"
    assert "ValueError" in body["error"]
    assert "simulated upstream DB connection lost" in body["error"]
    assert "trace_tail" in body
    assert isinstance(body["trace_tail"], list)
    # — Should contain at least the raise line
    joined = "\n".join(body["trace_tail"])
    assert "ValueError" in joined


def test_unhandled_exception_in_prod_returns_generic_detail(
    monkeypatch, app_with_raising_route
) -> None:
    """prod mode: bot only sees ``Internal Server Error`` — no exception leak."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "prod")

    client = TestClient(app_with_raising_route(), raise_server_exceptions=False)
    resp = client.get("/__test_boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body == {"detail": "Internal Server Error"}
    # — Must NOT contain leak markers
    assert "error" not in body
    assert "trace_tail" not in body
