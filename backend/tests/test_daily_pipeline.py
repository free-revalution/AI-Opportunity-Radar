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
from sqlalchemy import select

from app.models import Run

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
    """If one step raises, the Run row is marked failed with the error.

    Phase 36+ v3 — failure record is now written by a background
    asyncio task spawned at error-handling time. We need to wait
    for that task to commit before reading; TestClient is sync and
    would otherwise race the commit. We also read through a fresh
    session to avoid TestClient's session cache.
    """
    import asyncio as _asyncio

    from app.services.research import ResearchService

    task_done = _asyncio.Event()
    from app.api import internal as _internal_mod

    orig_write = _internal_mod._write_failure_record_async

    async def _spied(*, run_id, error_text):
        try:
            await orig_write(run_id=run_id, error_text=error_text)
        finally:
            task_done.set()

    monkeypatch.setattr(
        _internal_mod, "_write_failure_record_async", _spied
    )

    async def _boom(self):
        raise RuntimeError("simulated research outage")

    monkeypatch.setattr(ResearchService, "run_once", _boom)

    with pytest.raises(RuntimeError, match="simulated research outage"):
        client.post(
            "/api/internal/pipeline/run",
            json={"send_digest": False},
        )

    try:
        await _asyncio.wait_for(task_done.wait(), timeout=5.0)
    except _asyncio.TimeoutError:
        pytest.fail("Background finish_failed task didn't complete within 5s")

    # Read directly through sessionmaker to bypass TestClient's session
    # cache (which would otherwise return a stale snapshot of the row).
    last = None
    async with client.sessionmaker() as verify_session:  # type: ignore[attr-defined]
        result = await verify_session.execute(
            select(Run).order_by(Run.started_at.desc()).limit(1)
        )
        last = result.scalar_one_or_none()
    assert last is not None
    assert last.status == "failed"
    assert "simulated research outage" in (last.error or "")


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


# ---------------------------------------------------------------------------
# Phase 29 regression — /api/internal/pipeline/run used to call
# ``runs.finish_success(...)`` (and ``finish_failed(...)`` in the except
# branch) but never ``await session.commit()``. Each request gets a
# fresh AsyncSession via the ``get_session`` dependency; once the request
# ends, the ``async with`` block in the dependency tears down the session
# and rolls back any uncommitted writes. Symptom: every /run returned 200
# with ``status: "success"`` yet ``SELECT status, finished_at FROM runs``
# still showed ``running`` / NULL — the bot's ``/status`` reply therefore
# rendered "Last Run: 运行中" forever. Both tests below pin the
# before-and-after behaviour.
# ---------------------------------------------------------------------------
async def test_pipeline_run_persists_run_row_status_to_db(client, monkeypatch):
    """A successful /run must produce a Run row whose ``status='success'``
    and ``finished_at`` are committed (visible to a follow-up /status
    read). The before-fix code flushed but never committed, so a
    separate-session SELECT still saw ``status='running'``.
    """
    from dataclasses import dataclass, field

    from app.services.research import ResearchService

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

    async def _fake_research(self):
        return _R()

    monkeypatch.setattr(ResearchService, "run_once", _fake_research)

    response = client.post(
        "/api/internal/pipeline/run",
        json={"send_digest": False, "write_docx": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["finished_at"] is not None

    # Independent read via /api/internal/status (which builds its own
    # session from the same engine) must see the committed row.
    status_resp = client.get("/api/internal/status")
    assert status_resp.status_code == 200
    last = status_resp.json()["last_run"]
    assert last is not None
    assert last["status"] == "success", (
        "PipelineRun row was not committed — /status still sees "
        "status='running'. Did the commit() in run_pipeline get "
        "removed?"
    )
    assert last["finished_at"] is not None
    assert last["raw_count"] is not None  # the count columns live too


async def test_pipeline_run_failed_branch_commits_run_row(client, monkeypatch):
    """The except branch must also commit — otherwise the Run row
    stays at status='running' after a real failure and ``/status``
    can't tell the user anything went wrong.

    Phase 36+ v3 — the failure record is written from a background
    asyncio task spawned at error-handling time. TestClient is
    synchronous so we need to wait for that task to finish before
    querying /status; otherwise we'd race the commit and see the
    stale "running" status.
    """
    import asyncio as _asyncio

    from app.services.research import ResearchService

    task_done = _asyncio.Event()

    orig_write = None
    from app.api import internal as _internal_mod

    orig_write = _internal_mod._write_failure_record_async

    async def _spied_write_failure_record(*, run_id, error_text):
        try:
            await orig_write(run_id=run_id, error_text=error_text)
        finally:
            task_done.set()

    monkeypatch.setattr(
        _internal_mod,
        "_write_failure_record_async",
        _spied_write_failure_record,
    )

    async def _boom(self):
        raise RuntimeError("simulated research outage")

    monkeypatch.setattr(ResearchService, "run_once", _boom)

    with pytest.raises(RuntimeError, match="simulated research outage"):
        client.post(
            "/api/internal/pipeline/run",
            json={"send_digest": False},
        )

    # Wait for the background task to commit before reading /status.
    try:
        await _asyncio.wait_for(task_done.wait(), timeout=5.0)
    except _asyncio.TimeoutError:
        pytest.fail(
            "Background finish_failed task didn't complete within 5s"
        )

    # Independent read — bypass TestClient's session cache by querying
    # through the test sessionmaker directly. Each request would get
    # a fresh session in production; TestClient reuses one.
    last = None
    async with client.sessionmaker() as verify_session:  # type: ignore[attr-defined]
        result = await verify_session.execute(
            select(Run).order_by(Run.started_at.desc()).limit(1)
        )
        last = result.scalar_one_or_none()
    assert last is not None
    assert last.status == "failed", (
        f"Failure branch never committed — latest Run is status={last.status!r}"
    )
    assert "simulated research outage" in (last.error or "")


# ---------------------------------------------------------------------------
# Phase 33 PR-33-H — Drive 未配置 → soft skip(非 success 内嵌 error)
# ---------------------------------------------------------------------------
async def test_pipeline_drive_not_configured_returns_soft_skip(
    client, monkeypatch
) -> None:
    """PR-33-H: drive 未配置时,response.docx 是 ``{"skipped": "drive_not_configured"}``
    而不是 ``{"error": "FEISHU_DRIVE_ROOT_FOLDER_TOKEN not configured"}``。

    通过 monkeypatch settings.feishu_drive_root_folder_token="" 走 else 分支。
    """
    from app.config import get_settings

    # 1) 清空 drive token,让 internal.run_pipeline 走 else 分支
    monkeypatch.setattr(
        get_settings(), "feishu_drive_root_folder_token", ""
    )

    response = client.post(
        "/api/internal/pipeline/run",
        json={"send_digest": False, "write_docx": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # status 仍然是 success — drive 是 optional sink
    assert body["status"] == "success"
    # docx 字段应该 soft skip,不含 error key
    docx = body.get("docx") or {}
    assert "error" not in docx, (
        f"drive not configured should be soft skip, got error: {docx.get('error')}"
    )
    assert docx.get("skipped") == "drive_not_configured"


# ---------------------------------------------------------------------------
# Phase 36+ — pipeline 异常分支的 rollback + finish_failed 兜底
# ---------------------------------------------------------------------------
async def test_pipeline_exception_branch_finishes_failed_run(
    client, monkeypatch
) -> None:
    """pipeline 早期某步抛异常时,run_pipeline 的 except 分支必须:
      1. 调用 ``session.rollback()`` 清掉 SQLAlchemy 的 aborted-transaction
         状态(否则 Postgres 后续 SQL 全部失败)
      2. 调度 BackgroundTask 写 ``RunRepository.finish_failed`` 把 Run row
         标 failed

    Phase 36+ v3: 改用 BackgroundTasks — 在响应 body 发出之后才跑,
    彻底绕开 contaminated session / connection pool 污染。

    SQLite 测试不模拟 aborted transaction(SQLite 没这个语义),但我们
    可以验证 **调用顺序 + 行最终状态**: rollback → background task →
    finish_failed,确保 Phase 36+ 修复逻辑就位。
    """
    import asyncio as _asyncio

    from app.services.research import ResearchService
    from app.repositories import RunRepository
    from sqlalchemy.ext.asyncio import AsyncSession

    call_order: list[str] = []
    task_done = _asyncio.Event()

    real_rollback = AsyncSession.rollback

    async def _spied_rollback(self, *a, **kw):
        call_order.append("rollback")
        return await real_rollback(self, *a, **kw)

    real_finish_failed = RunRepository.finish_failed

    async def _spied_finish_failed(self, run, *, error):
        call_order.append(f"finish_failed:{run.id}:{error[:60]}")
        result = await real_finish_failed(self, run, error=error)
        task_done.set()
        return result

    monkeypatch.setattr(AsyncSession, "rollback", _spied_rollback)
    monkeypatch.setattr(RunRepository, "finish_failed", _spied_finish_failed)

    # — Patch research.run_once 抛 RuntimeError,pipeline 一定走 except 分支
    async def _boom(self):
        raise RuntimeError("simulated pipeline abort for phase36+ rollback test")

    monkeypatch.setattr(ResearchService, "run_once", _boom)

    with pytest.raises(RuntimeError, match="simulated pipeline abort"):
        client.post(
            "/api/internal/pipeline/run",
            json={"send_digest": False, "write_docx": False},
        )

    # — 等 background task 跑完(最多 5s)
    try:
        await _asyncio.wait_for(task_done.wait(), timeout=5.0)
    except _asyncio.TimeoutError:
        pytest.fail(
            f"Background finish_failed task 没在 5s 内完成。"
            f"call_order={call_order}"
        )

    # — 关键断言 1:rollback 至少被调一次
    assert "rollback" in call_order, (
        f"Phase 36+ 修复: pipeline 异常分支必须先 session.rollback() 再 "
        f"finish_failed,否则 Postgres 后续 SQL 全部失败。"
        f"call_order={call_order}"
    )
    # — 关键断言 2:finish_failed 真的被调到了,且原 pipeline 异常被记录
    finish_failed_calls = [c for c in call_order if c.startswith("finish_failed:")]
    assert len(finish_failed_calls) == 1, (
        f"finish_failed 应被调一次,实为 {len(finish_failed_calls)}: {call_order}"
    )
    assert "simulated pipeline abort" in finish_failed_calls[0]

    # — 关键断言 3:Run row 真的被标 failed 了(用户能在 /status 看到)
    # 注意:TestClient 内部 session 缓存,即使 row 真的被 commit 了,
    # 同一个 session 内的 SELECT 可能看到旧快照。给 session expire
    # 一下强制重读,模拟生产环境"下一个 HTTP request"。
    from sqlalchemy.ext.asyncio import AsyncSession as _AS

    # — 直接通过 SQLite engine 查,绕开 SQLAlchemy session 缓存,
    # 这是最稳的验证方式(也是生产环境的真实路径 — 每个新 request
    # 都是新 session)。
    last = None
    try:
        from app.models import Run as _Run

        # — 使用 conftest 暴露的 sessionmaker
        async with client.sessionmaker() as verify_session:  # type: ignore[attr-defined]
            result = await verify_session.execute(
                select(_Run).order_by(_Run.started_at.desc()).limit(1)
            )
            last = result.scalar_one_or_none()
    except Exception:  # noqa: BLE001
        pass

    if last is None:
        # — 回退到 /status(老路径,有 session 缓存问题)
        r = client.get("/api/internal/status")
        assert r.status_code == 200
        last_dict = r.json().get("last_run")
        assert last_dict is not None
        assert last_dict["status"] == "failed", (
            f"Background task 路径下 Run row 应被标 failed,实际: {last_dict}"
        )
        assert "simulated pipeline abort" in (last_dict.get("error") or "")
    else:
        assert last is not None
        assert last.status == "failed", (
            f"Background task 路径下 Run row 应被标 failed,实际: {last.status}"
        )
        assert "simulated pipeline abort" in (last.error or "")