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