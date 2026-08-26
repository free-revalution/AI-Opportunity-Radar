# n8n workflows

This directory stores workflow JSON exports. Each workflow calls the
backend via HTTP — **no business logic lives in n8n**.

## Loading the workflows

The Docker compose stack mounts this directory at `/tmp/workflows` inside
the n8n container. Use the n8n UI or CLI to import these files after the
service is up:

```bash
# Once n8n is running at http://localhost:5678
# Either: paste the JSON via the n8n UI ("Import from clipboard")
# Or:     POST it to the n8n API with the file body.
```

## Included workflows

| File | Purpose |
|---|---|
| `daily-opportunity-discovery.json` | Cron → backend discovery → digest build → Telegram |
| `research-opportunity.json` | Webhook → backend research trigger → backend notify |

Both reference `API_BASE_URL_INTERNAL` (set in `.env`) which should point
to `http://backend:8000` from inside the docker network.