"""Async worker process entry point.

Phase 7+ roadmap: this becomes the long-running worker that consumes
research / screening / clustering jobs from Redis Streams and dispatches
them to the right service. Today it is a minimal idle loop — kept here
so `docker compose up` can boot the worker container cleanly while the
real job dispatch is being designed.

Responsibilities (current scope):

* Boot the DB pool + Redis client so health-checks in compose are green.
* Tick every N seconds, logging a structured heartbeat (visible in
  `make docker-logs`).
* Exit cleanly on SIGTERM / SIGINT (docker sends SIGTERM on
  `docker compose stop`).

When the real job pipeline lands, swap the `tick()` body for a
`redis.asyncio.client.Redis.xreadgroup(...)` consumer loop and reuse the
service entry points under `app.services.*` (`IngestionService`,
`ResearchService`, `NotificationService`).
"""

from __future__ import annotations

import asyncio
import signal
from typing import NoReturn

from app.config import get_settings
from app.db import close_db, init_db
from app.utils import configure_logging, get_logger

logger = get_logger(__name__)

# Idle tick interval (seconds). The real consumer loop will be event-driven
# (xreadgroup) so this value mostly affects heartbeat logging.
_TICK_SECONDS: float = 30.0


async def tick(stop_event: asyncio.Event) -> None:
    """Single iteration of the idle heartbeat loop."""
    settings = get_settings()
    # Re-emitting the relevant runtime config in the heartbeat makes it
    # easy to verify from `docker logs` that the worker is on the right
    # env without leaking secret values.
    logger.info(
        "worker_tick",
        env=settings.app_env,
        llm_provider=settings.llm_default_provider,
        mock_mode=settings.mock_external_services,
    )


async def run() -> None:
    """Long-running worker loop — returns only on SIGTERM / SIGINT."""
    settings = get_settings()
    configure_logging(settings.app_log_level)
    logger.info(
        "worker_starting",
        env=settings.app_env,
        version="0.1.0",
    )

    # Boot the DB pool so /health checks against Postgres are green from
    # the worker container too. We don't fail the worker if init_db()
    # raises — that just means the worker will log "db_init_failed" and
    # keep ticking so transient DB outages don't trigger restart loops.
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("worker_db_init_failed", error=str(exc))

    stop = asyncio.Event()

    def _request_stop(signum: int) -> None:
        logger.info("worker_signal", signum=signum)
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop, int(sig))
        except NotImplementedError:
            # add_signal_handler is unavailable on Windows — fall back
            # to default behaviour (KeyboardInterrupt on SIGINT).
            signal.signal(sig, lambda s, _f: _request_stop(s))

    while not stop.is_set():
        try:
            await tick(stop)
        except Exception as exc:  # noqa: BLE001 — never let a tick die
            logger.warning("worker_tick_failed", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=_TICK_SECONDS)
        except asyncio.TimeoutError:
            continue

    await close_db()
    logger.info("worker_stopped")


def main() -> NoReturn:
    asyncio.run(run())


if __name__ == "__main__":
    main()