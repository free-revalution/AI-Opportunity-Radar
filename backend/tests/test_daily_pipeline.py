"""End-to-end test for the MVP daily pipeline.

Per simplify §10: the daily cron runs

    discovery → clustering → scoring → screening → research → digest

Each step is an internal endpoint that records a PipelineReport. This
test exercises the full sequence against the TestClient + SQLite DB
and verifies a single ``Run`` row is created.

Runs fully offline (mock_external_services=True).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_daily_pipeline_end_to_end(client, sqlite_session, monkeypatch):
    """All 6 steps + digest — verifies Run row created with aggregated counts."""

    # Stub ResearchService.run_once so we don't actually scrape.
    from dataclasses import dataclass, field

    from app.services.research import ResearchService

    @dataclass
    class _FakeReport:
        raw_count: int = 0
        new_count: int = 0
        signal_count: int = 0
        errors: list = field(default_factory=list)

        def as_dict(self):
            return {
                "raw_count": self.raw_count,
                "new_count": self.new_count,
                "signal_count": self.signal_count,
                "errors": self.errors,
            }

    async def _fake_research(self):
        return _FakeReport()

    monkeypatch.setattr(ResearchService, "run_once", _fake_research)

    # 1. discovery
    r = client.post("/api/internal/discovery/run", json={"mock": True})
    assert r.status_code == 200, r.text

    # 2. clustering
    r = client.post("/api/internal/clustering/run", json={})
    assert r.status_code == 200, r.text

    # 3. scoring
    r = client.post("/api/internal/scoring/run", json={})
    assert r.status_code == 200, r.text

    # 4. screening
    r = client.post("/api/internal/screening/run", json={})
    assert r.status_code == 200, r.text

    # 5. research (stubbed)
    r = client.post("/api/internal/research/run", json={})
    assert r.status_code == 200, r.text

    # 6. — Final aggregated pipeline/run that records the Run row.
    r = client.post("/api/internal/pipeline/run", json={"send_digest": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["trigger"] == "manual"
    assert body["run_id"] >= 1
    assert body["finished_at"] is not None
    assert body["error"] is None


async def test_daily_pipeline_run_records_failure(client, monkeypatch):
    """If one step raises, the Run row is marked failed with the error."""
    from app.services.research import ResearchService

    async def _boom(self):
        raise RuntimeError("simulated research outage")

    monkeypatch.setattr(ResearchService, "run_once", _boom)

    # Starlette's TestClient has ``raise_server_exceptions=True`` by
    # default, so the endpoint's ``raise`` propagates out of
    # ``client.post`` instead of becoming a 500 response. The behaviour
    # we want to verify is two-fold:
    #
    #   1. the exception is propagated (so n8n / the bot sees a failure)
    #   2. the Run row has been marked failed with the error message
    with pytest.raises(RuntimeError, match="simulated research outage"):
        client.post(
            "/api/internal/pipeline/run",
            json={"send_digest": False},
        )

    # The Run row should still exist and be marked failed.
    r2 = client.get("/api/internal/status")
    assert r2.status_code == 200
    last = r2.json()["last_run"]
    assert last is not None
    assert last["status"] == "failed"
    assert "simulated research outage" in (last["error"] or "")


# ---------------------------------------------------------------------------
# Phase 28 regression — pipeline endpoint used to call
# ``outcome.get("delivered")`` on a DigestSendSummary dataclass, raising
# ``AttributeError: 'DigestSendSummary' object has no attribute 'get'``
# and 500-ing the whole /run flow. The docx block also referenced
# ``raw_count`` / ``signal_count`` (defined further down), so
# write_docx=True without send_digest crashed with NameError. These tests
# pin both regressions.
# ---------------------------------------------------------------------------
async def test_pipeline_run_with_send_digest_returns_200(client, monkeypatch):
    """send_digest=True must not AttributeError on the dataclass summary."""
    from app.services.notification.service import DigestSendSummary
    from app.services.research import ResearchService

    # Stub research so the run completes deterministically.
    async def _fake_research(self):
        from dataclasses import dataclass, field

        @dataclass
        class _R:
            raw_count: int = 0
            new_count: int = 0
            signal_count: int = 0
            errors: list = field(default_factory=list)

            def as_dict(self):
                return {
                    "raw_count": self.raw_count,
                    "new_count": self.new_count,
                    "signal_count": self.signal_count,
                    "errors": self.errors,
                }

        return _R()

    monkeypatch.setattr(ResearchService, "run_once", _fake_research)

    # Stub NotificationService.send_digest to return a synthetic
    # DigestSendSummary (notifications_delivered=1) without actually
    # pushing to Telegram / Feishu.
    from app.services.notification.service import NotificationService

    async def _fake_send_digest(self, **kwargs):
        return DigestSendSummary(
            notifications_attempted=1,
            notifications_delivered=1,
            notifications_failed=0,
            chat_id="test_chat",
            text_chars=10,
            channel="feishu",
            provider="feishu",
            errors=[],
            preview="* stub digest preview *",
        )

    monkeypatch.setattr(NotificationService, "send_digest", _fake_send_digest)

    response = client.post(
        "/api/internal/pipeline/run",
        json={"send_digest": True, "write_docx": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    # — digest_sent must be True (proves .notifications_delivered was read
    # via the attribute, not .get())
    assert body["digest_sent"] is True


async def test_pipeline_run_with_write_docx_only_does_not_crash(client, monkeypatch):
    """write_docx=True without send_digest must not NameError.

    Before the fix, the docx block referenced ``raw_count`` /
    ``signal_count`` (defined further down) and tried
    ``outcome.get("preview", ...)`` against an undefined ``outcome``
    local — both raised inside the 200-return path and surfaced as 500.
    """
    from dataclasses import dataclass, field

    from app.services.research import ResearchService

    @dataclass
    class _R:
        raw_count: int = 3
        new_count: int = 1
        signal_count: int = 1
        errors: list = field(default_factory=list)

        def as_dict(self):
            return {
                "raw_count": self.raw_count,
                "new_count": self.new_count,
                "signal_count": self.signal_count,
                "errors": self.errors,
            }

    async def _fake_research(self):
        return _R()

    monkeypatch.setattr(ResearchService, "run_once", _fake_research)

    # Configure a drive root token + stub FeishuDriveClient.create_default
    # so the docx block exercises the code path without real HTTP.
    from app.config import get_settings
    from app.services.feishu.content_client import FeishuDriveClient
    from app.services.feishu.drive_org import DriveOrgService

    settings = get_settings()
    settings.feishu_drive_root_folder_token = "root_folder_token"

    class _FakeDrive:
        @property
        def is_configured(self):
            return True

        @property
        def folder_token(self):
            return "root_folder_token"

        async def ensure_folder_path(self, *, parent_token, path):
            return f"tok_{path[-1]}"

        async def create_docx_from_markdown(self, *, title, markdown, folder_token):
            return {
                "doc_id": "doc_fake",
                "url": "https://feishu.cn/docx/doc_fake",
                "folder_token": folder_token,
            }

    @classmethod
    def _create_default(cls, settings=None):
        return _FakeDrive()

    monkeypatch.setattr(FeishuDriveClient, "create_default", _create_default)

    response = client.post(
        "/api/internal/pipeline/run",
        json={"send_digest": False, "write_docx": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["digest_sent"] is False
    # — docx_ref is populated (not the "FEISHU_DRIVE_ROOT_FOLDER_TOKEN not
    # configured" branch). It may carry an error if DriveOrgService path
    # fails (depends on fixture), but the block must execute.
    assert body["docx"] is not None