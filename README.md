# AI Opportunity Radar (MVP)

> 公开信息 → AI 整理 → 飞书文档 → 飞书机器人控制

The MVP validates a single hypothesis: are paying readers willing to
keep coming back for an AI-curated daily digest of AI business
opportunities, delivered through a Feishu bot?

Everything else — subscriptions, activation codes, paywalls, content
radar, multi-channel fallback, the Next.js dashboard — has been moved
to [`experimental/`](experimental/) and is **not** part of the MVP.

---

## Pipeline

```
public sources                AI                     Feishu
─────────────           ──────────────          ──────────────
GitHub / HN / Reddit / ──► LLM summary       ──► daily digest IM
Product Hunt / RSS         + scoring
                            + deep research
                              (Firecrawl +
                               Browser Use)
```

The daily cron runs at **08:00 CST** (00:00 UTC) via n8n and writes a
single Feishu digest message. Failure of any step is recorded in the
`runs` table; the bot's `/status` command surfaces it.

---

## Feishu bot commands

| Command  | Alias    | Action |
|----------|----------|--------|
| `/today` | `/今日`  | Send today's digest right now |
| `/run`   | `/运行`  | Trigger the full daily pipeline manually |
| `/status`| `/状态`  | Last run + per-source health snapshot |
| `/sources`| `/源`   | Per-source health, identical to `/status sources` |
| `/help`  | `/帮助`  | List the commands |

All five are dispatched by [`backend/app/services/feishu/inbound.py`](backend/app/services/feishu/inbound.py)
and routed to internal endpoints under `/api/internal/*`.

---

## Tech stack

- **Backend** — Python 3.12, FastAPI, SQLAlchemy 2 (async), asyncpg,
  Alembic, Pydantic v2, structlog.
- **Database** — PostgreSQL 16.
- **Cache / dedup** — Redis 7 (only used by F.2 event idempotency).
- **Orchestration** — n8n (Sustainable Use License) — owns the daily cron.
- **LLM** — MiniMax primary; Firecrawl + Browser Use for deep research.
- **Tests** — pytest + httpx TestClient. `make test-backend`.

---

## Repository layout

```
.
├── backend/              FastAPI service (api/, models/, services/, tests/)
│   ├── app/
│   ├── alembic/          migrations
│   └── Dockerfile
├── n8n/
│   └── workflows/        imported by the n8n container
├── experimental/         FREEZE surface (moved out of MVP)
│   ├── backend/          pre-MVP services, repos, endpoints
│   ├── frontend/         pre-MVP Next.js dashboard
│   └── docs/             pre-MVP architecture docs
├── docker-compose.yml    postgres + redis + backend + n8n
├── Makefile              convenience commands
├── .env.example          copy → .env
└── README.md             this file
```

---

## Quick start

```bash
# 1. Prereqs: Docker Desktop, Python 3.12 (for local tools)
cp .env.example .env
# (edit .env to fill in real API keys, or leave MOCK_EXTERNAL_SERVICES=true)

# 2. Bring up the stack
make docker-up            # or: docker compose up -d --build

# 3. Run migrations once
make migrate

# 4. Open
#    Backend:   http://localhost:8000/docs
#    n8n:       http://localhost:5678
#    Postgres:  localhost:5432 (radar / radar)
#    Redis:     localhost:6379
```

To run the pipeline manually without waiting for cron:

```bash
curl -X POST http://localhost:8000/api/internal/pipeline/run \
     -H "X-Radar-Webhook: $RADAR_WEBHOOK_SECRET" \
     -H "Content-Type: application/json" \
     -d '{"send_digest": true}'
```

---

## Tests

```bash
make test-backend         # pytest (SQLite in-memory)
```

The pytest suite has **648 passing tests + 17 skipped** (the skipped
ones are FREEZE dispatcher tests that have been retired with their
implementations — see `experimental/`).

---

## Environment variables

The MVP template lists only the variables the running services
actually read. Vars for the FREEZE surface (subscriptions,
activation, content radar, multi-channel fallback, OpenAI/Anthropic/
Gemini keys, Telegram, backups, etc.) were removed — see
[`experimental/backend/.env.example`](experimental/backend/.env.example)
for the pre-MVP list.

Key vars:

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | yes (prod) | asyncpg DSN |
| `REDIS_URL` | yes (prod) | Feishu event dedupe |
| `MINIMAX_API_KEY` | recommended | primary LLM |
| `FIRECRAWL_API_KEY` | optional | web scraping (deep research) |
| `BROWSER_USE_API_KEY` | optional | headless browsing (deep research) |
| `FEISHU_WEBHOOK_URL` | recommended | daily-digest IM target |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | required (prod) | inbound events |
| `FEISHU_ENCRYPT_KEY` | optional | AES-256-CBC body decryption (F.1) |
| `RADAR_WEBHOOK_SECRET` | required (prod) | shared with n8n |
| `ADMIN_OPEN_IDS` / `ADMIN_API_SECRET` | recommended | `/api/admin/*` gating |
| `MOCK_EXTERNAL_SERVICES` | yes (local) | fixtures when keys missing |

---

## Security

- `.env` is gitignored — never commit real keys.
- All outbound URLs go through `assert_safe_url` (SSRF blocklist).
- API keys / tokens are filtered from structlog output.
- Inbound Feishu events: AES-256-CBC body decryption (F.1) +
  Redis SETNX idempotency (F.2, 24h TTL).
- `/api/internal/*` and `/api/admin/*` require the matching shared
  secret header.

---

## License

Proprietary. The third-party components we consume retain their
upstream licenses (n8n — Sustainable Use, Firecrawl — AGPL-3.0 hosted
only, Browser Use — MIT).