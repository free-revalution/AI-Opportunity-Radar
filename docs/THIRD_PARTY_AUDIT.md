# Third-Party Components Audit

> Generated: 2026-08-26
> Scope: Validate license, integration mode, and stability of every open-source component the MVP will rely on.

The MVP **does not** fork, vendor, or re-distribute any of these projects. Each one is consumed as an **external service / API** so we keep our own codebase under a permissive license and avoid copyleft contamination.

| Project | Repo | License | Usage mode | Risk |
|---|---|---|---|---|
| Firecrawl | [firecrawl/firecrawl](https://github.com/firecrawl/firecrawl) | **AGPL-3.0** | Hosted Cloud API (REST: `/scrape`, `/crawl`, `/map`, `/search`, `/extract`) | High if self-hosted & modified — we never self-host in MVP |
| Browser Use | [browser-use/browser-use](https://github.com/browser-use/browser-use) | MIT (library) / Cloud TOS (service) | Cloud API preferred, self-host fallback | Low |
| Deep Research | [dzhng/deep-research](https://github.com/dzhng/deep-research) | MIT | We **re-implement** the research loop in our backend, not vendoring | Low |
| TrendRadar | [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) | MIT (verify before commercialize) | Reference patterns only — our own connectors are independent | Medium (verify before commercialize) |
| n8n | [n8n-io/n8n](https://github.com/n8n-io/n8n) | **Sustainable Use License** (since 2025) | Self-hosted Docker for orchestration only | Cannot be re-offered as a hosted product |

## Detailed Findings

### Firecrawl — AGPL-3.0

- **Why risky**: AGPL-3.0 requires that any modified version exposed over a network must publish source. Self-hosting + modifying the codebase would force us to publish our internal changes.
- **MVP strategy**:
  - Use the **hosted API** (`https://api.firecrawl.dev`) only.
  - Never copy Firecrawl source into our repo.
  - Keep all Firecrawl access behind an abstraction (`FirecrawlService`) so the implementation can be swapped.
- **Verification before commercialization**: Re-check Firecrawl LICENSE, Hosted Terms, and trademark guidance. Firecrawl's API may impose additional usage limits or branding requirements.

### Browser Use

- **Library** (`browser-use` on PyPI): MIT-style permissive license, useful as reference.
- **Cloud service** (`https://api.browser-use.com`): commercial ToS, preferred for MVP because it removes Playwright/Chromium operational burden.
- **Fallback chain** (mandatory): Browser Use → Firecrawl → offline Mock. Implemented as a `FallbackWebDataProvider` composite in `backend/app/services/research/fallback_provider.py` — every `ExternalServiceError` from Browser Use is caught and the next provider is tried automatically. The chain is always terminated by an offline Mock so a single vendor outage never aborts a research job.
- **Roadmap**: the audit originally specified Browser Use → Firecrawl → raw HTTP → skip; the raw HTTP step is deferred to a later phase.

### Deep Research (dzhng/deep-research)

- MIT licensed, but the project itself uses Firecrawl + an OpenAI-compatible LLM and runs an iterative **depth+breadth** question loop.
- We **do not import** the package. We re-implement the loop in `backend/app/services/research/` so:
  - We can feed it `ResearchContext` (already-fetched URLs/documents) and avoid duplicate fetches.
  - We can swap the underlying search/crawl provider.
  - We control the budget (`max_urls`, `max_depth`, `max_llm_calls`, `max_tokens`).

### TrendRadar (sansan0/TrendRadar)

- Multi-platform (11+ Chinese platforms: Weibo, Zhihu, Douyin, Bilibili, Toutiao, etc.) hot-news aggregator with AI analysis and scheduled push.
- We **do not** clone this project. We borrow the *pattern* (keyword-configurable hot-news collection + AI analysis + scheduled push) and write our own connectors against the same source APIs (or RSS mirrors) under our own data model.
- Re-check license before commercialization.

### n8n

- License changed from Apache 2.0 to **Sustainable Use License** in 2025. Self-hosting for internal use is still permitted, but **we may not offer n8n itself as a hosted service to others** and **may not build a competing product using n8n**.
- We use n8n purely as a **workflow orchestrator** that calls our own backend HTTP API. All business logic (scoring, clustering, research parsing) stays in the backend. If we later need to drop n8n, the backend keeps working via cron + worker process.

## Abstraction Boundary

```
Our Backend (MIT-style, our own code)
    ├── FirecrawlWebDataProvider   ← talks to firecrawl.dev REST API
    ├── BrowserUseWebDataProvider  ← talks to api.browser-use.com (or self-host)
    │     ↑ wrapped by ↓
    ├── FallbackWebDataProvider    ← BU → Firecrawl → Mock chain (catches
    │                                ExternalServiceError per step)
    ├── ResearchService            ← our own iterative loop (inspired by deep-research)
    ├── LLMRouter                  ← OpenAI / Anthropic / Gemini
    ├── Source connectors          ← GitHub, Reddit, HN, Product Hunt, RSS, ...
    └── TelegramService            ← bot token only, no source code copy
```

Every external dependency is hidden behind a Python `WebDataProvider` (or `LLMProvider`, `TelegramProvider`) interface so tests can substitute a fake implementation.