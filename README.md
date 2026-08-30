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
Product Hunt / RSS         + scoring             + 📁 每日报告 Docx
                            + deep research       (4 段结构 云文档)
                              (Firecrawl +
                               Browser Use)
```

The pipeline runs on a **two-tier schedule** via n8n:

- **Every 30 minutes** (`incremental-30min.json`) — runs the cheap
  4-step loop `discovery → clustering → scoring → screening`. No
  LLM-heavy steps. Stays inside cost budget even at full source fan-out.
- **Once daily at 08:00 Asia/Shanghai** (`daily-pipeline-8am.json`)
  — triggers the full pipeline + digest + 每日报告 Docx write via
  `POST /api/internal/pipeline/run {"send_digest": true, "write_docx": true}`.

Failure of any step is recorded in the `runs` table; the bot's
`/status` command surfaces the last run summary plus the
**今日采集 → URL 去重 → 聚类** funnel so operators can spot
dedup-vs-clustering regressions at a glance.

---

## Feishu 双向配置 checklist (生产环境)

飞书机器人的**双向**交互是控制核心——`/run`、`/status`、`/help` 都依赖飞书把用户的 IM 消息 POST 回 `/api/feishu/event`,再由后端把回复发回原 chat。请逐项核对:

1. **创建飞书 App**
   - 打开 [飞书开放平台](https://open.feishu.cn) → 创建机器人应用
   - 记录 `App ID` + `App Secret`,填入 `.env` 的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`

2. **开启事件订阅**
   - App 详情 → 事件订阅 → 请求 URL 填 `https://<your-domain>/api/feishu/event`
   - 加密策略:**HTTPS** + 可选 **AES-256-CBC 加密** (`FEISHU_ENCRYPT_KEY`)
   - 订阅事件 `im.message.receive_v1`(必须)

3. **添加机器人能力**
   - 应用 → 机器人 → 启用 → 设置权限 `im:message`, `im:message:send_as_bot`
   - 把机器人拉进目标飞书群 → 群里发任意消息 → 机器人应自动回复 `/help`

4. **配置 webhook 兜底(可选)**
   - 群机器人 → 自定义机器人 → 复制 webhook URL → `FEISHU_WEBHOOK_URL`
   - 该 URL 在 App 鉴权失败时作为兜底通道(自动降级)

5. **后台 `/run` 防超时**
   - Phase 23 起 `/run` 改成异步:`task_runner.submit_pipeline_run()` 立即返回 `task_id`,完成后通过 IM 把结果推回原 chat
   - 飞书 IM 30s 超时,后台任务最长 30 min

6. **凭据校验**
   - 启动时 `app.main` 在 staging/prod 检查 `FEISHU_APP_ID + FEISHU_APP_SECRET` 同时存在,缺失则 logger.warning
   - 测试时设 `APP_ENV=local` 可跳过校验

7. **n8n 入站鉴权**
   - n8n workflow 的每个 HTTP 节点必须带 `X-Radar-Webhook: $RADAR_WEBHOOK_SECRET`
   - 后端 `internal.py` 拒绝无该 header 的 `/api/internal/*` 请求

---

## 飞书云文档 4 段结构 (Phase 25 v2.1)

每日 `08:00` cron 会把日报同时写到飞书云盘,落地到一个固定的 4 段结构:

```
📁 <FEISHU_DRIVE_ROOT_FOLDER_TOKEN>           ← 在飞书云盘创建并复制 token
├── 📌 首页
├── 📅 今日
├── 📁 每日报告
│   ├── 2026-08-30/
│   │   └── 2026-08-30 AI 商业日报.docx
│   ├── 2026-08-29/
│   │   └── 2026-08-29 AI 商业日报.docx
│   └── …
└── 📚 信息源
```

操作步骤:

1. 飞书云盘 → 新建文件夹(任意命名)→ 复制 token → `FEISHU_DRIVE_ROOT_FOLDER_TOKEN=`
2. `make migrate` → 新增 `daily_digest_docs` 表(date PK + doc_id + doc_url + folder_token)
3. 启动后端,调用 `GET /api/internal/docs/tree` → 自动 ensure 4 个子段(幂等)
4. 手动触发完整 pipeline:
   ```bash
   curl -X POST http://localhost:8000/api/internal/pipeline/run \
        -H "X-Radar-Webhook: $RADAR_WEBHOOK_SECRET" \
        -d '{"send_digest": true, "write_docx": true}'
   ```
5. 查询某天的 docx:`GET /api/internal/docs/daily?date=2026-08-30`

`write_docx: true` 时,`run_pipeline` 会额外:
- 调 `DriveOrgService.write_daily_digest()` 写当日 Docx
- 持久化 `DailyDigestDoc` 行(date PK,FK 到 runs.id)
- 在 `run_pipeline` 的响应里返回 `docx: {date, doc_id, doc_url, folder_token}`

若 `FEISHU_DRIVE_ROOT_FOLDER_TOKEN` 未配置,`write_docx` 会被静默跳过并返回 `{error: "..."}`,不会阻断 pipeline。

---

## 信息源列表

MVP 默认开启 12 个源(`.env` 的 `ENABLED_SOURCES`):

| Slug | 类型 | 说明 |
|---|---|---|
| `github` | API | 公开 repos,按 stars 排序 |
| `reddit` | API | r/MachineLearning 等 |
| `hackernews` | API | Top stories |
| `producthunt` | API | 每日发布 |
| `rss` | RSS | 15 条订阅(财富/华尔街见闻/FT/36氪/虎嗅/亿邦/投资界/CNBC/Reuters/Verge 等) |
| `arxiv` | API | cs.AI / cs.CL 最新论文 |
| `huggingface` | API | models leaderboard(按下载量) |
| `douyin` | API | 抖音热搜榜 |
| `weibo` | API | 微博热搜(需代理) |
| `zhihu` | API | 知乎热榜(需代理) |
| `amazon_best` | RSS | Amazon Best Sellers(多类目) |
| `wallstreetcn_hot` | API | 华尔街见闻热门文章 |

新源注册到 `backend/app/services/ingestion/registry.py` 即可。Bot 在 `/sources` 命令里报告每个源的 `healthy` / `last_success_at`。

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

The pytest suite has **726 passing tests + 17 skipped** (the skipped
ones are FREEZE dispatcher tests that have been retired with their
implementations — see `experimental/`). The 2 alembic migration
tests are pre-existing SQLite `ON DELETE CASCADE` incompatibilities
in the FREEZE `orders` table (3b7c9d2a1f4e); they do not run in CI.

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
| `FEISHU_DRIVE_ROOT_FOLDER_TOKEN` | optional | 飞书云盘 4 段结构 根目录 |
| `FEISHU_BITABLE_APP_TOKEN` | optional | 多维表格 — 每日报告索引 |
| `FEISHU_BITABLE_OPPORTUNITIES_APP_TOKEN` | optional | 多维表格 — Opportunities 表 |
| `ENABLED_SOURCES` | optional | 逗号分隔的 source slug(默认全开) |
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