# Alerting Rules

> Phase 12 — sample Prometheus alerting rules for the AI Opportunity Radar.
> Copy these into your `prometheus.yml` / Alertmanager config; we deliberately
> do not ship a Prometheus stack in `docker-compose` (operators keep the
> choice of scrape target).

## What to scrape

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: radar-backend
    metrics_path: /api/metrics
    static_configs:
      - targets: ['backend:8000']
    scrape_interval: 30s
```

The backend exposes Prometheus text-format on `GET /api/metrics`. The
endpoint is unauthenticated by design — restrict via network policy
(allowlist the Prometheus IP, or front with an internal-only ingress).

## Alert rules

```yaml
groups:
  - name: radar-pipeline
    interval: 5m
    rules:
      # 1. The daily pipeline hasn't ticked in 24h.
      - alert: RadarPipelineSilent
        expr: |
          sum(rate(radar_pipeline_runs_total[1h])) == 0
        for: 1h
        labels:
          severity: page
        annotations:
          summary: "AI Radar pipeline has not run for 1h"
          runbook: "https://github.com/free-revalution/AI-Opportunity-Radar/blob/main/docs/RUNBOOK.md#n8n-cron-stuck"

      # 2. A specific stage is failing more than 30% of the time over 5m.
      - alert: RadarPipelineStageFailureRate
        expr: |
          (
            sum by (stage) (rate(radar_pipeline_runs_total{outcome="error"}[5m]))
            /
            sum by (stage) (rate(radar_pipeline_runs_total[5m]))
          ) > 0.3
        for: 10m
        labels:
          severity: page
        annotations:
          summary: "Stage {{ $labels.stage }} failing >30% of runs"
          runbook: "https://github.com/free-revalution/AI-Opportunity-Radar/blob/main/docs/RUNBOOK.md#first-responder-checklist"

      # 3. Web-data provider errors are spiking.
      - alert: RadarWebDataErrors
        expr: |
          sum by (provider) (rate(radar_web_data_requests_total{outcome="error"}[10m])) > 0.1
        for: 10m
        labels:
          severity: warn
        annotations:
          summary: "Web-data provider {{ $labels.provider }} erroring >6/min"

      # 4. Research job queue depth growing.
      - alert: RadarResearchQueueBacklog
        expr: radar_research_jobs_pending > 50
        for: 30m
        labels:
          severity: warn
        annotations:
          summary: "Research queue has {{ $value }} pending jobs"

      # 5. Research p95 latency over 5 minutes.
      - alert: RadarResearchP95Slow
        expr: |
          histogram_quantile(0.95, sum by (le) (rate(radar_research_job_duration_seconds_bucket[5m]))) > 180
        for: 15m
        labels:
          severity: warn
        annotations:
          summary: "Research p95 latency >3min for 15m"

      # 6. Postgres unreachable from the backend.
      - alert: RadarPostgresDown
        expr: |
          sum(rate(radar_pipeline_runs_total{outcome="error",kind="OperationalError"}[5m])) > 0.05
        for: 5m
        labels:
          severity: page
        annotations:
          summary: "Backend cannot reach Postgres"
          runbook: "https://github.com/free-revalution/AI-Opportunity-Radar/blob/main/docs/RUNBOOK.md#postgres-unreachable"

      # 7. Notifications failing.
      - alert: RadarNotificationFailureRate
        expr: |
          (
            sum(rate(radar_notifications_total{outcome="error"}[10m]))
            /
            sum(rate(radar_notifications_total[10m]))
          ) > 0.2
        for: 15m
        labels:
          severity: warn
        annotations:
          summary: "Telegram notifications failing >20%"
          runbook: "https://github.com/free-revalution/AI-Opportunity-Radar/blob/main/docs/RUNBOOK.md#telegram-send-failure"

      # 8. The HTTP middleware sees rising 5xx.
      - alert: RadarHttp5xx
        expr: |
          (
            sum by (path) (rate(radar_http_requests_total{status=~"5.."}[5m]))
            /
            sum by (path) (rate(radar_http_requests_total[5m]))
          ) > 0.05
        for: 10m
        labels:
          severity: page
        annotations:
          summary: "Path {{ $labels.path }} returning >5% 5xx"
```

## Routing

Pipe the alerts into Alertmanager, then to your channel of choice
(Slack, PagerDuty, OpsGenie). Severity mapping:

- `severity: page` — wake the on-call.
- `severity: warn` — Slack channel during business hours, batch digest at night.

## Tuning

These thresholds are deliberately conservative — tune after a week of
production data. Two quick knobs:

- `RadarPipelineSilent` — drop the `for: 1h` to `for: 30m` if your
  pipeline runs more often than once a day.
- `RadarResearchP95Slow` — the 180s p95 is sized for our default
  `DEEP_RESEARCH_MAX_URLS=20`. Increase if you've raised that limit.

## Dashboards

`docs/RUNBOOK.md` already lists the metrics; for a Grafana dashboard,
build panels around:

- Row 1: pipeline rate (sum by stage), pipeline failure rate (errors / total).
- Row 2: web-data rate by provider + outcome, web-data error rate by provider.
- Row 3: research p50/p95 latency histogram, queue depth gauge.
- Row 4: notification success rate, notification error rate by provider.
- Row 5: HTTP request rate by path + status code.

(Sample JSON dashboard is deliberately not shipped — Grafana dashboards
should reflect your own alerting paths.)