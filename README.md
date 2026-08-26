# AI Opportunity Radar

> Global AI business opportunity radar — daily discovery, scoring, and deep research of high-value product opportunities.

This repository contains the **full source** of the product. We do **not** vendor any third-party project; every open-source component is consumed as an external service. See [docs/THIRD_PARTY_AUDIT.md](docs/THIRD_PARTY_AUDIT.md).

---

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Project init / Docker / Backend skeleton / Frontend skeleton / Tests | **done** |
| 2 | Alembic migrations + repositories | **done** |
| 3 | Source connectors (GitHub, Reddit, HN, Product Hunt, RSS) | **done** |
| 4 | Deduplication + clustering | **done** |
| 5 | AI screening | planned |
| 6 | Opportunity scoring (formula already implemented in code) | planned |
| 7 | Deep research engine | planned |
| 8 | Telegram notifications | planned |
| 9 | Dashboard polish | planned |
| 10 | n8n workflows | wired (stubs only) |
| 11 | Browser Use integration | planned |
| 12 | Monitoring + ops | planned |

---

## Architecture

```
Internet
  └── source connectors (GitHub, Reddit, HN, Product Hunt, RSS, …)
        └── RawItem rows in PostgreSQL
              └── AI screening (LLM)
                    └── Signal rows
                          └── Opportunity scoring (deterministic formula)
                                └── Deep Research (LLM + Firecrawl)
                                      └── ResearchReport rows
                                            └── Telegram digest (daily 02:00 UTC)
                                            └── Next.js dashboard
```

Five independent open-source projects are **consumed as services**:

| Component | Repo | License | Mode |
|---|---|---|---|
| n8n | [n8n-io/n8n](https://github.com/n8n-io/n8n) | Sustainable Use License | Self-hosted Docker, orchestrator only |
| Firecrawl | [firecrawl/firecrawl](https://github.com/firecrawl/firecrawl) | AGPL-3.0 | Hosted REST API only |
| Browser Use | [browser-use/browser-use](https://github.com/browser-use/browser-use) | MIT | Cloud API preferred |
| TrendRadar | [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) | MIT (verify) | Reference patterns only |
| Deep Research | [dzhng/deep-research](https://github.com/dzhng/deep-research) | MIT | Re-implemented in our backend |

---

## Tech Stack

- **Backend** — Python 3.12, FastAPI, SQLAlchemy 2 (async), asyncpg, Alembic, structlog, Pydantic v2.
- **Frontend** — Next.js 14 (App Router), TypeScript, Tailwind, custom shadcn-style UI primitives.
- **Database** — PostgreSQL 16.
- **Cache / Queue** — Redis 7.
- **Orchestration** — n8n (Sustainable Use License).
- **Testing** — pytest + httpx TestClient (backend), Vitest + Testing Library (frontend).

---

## Repository Layout

```
.
├── backend/             FastAPI service (api/, models/, services/, tests/)
│   ├── app/
│   ├── alembic/         Migration environment
│   ├── fixtures/        Seed data
│   └── Dockerfile
├── frontend/            Next.js dashboard
│   └── src/
│       ├── app/         App router pages
│       ├── components/  UI components
│       ├── lib/         api client + utilities
│       └── types/       shared TS types
├── n8n/
│   └── workflows/       Imported into the n8n container at runtime
├── docs/                Architecture, audit, deployment
├── docker-compose.yml    Postgres + Redis + Backend + Worker + Frontend + n8n
├── Makefile             Convenience entrypoints
├── .env.example         Copy to `.env` and fill in
└── README.md            (this file)
```

---

## Quick Start (local)

```bash
# 1. Install prerequisites
#    - Docker Desktop
#    - Python 3.12 (for local tooling)
#    - Node 20+ (for local tooling)

# 2. Clone, configure
cp .env.example .env
# (optional) edit .env and paste real API keys

# 3. Boot the stack
make docker-up
# or: docker compose up -d --build

# 4. Open
#    Backend:        http://localhost:8000/docs
#    Frontend:       http://localhost:3000
#    n8n:            http://localhost:5678  (admin / change-me)
#    Postgres:       localhost:5432  (radar / radar)
#    Redis:          localhost:6379
```

## Run tests

```bash
make test
# or individually:
make test-backend
make test-frontend
```

## Common commands

```bash
make help              # list all targets
make dev               # run backend + frontend locally (no docker)
make migrate           # run alembic migrations
make seed              # load demo opportunities into the DB
make docker-down       # tear the stack down (volumes are kept)
make docker-logs       # tail docker logs
make clean             # remove caches / build artefacts
```

---

## Environment Variables

See [.env.example](.env.example). The key flags:

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes (prod) | asyncpg DSN |
| `REDIS_URL` | yes (prod) | redis DSN |
| `OPENAI_API_KEY` | optional | LLM provider |
| `FIRECRAWL_API_KEY` | optional | web data layer |
| `BROWSER_USE_API_KEY` | optional | browser interaction layer |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | optional | daily digest push |
| `N8N_BASE_URL` / `N8N_API_KEY` | optional | workflow orchestration |
| `MOCK_EXTERNAL_SERVICES` | yes (local) | when true, services fall back to fixtures |

When `MOCK_EXTERNAL_SERVICES=true` and an API key is missing, the connector returns
fixture data so you can develop without paying for external services.

---

## Scoring Formula (canonical)

```
Opportunity Score =
    Trend Velocity        × 0.20
  + Demand                × 0.20
  + Monetization          × 0.20
  + Competition Gap       × 0.15
  + China Gap             × 0.15
  + Execution Feasibility × 0.10
```

All sub-scores are normalised to 0-100. Total is rounded to two decimals.
A score ≥ 70 triggers deep research. ≥ 85 is `strongly_recommend`.

Implementation: [`backend/app/services/scoring/scoring.py`](backend/app/services/scoring/scoring.py).

---

## Security

- `.env` is gitignored; never commit real keys.
- All external URLs are passed through `assert_safe_url` (SSRF protection).
- API keys and tokens are filtered from logs by the structured-logging layer.
- Webhooks verify a shared secret header.

---

## License

Proprietary. Third-party components retain their own licenses (see [docs/THIRD_PARTY_AUDIT.md](docs/THIRD_PARTY_AUDIT.md)).