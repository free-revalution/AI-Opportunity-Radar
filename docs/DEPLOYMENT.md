# Deployment Guide

## Local development

```bash
cp .env.example .env
make docker-up
```

The stack boots Postgres, Redis, the FastAPI backend (with live reload),
the Next.js dev server, and n8n. Source-code directories are bind-mounted
so edits are reflected immediately.

## Production (single host, Docker)

1. Provision a Linux host with Docker 24+.
2. Clone the repo.
3. Copy `.env.example` to `.env` and fill in real API keys.
4. Set `APP_ENV=prod`, `APP_DEBUG=false`, `APP_SECRET_KEY=<random>`.
5. Boot:

   ```bash
   docker compose up -d --build
   docker compose exec backend alembic upgrade head
   docker compose exec backend python -m app.scripts.seed
   ```

6. Front the FastAPI container with Caddy or nginx for TLS:

   ```
   api.your-domain.com → http://backend:8000
   app.your-domain.com → http://frontend:3000
   ```

7. Configure a webhook from Telegram → `https://api.your-domain.com/api/telegram/webhook`
   with the secret from `TELEGRAM_WEBHOOK_SECRET`.

## Production (managed Kubernetes)

Each service becomes a Deployment; Postgres becomes a managed service
(RDS / Cloud SQL); Redis becomes Elasticache. Set the same env vars on
the backend Deployment and the worker Deployment.

The `worker` container and the `backend` container share the same image
but run different commands (`uvicorn` vs `python -m app.worker`).

## Observability

- `/api/health` — per-dependency status (Postgres, Redis, LLM providers,
  Firecrawl, Browser Use, Telegram, n8n). Each component reports
  `healthy` / `degraded` / `down`.
- `/api/health/live` — Kubernetes-style liveness probe (no dependency
  checks).
- Structured JSON logs on stdout (`structlog`). Ship with
  `docker compose logs` or your log forwarder of choice.

## Backups

- Postgres: nightly `pg_dump` to object storage.
- Redis: enable AOF persistence (already configured in compose).
- n8n: `n8n export:workflow --all` should be run weekly.

## Upgrades

```bash
git pull
docker compose pull
docker compose up -d --build
docker compose exec backend alembic upgrade head
```