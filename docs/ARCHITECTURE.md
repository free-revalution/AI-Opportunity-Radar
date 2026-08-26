# Architecture

## Pipeline

```
Internet
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  Source connectors (asyncio, one per platform)              │
│  ─ GitHubTrendingConnector                                  │
│  ─ RedditConnector (r/SaaS, r/LocalLLaMA, r/SideProject, …) │
│  ─ HackerNewsConnector (Algolia API)                        │
│  ─ ProductHuntConnector (REST + GraphQL)                    │
│  ─ RSSConnector (feedparser)                                │
│  ─ YouTubeConnector                                         │
└─────────────────────────────────────────────────────────────┘
   │ uniform RawItem dataclass
   ▼
┌─────────────────────────────────────────────────────────────┐
│  Deduplication                                              │
│  ─ UNIQUE(source_id, external_id)                           │
│  ─ content_hash (sha256 over normalised title + url)        │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  AI Screening (cheap LLM, JSON-mode)                        │
│  ─ is_business_relevant                                     │
│  ─ category, problem, potential_business                    │
│  ─ trend_strength, demand_strength                          │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  Embedding + clustering                                     │
│  ─ pgvector (planned) or in-memory numpy for MVP            │
│  ─ similarity threshold from EMBEDDING_CLUSTER_THRESHOLD    │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  Opportunity scoring (deterministic)                        │
│  ─ Trend × 0.20 + Demand × 0.20 + Monetization × 0.20       │
│  ─ Competition Gap × 0.15 + China Gap × 0.15 + Execution 0.10│
│  ─ total_score ≥ 70 → trigger deep research                 │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  Deep Research                                              │
│  ─ ResearchContext = opportunity + source URLs + docs       │
│  ─ Iterative depth+breadth loop (max 20 URLs, 3 depth)      │
│  ─ Firecrawl (search/scrape) first                          │
│  ─ Browser Use only on JS-heavy or login-required targets   │
│  ─ LLM produces strict JSON (executive_summary, …)          │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  Notifications                                             │
│  ─ Telegram bot (daily digest + research-complete)          │
│  ─ Dashboard (Next.js server components pull from API)      │
└─────────────────────────────────────────────────────────────┘
```

## Process topology

```
docker-compose stack
├── postgres        (port 5432)
├── redis           (port 6379)
├── backend         FastAPI HTTP  (port 8000)
├── worker          python -m app.worker  (Phase 3+)
├── frontend        next dev             (port 3000)
└── n8n             orchestrator         (port 5678)
```

External (NOT in compose):

- `api.firecrawl.dev` (Firecrawl REST)
- `api.browser-use.com` (Browser Use Cloud)
- `api.openai.com` / Anthropic / Gemini (LLM)
- `api.telegram.org` (Telegram Bot API)

## Why no direct Postgres writes from n8n?

n8n only calls **our backend HTTP API**. Business logic, validation and
deduplication always live in our code so we can later replace n8n with a
plain cron + worker without rewriting the system.

## Why is scoring implemented in pure Python?

The weighted formula (README §12) is the core product IP and needs to be
unit-tested without infrastructure. Keeping it framework-free (no DB,
no FastAPI) means the tests run in milliseconds and the formula is
auditable in a single file.