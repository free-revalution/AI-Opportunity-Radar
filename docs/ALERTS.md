# 告警规则

> Phase 12 — AI Opportunity Radar 的 Prometheus 告警规则样例。
> 将其复制到你的 `prometheus.yml` / Alertmanager 配置中;我们刻意不在
> `docker-compose` 中内置 Prometheus 栈(运维可自行选择抓取目标)。

## 抓取目标

添加到你的 `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: radar-backend
    metrics_path: /api/metrics
    static_configs:
      - targets: ['backend:8000']
    scrape_interval: 30s
```

后端在 `GET /api/metrics` 暴露 Prometheus 文本格式。该端点按设计无鉴权 —
通过网络策略限制(白名单 Prometheus IP,或仅对内网入口暴露)。

## 告警规则

```yaml
groups:
  - name: radar-pipeline
    interval: 5m
    rules:
      # 1. 每日流水线已 1 小时未运行。
      - alert: RadarPipelineSilent
        expr: |
          sum(rate(radar_pipeline_runs_total[1h])) == 0
        for: 1h
        labels:
          severity: page
        annotations:
          summary: "AI Radar 流水线已 1 小时未运行"
          runbook: "https://github.com/free-revalution/AI-Opportunity-Radar/blob/main/docs/RUNBOOK.md#n8n-cron-卡住"

      # 2. 某个阶段在 5 分钟窗口内失败率超过 30%。
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
          summary: "阶段 {{ $labels.stage }} 失败率 >30%"
          runbook: "https://github.com/free-revalution/AI-Opportunity-Radar/blob/main/docs/RUNBOOK.md#第一响应检查清单"

      # 3. Web 数据提供方错误激增。
      - alert: RadarWebDataErrors
        expr: |
          sum by (provider) (rate(radar_web_data_requests_total{outcome="error"}[10m])) > 0.1
        for: 10m
        labels:
          severity: warn
        annotations:
          summary: "Web 数据提供方 {{ $labels.provider }} 错误率 >6/min"

      # 4. 研究任务队列堆积。
      - alert: RadarResearchQueueBacklog
        expr: radar_research_jobs_pending > 50
        for: 30m
        labels:
          severity: warn
        annotations:
          summary: "研究队列有 {{ $value }} 个待处理任务"

      # 5. 研究任务 5 分钟 p95 延迟过高。
      - alert: RadarResearchP95Slow
        expr: |
          histogram_quantile(0.95, sum by (le) (rate(radar_research_job_duration_seconds_bucket[5m]))) > 180
        for: 15m
        labels:
          severity: warn
        annotations:
          summary: "研究任务 p95 延迟 >3 分钟,持续 15 分钟"

      # 6. 后端无法访问 Postgres。
      - alert: RadarPostgresDown
        expr: |
          sum(rate(radar_pipeline_runs_total{outcome="error",kind="OperationalError"}[5m])) > 0.05
        for: 5m
        labels:
          severity: page
        annotations:
          summary: "后端无法连接 Postgres"
          runbook: "https://github.com/free-revalution/AI-Opportunity-Radar/blob/main/docs/RUNBOOK.md#postgres-不可达"

      # 7. 通知失败。
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
          summary: "Telegram 通知失败率 >20%"
          runbook: "https://github.com/free-revalution/AI-Opportunity-Radar/blob/main/docs/RUNBOOK.md#telegram-发送失败"

      # 8. HTTP 中间件观察到 5xx 上升。
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
          summary: "路径 {{ $labels.path }} 返回 >5% 5xx"
```

## 路由

把告警接入 Alertmanager,再路由到你选择的通道(Slack、PagerDuty、OpsGenie)。
严重等级映射:

- `severity: page` — 唤醒 on-call。
- `severity: warn` — 工作时间走 Slack 频道,夜间出批量摘要。

## 调优

这些阈值刻意保守 — 跑一周生产数据后再调。两个快速旋钮:

- `RadarPipelineSilent` — 若你的流水线一天运行多次,把 `for: 1h` 调到 `for: 30m`。
- `RadarResearchP95Slow` — 180 秒 p95 是按默认 `DEEP_RESEARCH_MAX_URLS=20` 估的;
  若你调高了这个上限,相应调高阈值。

## 仪表盘

`docs/RUNBOOK.md` 已列出全部指标;若要 Grafana 仪表盘,可围绕以下内容建面板:

- 第 1 行:流水线速率(按 stage 求和)、流水线失败率(错误 / 总数)。
- 第 2 行:Web 数据提供方速率(按结果)、Web 数据错误率(按提供方)。
- 第 3 行:研究任务 p50/p95 延迟直方图、队列深度 Gauge。
- 第 4 行:通知成功率、按提供方划分的通知错误率。
- 第 5 行:按路径 + 状态码划分的 HTTP 请求速率。

(刻意不附带样例 JSON 仪表盘 — Grafana 仪表盘应反映你自己的告警路径。)