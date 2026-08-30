"""Phase 21 — unit tests for the unified ``app.api.deps.require_admin``.

Strategy: build a tiny FastAPI app with one ``GET /whoami`` endpoint
that depends on ``require_admin`` and returns the actor label, then
exercise every header / setting combination. Settings are injected via
``app.dependency_overrides[get_settings]`` — same pattern as
``test_admin_api.py::_override_settings``.

The test suite's conftest clears ``APP_SECRET_KEY`` and
``RADAR_WEBHOOK_SECRET`` at import time, so we re-set the env var per
test when we want the webhook path exercised.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_admin
from app.config import get_settings

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test fixture app
# ---------------------------------------------------------------------------
WEBHOOK_SECRET = "test-webhook-secret-abc"
APP_SECRET_KEY = "test-app-secret-xyz"
ADMIN_SECRET = "test-admin-secret-42"
ADMIN_OPEN_ID = "ou_admin_test_99"
NOT_ADMIN_OPEN_ID = "ou_user_random"


def make_app(*, admin_secret: str = ADMIN_SECRET,
             admin_open_ids: list[str] | None = None,
             app_secret_key: str = "") -> FastAPI:
    """Build a 1-endpoint app whose auth depends on require_admin."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(actor: str = Depends(require_admin)) -> dict[str, str]:
        return {"actor": actor}

    class _Settings:
        def __init__(self) -> None:
            self.app_secret_key = app_secret_key
            self.admin_api_secret = admin_secret
            self.admin_open_ids = list(admin_open_ids or [])

    app.dependency_overrides[get_settings] = lambda: _Settings()
    return app


@pytest.fixture
def restore_webhook_env() -> Iterator[None]:
    """Save + restore RADAR_WEBHOOK_SECRET so env mutations don't leak."""
    original = os.environ.get("RADAR_WEBHOOK_SECRET", "")
    yield
    if original:
        os.environ["RADAR_WEBHOOK_SECRET"] = original
    else:
        os.environ.pop("RADAR_WEBHOOK_SECRET", None)


# ---------------------------------------------------------------------------
# Header passing
# ---------------------------------------------------------------------------
class TestAcceptedHeaders:
    async def test_valid_webhook_header_returns_webhook_label(
        self, restore_webhook_env
    ):
        os.environ["RADAR_WEBHOOK_SECRET"] = WEBHOOK_SECRET
        app = make_app(app_secret_key="")
        client = TestClient(app)

        resp = client.get(
            "/whoami", headers={"X-Radar-Webhook": WEBHOOK_SECRET}
        )
        assert resp.status_code == 200
        assert resp.json() == {"actor": "webhook"}

    async def test_valid_admin_secret_returns_secret_label(self):
        app = make_app()
        client = TestClient(app)

        resp = client.get(
            "/whoami", headers={"X-Radar-Admin-Secret": ADMIN_SECRET}
        )
        assert resp.status_code == 200
        assert resp.json() == {"actor": "secret"}

    async def test_feishu_open_id_returns_open_id(self):
        app = make_app(admin_open_ids=[ADMIN_OPEN_ID])
        client = TestClient(app)

        resp = client.get(
            "/whoami", headers={"X-Feishu-Open-Id": ADMIN_OPEN_ID}
        )
        assert resp.status_code == 200
        assert resp.json() == {"actor": ADMIN_OPEN_ID}


# ---------------------------------------------------------------------------
# 401 cases
# ---------------------------------------------------------------------------
class TestRejected:
    async def test_no_headers_returns_401(self):
        app = make_app()
        client = TestClient(app)

        resp = client.get("/whoami")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "admin credentials required"

    async def test_wrong_webhook_returns_401(self, restore_webhook_env):
        os.environ["RADAR_WEBHOOK_SECRET"] = WEBHOOK_SECRET
        app = make_app(app_secret_key="")
        client = TestClient(app)

        resp = client.get(
            "/whoami", headers={"X-Radar-Webhook": "definitely-wrong"}
        )
        assert resp.status_code == 401

    async def test_wrong_admin_secret_returns_401(self):
        app = make_app()
        client = TestClient(app)

        resp = client.get(
            "/whoami", headers={"X-Radar-Admin-Secret": "nope"}
        )
        assert resp.status_code == 401

    async def test_unknown_open_id_returns_401(self):
        app = make_app(admin_open_ids=[ADMIN_OPEN_ID])
        client = TestClient(app)

        resp = client.get(
            "/whoami", headers={"X-Feishu-Open-Id": NOT_ADMIN_OPEN_ID}
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Dev short-circuit + precedence
# ---------------------------------------------------------------------------
class TestDevShortCircuit:
    async def test_all_settings_empty_accepts_any_caller(self):
        """If admin_open_ids + admin_api_secret + webhook_expected are
        all empty/unset, dev mode accepts every caller (matches legacy
        _require_webhook behaviour that conftest relies on)."""
        app = make_app(admin_secret="", admin_open_ids=[], app_secret_key="")
        # Make sure the env var isn't leaking in.
        os.environ.pop("RADAR_WEBHOOK_SECRET", None)
        client = TestClient(app)

        # No headers → still 200.
        resp = client.get("/whoami")
        assert resp.status_code == 200
        assert resp.json()["actor"] == "webhook"

        # Random headers → still 200.
        resp = client.get("/whoami", headers={"X-Radar-Webhook": "anything"})
        assert resp.status_code == 200

    async def test_feishu_wins_over_webhook_when_both_pass(
        self, restore_webhook_env
    ):
        """When both Feishu open_id and webhook are valid, Feishu
        wins — gives audit rows the strongest actor label."""
        os.environ["RADAR_WEBHOOK_SECRET"] = WEBHOOK_SECRET
        app = make_app(
            app_secret_key="", admin_open_ids=[ADMIN_OPEN_ID]
        )
        client = TestClient(app)

        resp = client.get(
            "/whoami",
            headers={
                "X-Feishu-Open-Id": ADMIN_OPEN_ID,
                "X-Radar-Webhook": WEBHOOK_SECRET,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["actor"] == ADMIN_OPEN_ID

    async def test_admin_secret_wins_over_webhook_when_both_pass(
        self, restore_webhook_env
    ):
        os.environ["RADAR_WEBHOOK_SECRET"] = WEBHOOK_SECRET
        app = make_app(app_secret_key="")
        client = TestClient(app)

        resp = client.get(
            "/whoami",
            headers={
                "X-Radar-Admin-Secret": ADMIN_SECRET,
                "X-Radar-Webhook": WEBHOOK_SECRET,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["actor"] == "secret"


# ---------------------------------------------------------------------------
# Two-source webhook — Phase 27 fix: RADAR_WEBHOOK_SECRET must work
# alongside APP_SECRET_KEY (n8n injects the former via docker-compose).
# ---------------------------------------------------------------------------
class TestWebhookTwoSources:
    async def test_app_secret_key_only(
        self, restore_webhook_env
    ) -> None:
        """Only APP_SECRET_KEY configured — webhook header that matches
        it must be accepted."""
        os.environ.pop("RADAR_WEBHOOK_SECRET", None)
        app = make_app(
            admin_secret="", admin_open_ids=[], app_secret_key=APP_SECRET_KEY  # type: ignore[arg-type]
        )
        client = TestClient(app)
        resp = client.get(
            "/whoami", headers={"X-Radar-Webhook": APP_SECRET_KEY}
        )
        assert resp.status_code == 200
        assert resp.json() == {"actor": "webhook"}

    async def test_radar_webhook_secret_only(
        self, restore_webhook_env
    ) -> None:
        """Only RADAR_WEBHOOK_SECRET configured — header that matches it
        must be accepted (this is what n8n uses)."""
        os.environ["RADAR_WEBHOOK_SECRET"] = APP_SECRET_KEY  # reuse the literal
        app = make_app(
            admin_secret="", admin_open_ids=[], app_secret_key=""
        )
        client = TestClient(app)
        resp = client.get(
            "/whoami", headers={"X-Radar-Webhook": APP_SECRET_KEY}
        )
        assert resp.status_code == 200
        assert resp.json() == {"actor": "webhook"}

    async def test_both_configured_either_works(
        self, restore_webhook_env
    ) -> None:
        """Both APP_SECRET_KEY and RADAR_WEBHOOK_SECRET configured —
        the client may send either value."""
        os.environ["RADAR_WEBHOOK_SECRET"] = APP_SECRET_KEY
        app = make_app(
            admin_secret="", admin_open_ids=[],
            app_secret_key=APP_SECRET_KEY,  # type: ignore[arg-type]
        )
        client = TestClient(app)

        # Send the APP_SECRET_KEY value → accepted.
        resp = client.get(
            "/whoami", headers={"X-Radar-Webhook": APP_SECRET_KEY}
        )
        assert resp.status_code == 200
        assert resp.json() == {"actor": "webhook"}

    async def test_both_configured_different_values_both_work(
        self, restore_webhook_env
    ) -> None:
        """Both configured with *different* values — each must match
        its own source."""
        os.environ["RADAR_WEBHOOK_SECRET"] = WEBHOOK_SECRET
        app = make_app(
            admin_secret="", admin_open_ids=[],
            app_secret_key=APP_SECRET_KEY,  # type: ignore[arg-type]
        )
        client = TestClient(app)

        # Send APP_SECRET_KEY value → accepted.
        resp = client.get(
            "/whoami", headers={"X-Radar-Webhook": APP_SECRET_KEY}
        )
        assert resp.status_code == 200

        # Send RADAR_WEBHOOK_SECRET value → also accepted.
        resp = client.get(
            "/whoami", headers={"X-Radar-Webhook": WEBHOOK_SECRET}
        )
        assert resp.status_code == 200

        # Send a totally wrong value → rejected.
        resp = client.get(
            "/whoami", headers={"X-Radar-Webhook": "neither-match"}
        )
        assert resp.status_code == 401
