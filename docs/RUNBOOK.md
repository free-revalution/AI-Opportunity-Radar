# 运维 Runbook

> Phase 12 — AI Opportunity Radar 后端的第一响应手册。
> 在升级任何人之前请先阅读本文档;大多数故障可以在主机上恢复而无需触碰代码。

## 健康端点(速查)

| 端点 | 用途 | 返回 |
|---|---|---|
| `GET /api/health/live` | 存活探针 — 进程是否启动 | 始终 `200 {"status":"alive"}` |
| `GET /api/health/ready` | 就绪探针 — DB + Redis 是否可用 | `200 {"status":"ready"}` 或 `503 {"status":"not_ready"}` |
| `GET /api/health` | 深度健康 — 每个依赖 | `200` 含逐组件状态;永不返回 `5xx` |
| `GET /api/metrics` | Prometheus 文本格式导出 | `200 text/plain` 或 `404`(若禁用) |

- **存活**失败意味着「重启容器」。
- **就绪**失败意味着「停止向该 Pod 发送流量」但**不要**重启(DB 抖动会恢复)。
- **深度健康**失败意味着「调查对应的组件」。

compose 中的 `backend` 健康检查指向 `/api/health`;它容忍 `degraded`。只有 `down` 才把容器标记为 unhealthy。
Kubernetes 用户应配置 liveness → `/api/health/live`、readiness → `/api/health/ready`。

## 第一响应检查清单

1. **收到告警?** 运行 `make docker-logs` 并检查最后一个 `startup` 事件的时间戳。
   若没有 → 进程已死 → `make docker-up`(compose 会自动重启,除非手动停止)。
2. **后端是否存活?** `curl -sf http://localhost:8000/api/health/live`(应返回 200)。
   若返回 5xx → 检查 `docker logs radar-backend`。
3. **流水线卡住了?** `curl -s http://localhost:8000/api/metrics | grep radar_pipeline_runs_total` —
   `stage="research"` 的计数器应在每天凌晨 02:00 UTC 跳动。若 24 小时无增长,
   检查 n8n 容器(`docker logs radar-n8n`)是否有 cron 错误。
4. **外部服务挂了?** `curl -s http://localhost:8000/api/health | jq .components.firecrawl`
   (或 `browser_use`)。Phase 11 的降级链应保证研究任务继续运行 —
   查看 `radar_web_data_requests_total{outcome="error"}` 的错误率。
5. **通知没发出?** `curl -s http://localhost:8000/api/health | jq .components.telegram`。
   若 `degraded`,说明 `.env` 里 `TELEGRAM_BOT_TOKEN` 或 `TELEGRAM_CHAT_ID` 缺失。
6. **DB 满了?** `docker exec radar-postgres psql -U radar -d radar -c "SELECT pg_database_size('radar');"` —
   与卷大小对比。若接近满,清理旧的 `Raw` + `Research` 行或扩容卷。

## 常见故障模式

### Postgres 不可达

**症状:** `/api/health` 返回 `postgres.status=down`,`/api/health/ready` 返回 503,
摄取端点 500。

**恢复:**
```bash
docker logs radar-postgres --tail=200
docker exec radar-postgres pg_isready -U radar -d radar
docker compose restart postgres   # 仅在 pg_isready 反复失败时
```

后端会自动重连(SQLAlchemy `pool_pre_ping=True`)。

### Redis 不可达

**症状:** `/api/health` 返回 `redis.status=down`。后端保持运行但 `aiocache` 读开始直接打到 Postgres。

**恢复:**
```bash
docker logs radar-redis --tail=50
docker exec radar-redis redis-cli ping
docker compose restart redis
```

Redis 使用 AOF 持久化(`--appendonly yes`);容器数据在重启后仍保留。

### Firecrawl 中断

**症状:** `/api/health` 中 `firecrawl.status=degraded`。研究任务变慢但仍能完成。

**恢复:** 无需处理 — Phase 11 的降级链会尝试 Browser Use,然后 Mock。
查看 `radar_web_data_requests_total{outcome="error",provider="firecrawl"}` 的速率;
若持续走高,提交 Firecrawl 工单。

### Browser Use 中断

与 Firecrawl 类似 — 链路降级到 Mock。Mock 返回 fixture URL,所以研究仍能完成(来源质量下降)。
预期 `radar_web_data_requests_total` 中 `provider="mock"` 的比例会上升。

### Telegram 发送失败

**症状:** 日志中出现 `notification_failed` 事件;`/api/notifications/history` 中有 `error=...` 的行。

**恢复:** 验证 `.env` 中的 bot token。手动冒烟测试:
```bash
curl -sf "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"
```
若机器人有响应则 token 正常 — 检查 chat ID。

### n8n cron 卡住

**症状:** 24 小时内没有新的 `radar_pipeline_runs_total{stage=...}` 样本。

**恢复:**
```bash
docker logs radar-n8n --tail=100
docker exec radar-n8n n8n export:workflow --all
make n8n-validate    # 确认 JSON 文件仍可解析
make n8n-sync        # 重新推入容器
```

若 n8n 已死,后端仍可通过直接 HTTP 调用工作
(`curl -X POST http://localhost:8000/api/internal/discovery/run`)。
在 n8n 恢复之前你可以手动触发流水线。

## 备份与恢复

### 夜间备份

```bash
make backup
```
通过 `docker exec radar-postgres pg_dump` 把 `./backups/radar-<UTC-时间戳>.sql` 写入本地。

要自动化,在主机上添加一条 cron 条目:
```cron
0 3 * * * cd /srv/radar && make backup
```

### 在新主机上恢复

```bash
docker compose up -d postgres
# 等待 postgres 变为 healthy(docker compose ps)
make restore -- --file=./backups/radar-20260826T030000Z.sql
```

### 验证备份

```bash
# 快速冒烟:
grep -c '^CREATE TABLE' ./backups/radar-20260826T030000Z.sql
# 预期 ~10+(alembic 迁移 + DB 元数据)。
```

更严格的检查:
```bash
docker exec -i radar-postgres psql -U radar -d radar_restore < ./backups/radar-20260826T030000Z.sql
# (起一个使用不同 DB 名的第二个容器来隔离测试)
```

## 可观测性速查

`make metrics-scrape` 从实时端点 grep 出前 40 行 `radar_*`。完整列表:

- `radar_pipeline_runs_total{stage,outcome,kind}` — 批量端点跳动计数器。
- `radar_pipeline_duration_seconds{stage}` — `run_once` 的 wall-clock 直方图。
- `radar_web_data_requests_total{provider,op,outcome,chain}` — 每个提供方的尝试。
- `radar_external_service_errors_total{provider,kind}` — 粗粒度错误计数。
- `radar_notifications_total{kind,provider,outcome}` — Telegram 投递结果。
- `radar_research_job_duration_seconds` — 每个研究任务的 wall-clock。
- `radar_http_requests_total{method,path,status}` + `radar_http_request_duration_seconds{method,path}` — HTTP 中间件。

告警阈值见 [`ALERTS.md`](ALERTS.md)。

## 升级路径

若上述步骤在 30 分钟内未能恢复服务:

1. 抓取 `docker logs radar-backend --since=1h > /tmp/backend.log`(`radar-postgres`、`radar-n8n` 同理)。
2. 抓取 `/api/health` 和 `/api/metrics` 快照。
3. 若故障涉及 Telegram,抓取 `/api/notifications/history` 中最近的 `Notification` 行。
4. 带着这三份文件呼叫 on-call。