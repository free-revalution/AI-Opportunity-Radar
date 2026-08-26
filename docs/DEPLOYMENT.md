# 部署指南

## 本地开发

```bash
cp .env.example .env
make docker-up
```

栈会启动 Postgres、Redis、FastAPI 后端(支持热重载)、Next.js 开发服务器以及 n8n。
源码目录通过 bind mount 挂载,编辑立即生效。

## 生产(单机,Docker)

1. 准备一台装有 Docker 24+ 的 Linux 主机。
2. 克隆仓库。
3. 复制 `.env.example` 为 `.env` 并填入真实 API Key。
4. 设置 `APP_ENV=prod`、`APP_DEBUG=false`、`APP_SECRET_KEY=<随机值>`。
5. 启动:

   ```bash
   docker compose up -d --build
   docker compose exec backend alembic upgrade head
   docker compose exec backend python -m app.scripts.seed
   ```

6. 在 FastAPI 容器前面用 Caddy 或 nginx 提供 TLS:

   ```
   api.your-domain.com → http://backend:8000
   app.your-domain.com → http://frontend:3000
   ```

7. 配置 Telegram Webhook 指向 `https://api.your-domain.com/api/telegram/webhook`,
   并使用 `TELEGRAM_WEBHOOK_SECRET` 中的密钥。

## 生产(托管 Kubernetes)

每个服务对应一个 Deployment;Postgres 使用托管服务(RDS / Cloud SQL);
Redis 使用 Elasticache。在 backend Deployment 与 worker Deployment 上设置相同的环境变量。

`worker` 容器与 `backend` 容器共享同一镜像,但运行不同命令(`uvicorn` 与 `python -m app.worker`)。

## 可观测性

- `/api/health` — 逐依赖项状态(Postgres、Redis、LLM 提供方、Firecrawl、Browser Use、Telegram、n8n)。
  每个组件报告 `healthy` / `degraded` / `down`。
- `/api/health/live` — Kubernetes 风格的存活探针(无依赖检查)。
  进程存活始终返回 200。
- `/api/health/ready` — Kubernetes 风格的就绪探针。仅当 Postgres + Redis 健康时返回 200,
  否则返回 503。将其用作 `readinessProbe`,让负载均衡器在 DB 抖动时把 Pod 摘出轮转
  而无需重启。
- `/api/metrics` — Prometheus 文本格式导出。运维从自己的 Prometheus 实例抓取此端点。
  该端点按设计无鉴权 — 通过网络策略限制访问。告警规则样例见
  [`ALERTS.md`](ALERTS.md),第一响应手册见 [`RUNBOOK.md`](RUNBOOK.md)。
- stdout 上的结构化 JSON 日志(`structlog`)。可用 `docker compose logs`
  或你常用的日志转发器收集。

## 备份

Postgres 是我们唯一备份的有状态服务:

- `make backup` — 通过 `docker exec pg_dump` 把 `radar-postgres` 容器导出到
  `./backups/radar-<UTC>.sql`。生产环境接入夜间 cron。
- `make restore -- --file=./backups/...sql` — 把一个 dump 恢复到运行中的容器。
  先用 `--dry-run` 查看准确的 `docker exec` 命令而不实际执行。
- `make backup-dry` / `make restore-dry` — 仅打印命令而不执行(cron 调试很有用)。
- Postgres 凭据通过默认 compose 文件传入;对非默认部署用
  `BACKUP_CONTAINER_NAME` + `BACKUP_OUTPUT_DIR` 环境变量覆盖。
- Redis:启用 AOF 持久化(已在 compose 中配置)。
- n8n:应每周运行一次 `n8n export:workflow --all`。

## 升级

```bash
git pull
docker compose pull
docker compose up -d --build
docker compose exec backend alembic upgrade head
```