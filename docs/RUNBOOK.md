# Operator Runbook

> Phase 12 — first-responder playbook for the AI Opportunity Radar backend.
> Read this before paging anyone; most outages are recoverable from the host
> without touching the codebase.

## Health endpoints (quick reference)

| Endpoint | Purpose | Returns |
|---|---|---|
| `GET /api/health/live` | Liveness probe — process up? | `200 {"status":"alive"}` always |
| `GET /api/health/ready` | Readiness probe — DB + Redis up? | `200 {"status":"ready"}` or `503 {"status":"not_ready"}` |
| `GET /api/health` | Deep health — every dependency | `200` with per-component status; never `5xx` |
| `GET /api/metrics` | Prometheus text-format exposition | `200 text/plain` or `404` if disabled |

- **Liveness** failures mean "restart the container".
- **Readiness** failures mean "stop sending traffic to the pod" but do NOT restart (DB blip will recover).
- **Deep health** failures mean "investigate the named component".

The compose `backend` healthcheck hits `/api/health`; it tolerates `degraded`. Only `down` marks the container unhealthy. Kubernetes users should configure liveness → `/api/health/live` and readiness → `/api/health/ready`.

## First-responder checklist

1. **Page received?** `make docker-logs` and check the timestamp on the last `startup` event. If absent → process is dead → `make docker-up` (compose restarts automatically unless stopped).
2. **Backend looks alive?** `curl -sf http://localhost:8000/api/health/live` (expect 200). If 5xx → check `docker logs radar-backend`.
4. **Pipeline is stuck?** `curl -s http://localhost:8000/api/metrics | grep radar_pipeline_runs_total` — the counter for `stage="research"` should tick every night at 02:00 UTC. If 24h of silence, check the n8n container (`docker logs radar-n8n`) for cron errors.
5. **External service down?** `curl -s http://localhost:8000/api/health | jq .components.firecrawl` (or `browser_use`). The Phase 11 fallback chain should keep research jobs running — check `radar_web_data_requests_total{outcome="error"}` for the error rate.
6. **Notifications not sending?** `curl -s http://localhost:8000/api/health | jq .components.telegram`. If `degraded`, `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is missing in `.env`.
7. **DB full?** `docker exec radar-postgres psql -U radar -d radar -c "SELECT pg_database_size('radar');"` — compare to the volume size. If near full, prune old `Raw` + `Research` rows or expand the volume.

## Common failure modes

### Postgres unreachable

**Symptoms:** `/api/health` returns `postgres.status=down`, `/api/health/ready` returns 503, ingest endpoints 500.

**Recovery:**
```bash
docker logs radar-postgres --tail=200
docker exec radar-postgres pg_isready -U radar -d radar
docker compose restart postgres   # only if pg_isready fails repeatedly
```

The backend will reconnect automatically (SQLAlchemy `pool_pre_ping=True`).

### Redis unreachable

**Symptoms:** `/api/health` returns `redis.status=down`. The backend stays up but `aiocache` reads start hitting Postgres directly.

**Recovery:**
```bash
docker logs radar-redis --tail=50
docker exec radar-redis redis-cli ping
docker compose restart redis
```

Redis uses AOF persistence (`--appendonly yes`); the container's data survives a restart.

### Firecrawl outage

**Symptoms:** `firecrawl.status=degraded` in `/api/health`. Research jobs are slower but still complete.

**Recovery:** None required — Phase 11's fallback chain tries Browser Use, then Mock. Check the rate of `radar_web_data_requests_total{outcome="error",provider="firecrawl"}`; if sustained, open a Firecrawl ticket.

### Browser Use outage

Same as Firecrawl — the chain falls through to Mock. Mock returns fixture URLs so research still completes (with lower-quality sources). Expect the `provider="mock"` rate in `radar_web_data_requests_total` to spike.

### Telegram send failure

**Symptoms:** `notification_failed` events in the logs; `/api/notifications/history` shows rows with `error=...`.

**Recovery:** Verify the bot token in `.env`. Manual smoke:
```bash
curl -sf "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"
```
If the bot responds, the token is fine — check the chat ID.

### n8n cron stuck

**Symptoms:** No new `radar_pipeline_runs_total{stage=...}` samples for 24h.

**Recovery:**
```bash
docker logs radar-n8n --tail=100
docker exec radar-n8n n8n export:workflow --all
make n8n-validate    # confirm JSON files still parse
make n8n-sync        # re-push into the container
```

If n8n is dead, the backend still works via direct HTTP (`curl -X POST http://localhost:8000/api/internal/discovery/run`). You can run the pipeline manually until n8n is restored.

## Backup and restore

### Nightly backup

```bash
make backup
```
Writes `./backups/radar-<UTC-timestamp>.sql` via `docker exec radar-postgres pg_dump`.

To automate, add a cron entry on the host:
```cron
0 3 * * * cd /srv/radar && make backup
```

### Restore on a fresh host

```bash
docker compose up -d postgres
# wait for postgres to become healthy (docker compose ps)
make restore -- --file=./backups/radar-20260826T030000Z.sql
```

### Verify a backup

```bash
# Quick smoke:
grep -c '^CREATE TABLE' ./backups/radar-20260826T030000Z.sql
# Expect ~10+ (alembic migrations + DB metadata).
```

A more rigorous check:
```bash
docker exec -i radar-postgres psql -U radar -d radar_restore < ./backups/radar-20260826T030000Z.sql
# (spin up a second container with a different DB name to test in isolation)
```

## Observability quick-reference

`make metrics-scrape` greps the first 40 `radar_*` lines from the live endpoint. The full list:

- `radar_pipeline_runs_total{stage,outcome,kind}` — batch-endpoint tick counter.
- `radar_pipeline_duration_seconds{stage}` — histogram of `run_once` wall time.
- `radar_web_data_requests_total{provider,op,outcome,chain}` — per-provider attempt.
- `radar_external_service_errors_total{provider,kind}` — coarse error counter.
- `radar_notifications_total{kind,provider,outcome}` — Telegram dispatch outcomes.
- `radar_research_job_duration_seconds` — per-job wall time.
- `radar_http_requests_total{method,path,status}` + `radar_http_request_duration_seconds{method,path}` — HTTP middleware.

Alert thresholds are documented in [`ALERTS.md`](ALERTS.md).

## Escalation

If the steps above don't restore service within 30 minutes:

1. Capture `docker logs radar-backend --since=1h > /tmp/backend.log` (and same for `radar-postgres`, `radar-n8n`).
2. Capture `/api/health` and `/api/metrics` snapshots.
3. Capture the most recent `Notification` rows from `/api/notifications/history` if the outage touches Telegram.
4. Page the on-call with the three files attached.