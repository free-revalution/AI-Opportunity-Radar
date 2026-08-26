# n8n workflows

This directory stores workflow JSON exports. Each workflow is a thin
HTTP orchestrator that calls our FastAPI backend at
`/api/internal/...` — **no business logic lives in n8n**.

## Loading the workflows

The Docker compose stack mounts this directory at `/tmp/workflows`
inside the n8n container, and `RADAR_WEBHOOK_SECRET` + `API_BASE_URL_INTERNAL`
are injected as environment variables so the workflow HTTP nodes can
sign their requests and reach the backend over the docker network.

Two ways to push the local files into a running n8n:

```bash
# CLI helper — pushed via the n8n REST API
make n8n-sync               # create-or-update + activate
make n8n-validate           # parse every JSON, no network calls

# Or, once n8n is running at http://localhost:5678
# Either: paste the JSON via the n8n UI ("Import from clipboard")
# Or:     POST it to the n8n API with the file body.
```

## Authentication

Every workflow HTTP node sends `X-Radar-Webhook: $env.RADAR_WEBHOOK_SECRET`.
The backend verifies the same shared secret on every `/api/internal/*`
endpoint (see `backend/app/api/internal.py`).

| Side | Variable |
|---|---|
| Backend | `RADAR_WEBHOOK_SECRET` (or `APP_SECRET_KEY` as a stand-in) |
| n8n | `$env.RADAR_WEBHOOK_SECRET` |
| CLI helper | `N8N_BASE_URL` + `N8N_API_KEY` |

If both sides are unconfigured the backend accepts every request — useful
for local development, **never** for production.

## Included workflows

| File | Trigger | Purpose |
|---|---|---|
| `daily-opportunity-discovery.json` | Cron `0 2 * * *` UTC | Discovery → Clustering → Screening → Scoring → Deep Research → Digest/Telegram |
| `research-opportunity.json` | Webhook `POST /webhook/research-opportunity` | Run one specific research job, then send a Telegram alert |
| `manual-pipeline.json` | Webhook `POST /webhook/manual-pipeline` | Operator fires the full pipeline (or any subset) on demand |

The daily workflow is the only one shipped `active: true`. The two
webhook workflows ship `active: false` — flip them on via `make n8n-sync`
once you have verified the secret wiring.

## Backend contract

Every workflow calls one of these `POST /api/internal/...` endpoints.
None of them mutate state outside the database, and all of them are
idempotent at the job level (re-running a discovery or clustering pass
is safe).

| Endpoint | Body | Notes |
|---|---|---|
| `/api/internal/discovery/run` | `{"sources": [...], "mock": false}` | Ingest fresh items from enabled sources |
| `/api/internal/clustering/run` | `{"raw_item_limit": 500, "threshold": 0.82}` | Embed + cluster unclustered RawItems |
| `/api/internal/screening/run` | `{"limit": 50, "use_mock": false}` | LLM screening per opportunity |
| `/api/internal/scoring/run` | `{"limit": 200, "trigger_threshold": 70}` | Deterministic score + research eligibility gate |
| `/api/internal/research/run` | `{"limit": 10, "max_urls": 20, "use_mock_web": false, "use_mock_llm": false}` | Process every pending ResearchJob |
| `/api/internal/research/run/{id}` | `{"use_mock_web": false, "use_mock_llm": false}` | Single-job variant (used by `research-opportunity`) |
| `/api/internal/notifications/digest/send` | `{"max_entries": 5, "min_score": 70.0, "dry_run": false}` | Build + send the Telegram digest |
| `/api/internal/notifications/opportunity/{id}/send` | `{"extra_note": "..."}` | One-off Telegram alert for a freshly-completed report |

## Validating workflows

`backend/tests/test_n8n_workflows.py` runs as part of `make test-backend`
and asserts that every file in this directory:

1. is valid JSON with `name`, `nodes`, `connections`
2. has unique node `id`s
3. has a real `cronExpression` on any schedule trigger (no empty `interval`)
4. only hits `/api/internal/...` URLs from HTTP Request nodes
5. signs those requests with `X-Radar-Webhook: $env.RADAR_WEBHOOK_SECRET`
6. only references node names that actually exist in `nodes`

Drop a new workflow into this directory and the test suite will validate
it for free.
