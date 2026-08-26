# AI Opportunity Radar(AI 机会雷达)

> 全球 AI 商业机会雷达 — 每日发现、评分与深度研究高价值的 AI 产品机会。

本仓库包含产品的**完整源码**。我们**不内置**任何第三方项目;所有开源组件都以外部服务形式消费。详见 [docs/THIRD_PARTY_AUDIT.md](docs/THIRD_PARTY_AUDIT.md)。

---

## 状态

| 阶段 | 范围 | 状态 |
|---|---|---|
| 1 | 项目初始化 / Docker / 后端骨架 / 前端骨架 / 测试 | **已完成** |
| 2 | Alembic 迁移 + 仓储层 | **已完成** |
| 3 | 数据源连接器(GitHub、Reddit、HN、Product Hunt、RSS) | **已完成** |
| 4 | 去重 + 聚类 | **已完成** |
| 5 | AI 筛选 | **已完成** |
| 6 | 机会评分(公式已在代码中实现) | **已完成** |
| 7 | 深度研究引擎 | **已完成** |
| 8 | Telegram 通知 | **已完成** |
| 9 | 仪表盘打磨 | **已完成** |
| 10 | n8n 工作流 | **已完成** |
| 11 | Browser Use 集成 | **已完成** |
| 12 | 监控 + 运维 | **已完成** |

---

## 架构

```
互联网
  └── 数据源连接器(GitHub、Reddit、HN、Product Hunt、RSS、…)
        └── PostgreSQL 中的 RawItem 行
              └── AI 筛选(LLM)
                    └── Signal 行
                          └── 机会评分(确定性公式)
                                └── 深度研究(LLM + Firecrawl + Browser Use*)
                                      └── ResearchReport 行
                                            └── Telegram 摘要(每日 02:00 UTC)
                                            └── Next.js 仪表盘

\* Phase 11 — `FallbackWebDataProvider` 组合器在出现任何 `ExternalServiceError` 时,
会自动从 Browser Use → Firecrawl → 离线 Mock 降级。

† Phase 12 — 后端在 `/api/metrics` 暴露 Prometheus 文本格式,并在
`/api/health/ready` 提供严格的就绪探针。详见
[`docs/RUNBOOK.md`](docs/RUNBOOK.md) + [`docs/ALERTS.md`](docs/ALERTS.md)。
```

五个独立的开源项目**以服务形式消费**:

| 组件 | 仓库 | 许可证 | 模式 |
|---|---|---|---|
| n8n | [n8n-io/n8n](https://github.com/n8n-io/n8n) | Sustainable Use License | 自托管 Docker,仅作为编排器 |
| Firecrawl | [firecrawl/firecrawl](https://github.com/firecrawl/firecrawl) | AGPL-3.0 | 仅托管 REST API |
| Browser Use | [browser-use/browser-use](https://github.com/browser-use/browser-use) | MIT | 优先使用云 API |
| TrendRadar | [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) | MIT(需核实) | 仅参考其模式 |
| Deep Research | [dzhng/deep-research](https://github.com/dzhng/deep-research) | MIT | 在我们的后端重写实现 |

---

## 技术栈

- **后端** — Python 3.12、FastAPI、SQLAlchemy 2(async)、asyncpg、Alembic、structlog、Pydantic v2。
- **前端** — Next.js 14(App Router)、TypeScript、Tailwind、自研 shadcn 风格 UI 原语。
- **数据库** — PostgreSQL 16。
- **缓存 / 队列** — Redis 7。
- **编排** — n8n(Sustainable Use License)。
- **测试** — pytest + httpx TestClient(后端),Vitest + Testing Library(前端)。

---

## 仓库结构

```
.
├── backend/             FastAPI 服务(api/、models/、services/、tests/)
│   ├── app/
│   ├── alembic/         迁移环境
│   ├── fixtures/        种子数据
│   └── Dockerfile
├── frontend/            Next.js 仪表盘
│   └── src/
│       ├── app/         App Router 页面
│       ├── components/  UI 组件
│       ├── lib/         API 客户端 + 工具函数
│       └── types/       共享 TS 类型
├── n8n/
│   └── workflows/       运行时导入到 n8n 容器
├── docs/                架构、审计、部署
├── docker-compose.yml   Postgres + Redis + Backend + Worker + Frontend + n8n
├── Makefile             便捷命令
├── .env.example         复制为 `.env` 并填入
└── README.md            (本文档)
```

---

## 快速开始(本地)

```bash
# 1. 安装前置依赖
#    - Docker Desktop
#    - Python 3.12(用于本地工具)
#    - Node 20+(用于本地工具)

# 2. 克隆并配置
cp .env.example .env
# (可选)编辑 .env 并填入真实 API Key

# 3. 启动栈
make docker-up
# 或:docker compose up -d --build

# 4. 打开
#    后端:         http://localhost:8000/docs
#    前端:         http://localhost:3000
#    n8n:          http://localhost:5678  (admin / change-me)
#    Postgres:     localhost:5432  (radar / radar)
#    Redis:        localhost:6379
```

## 运行测试

```bash
make test
# 或分别运行:
make test-backend
make test-frontend
```

## 常用命令

```bash
make help              # 列出所有 target
make dev               # 本地运行后端 + 前端(无 Docker)
make migrate           # 运行 alembic 迁移
make seed              # 载入演示机会数据
make docker-down       # 关闭栈(卷会保留)
make docker-logs       # tail docker 日志
make clean             # 清理缓存 / 构建产物
```

---

## 环境变量

详见 [.env.example](.env.example)。关键开关:

| 变量 | 是否必填 | 用途 |
|---|---|---|
| `DATABASE_URL` | 是(生产) | asyncpg DSN |
| `REDIS_URL` | 是(生产) | redis DSN |
| `MINIMAX_API_KEY` | 默认 LLM | 智谱 GLM(`glm-4.7` / `glm-4-flash`),OpenAI 兼容 |
| `OPENAI_API_KEY` | 备选 LLM | 当 `LLM_DEFAULT_PROVIDER=openai` 时启用 |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | 备选 LLM | 需 `pip install -e .[anthropic]` / `[gemini]` |
| `FIRECRAWL_API_KEY` | 可选 | Web 数据层 |
| `BROWSER_USE_API_KEY` | 可选 | 浏览器交互层(Phase 11) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 可选 | 每日摘要推送 |
| `N8N_BASE_URL` / `N8N_API_KEY` | 可选 | 工作流编排 |
| `MOCK_EXTERNAL_SERVICES` | 是(本地) | 为 true 时各服务使用 fixture 数据 |

## 可观测性(Phase 12)

| 端点 | 用途 |
|---|---|
| `GET /api/health/live` | 存活探针 — 进程存活始终返回 200 |
| `GET /api/health/ready` | 就绪探针 — 仅在 Postgres + Redis 健康时返回 200 |
| `GET /api/health` | 深度状态 — 逐依赖项报告 |
| `GET /api/metrics` | Prometheus 文本格式导出 — 由你自己的监控栈抓取 |

`make backup`、`make restore`、`make backup-dry`、`make metrics-scrape`
用于日常运维操作。详见 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
和 [`docs/ALERTS.md`](docs/ALERTS.md)。

当 `MOCK_EXTERNAL_SERVICES=true` 且 API Key 缺失时,连接器返回
fixture 数据,这样无需付费外部服务即可开发。

---

## 评分公式(标准)

```
机会总分 =
    趋势速度        × 0.20
  + 需求            × 0.20
  + 变现能力        × 0.20
  + 竞争空白        × 0.15
  + 中国空白        × 0.15
  + 可执行性        × 0.10
```

所有子分数归一化到 0–100。总分保留两位小数。
总分 ≥ 70 触发深度研究;≥ 85 标记为 `strongly_recommend`。

实现见 [`backend/app/services/scoring/scoring.py`](backend/app/services/scoring/scoring.py)。

---

## 安全

- `.env` 已在 gitignore;永不提交真实 Key。
- 所有外部 URL 通过 `assert_safe_url`(SSRF 防护)。
- API Key 与 Token 通过结构化日志层过滤掉。
- Webhook 校验共享密钥头。

---

## 许可证

专有。第三方组件保留各自许可证(见 [docs/THIRD_PARTY_AUDIT.md](docs/THIRD_PARTY_AUDIT.md))。