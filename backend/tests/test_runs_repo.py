"""Tests for the MVP `Run` model + `RunRepository`.

The Run table is the source of truth for the Feishu `/status` command
(see simplify §37). These tests verify the lifecycle (start → finish)
and the read helpers (`latest`, `recent`) used by the new internal
endpoints (`/api/internal/status`).
"""

from __future__ import annotations

import pytest

from app.models import Run
from app.repositories import RunRepository


pytestmark = pytest.mark.asyncio


# ----------------- start / finish lifecycle -----------------
async def test_start_creates_running_row(sqlite_session):
    repo = RunRepository(sqlite_session)
    run = await repo.start(trigger="scheduler")
    assert run.id is not None
    assert run.status == "running"
    assert run.trigger == "scheduler"
    assert run.started_at is not None
    assert run.finished_at is None
    assert run.raw_count is None


async def test_finish_success_records_counts(sqlite_session):
    repo = RunRepository(sqlite_session)
    run = await repo.start(trigger="manual")
    await repo.finish_success(
        run,
        raw_count=128,
        new_count=47,
        signal_count=12,
    )
    fetched = await repo.get_by_id(run.id)
    assert fetched is not None
    assert fetched.status == "success"
    assert fetched.finished_at is not None
    assert fetched.raw_count == 128
    assert fetched.new_count == 47
    assert fetched.signal_count == 12
    assert fetched.error is None


async def test_finish_failed_records_error(sqlite_session):
    repo = RunRepository(sqlite_session)
    run = await repo.start(trigger="bot_run")
    await repo.finish_failed(run, error="LLM timeout")
    fetched = await repo.get_by_id(run.id)
    assert fetched is not None
    assert fetched.status == "failed"
    assert fetched.error == "LLM timeout"
    assert fetched.finished_at is not None


async def test_finish_failed_truncates_long_error(sqlite_session):
    repo = RunRepository(sqlite_session)
    run = await repo.start()
    long_err = "x" * 9000
    await repo.finish_failed(run, error=long_err)
    fetched = await repo.get_by_id(run.id)
    assert fetched is not None
    assert len(fetched.error or "") == 8000


# ----------------- read helpers -----------------
async def test_latest_returns_most_recent(sqlite_session):
    repo = RunRepository(sqlite_session)
    first = await repo.start(trigger="scheduler")
    await repo.finish_success(first, raw_count=10, new_count=10, signal_count=1)
    second = await repo.start(trigger="manual")
    await repo.finish_success(second, raw_count=20, new_count=20, signal_count=2)

    latest = await repo.latest()
    assert latest is not None
    assert latest.id == second.id
    assert latest.raw_count == 20


async def test_latest_returns_none_when_empty(sqlite_session):
    repo = RunRepository(sqlite_session)
    assert await repo.latest() is None


async def test_recent_orders_by_started_at_desc(sqlite_session):
    repo = RunRepository(sqlite_session)
    ids: list[int] = []
    for _ in range(3):
        run = await repo.start()
        await repo.finish_success(run, raw_count=1, new_count=1, signal_count=0)
        ids.append(run.id)
    recent = await repo.recent(limit=10)
    assert [r.id for r in recent] == list(reversed(ids))


async def test_recent_respects_limit(sqlite_session):
    repo = RunRepository(sqlite_session)
    for _ in range(5):
        await repo.start()
    recent = await repo.recent(limit=2)
    assert len(recent) == 2


# ----------------- trigger values -----------------
@pytest.mark.parametrize("trigger", ["scheduler", "manual", "bot_run", "test"])
async def test_start_accepts_freeform_trigger(sqlite_session, trigger: str):
    repo = RunRepository(sqlite_session)
    run = await repo.start(trigger=trigger)
    assert run.trigger == trigger
