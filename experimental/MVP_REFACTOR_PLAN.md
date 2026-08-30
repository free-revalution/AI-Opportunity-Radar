# AI Opportunity Radar — MVP 大幅裁剪方案（Phase A — Audit 报告）

> **状态**：Phase A 完成。**未修改任何代码**。等待用户确认后再进入 Phase C。
>
> **依据**：
> - 简化方案 V1.0：`AI Opportunity Radar MVP 大幅裁剪与代码重构方案.md`（最高优先级指导）
> - 3 份 Explore Agent 审计报告（backend core / backend services+APIs / frontend+n8n+tests+scripts）
> - 直接 Read 的关键源文件
>
> **MVP 唯一目标**：验证「用户是否愿意持续阅读我们整理的商业信息」并「是否愿意为它持续付费」。
>
> **MVP 唯一交付**：「公开信息 → AI整理 → 飞书文档 → 飞书机器人控制」+ 5 个 Bot 命令：`/today /run /status /sources /help`。

---

## §0. 项目当前状态

| 维度 | 现状 |
|---|---|
| **代码阶段** | Phase 24（Compliance Engine + Admin Surface），共 24 次提交 |
| **后端规模** | FastAPI + async SQLAlchemy 2.0 + Alembic + 21 个 service 包 + 12 个 router + 80+ endpoints |
| **前端规模** | Next.js 14 App Router + 26 个页面 + 28 个组件 + 6 个 lib |
| **数据库** | PostgreSQL 16 + 16 个 SQLAlchemy model + 11 个 alembic migration（head `2e3f4a5b6c7d`） |
| **缓存 / 队列** | Redis 7 + DB0 主缓存 / DB1 结果后端（实际未用） |
| **调度** | **只有 n8n**（5 个 workflow）。`backend/app/worker.py` 是 idle 心跳 stub（30s 一次 `worker_tick` 日志，无任何任务） |
| **CI** | **不存在**（无 `.github/workflows`，无任何自动化测试） |
| **测试** | 96 个 backend pytest + 26 个 frontend vitest |
| **Docker** | 6 个服务：postgres / redis / backend / worker / frontend / n8n |

---

## §1. 当前真实架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                              EXTERNAL                                    │
│  ┌────────────┐  ┌──────────┐  ┌─────────────┐  ┌──────────────┐         │
│  │ Feishu Bot │  │ Telegram │  │ n8n Cron    │  │ LLM Providers│         │
│  └─────┬──────┘  └────┬─────┘  └──────┬──────┘  └──────┬───────┘         │
│        │              │               │                 │                 │
│        │ /today etc   │ digest        │ scheduleTrigger │ MiniMax/OpenAI/ │
│        │              │               │ (UTC 02 / 02:30 │ Anthropic/Gemini│
│        │              │               │  / 03:30)      │                 │
└────────┼──────────────┼───────────────┼─────────────────┼─────────────────┘
         │              │               │                 │
         ▼              ▼               ▼                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       BACKEND (FastAPI :8000)                            │
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐    │
│  │ /api/feishu │  │ /api/internal/* │  │ /api/admin/*                │    │
│  │ (event cb)  │  │ (cron callback) │  │ (sole-operator console)     │    │
│  └──────┬──────┘  └────────┬────────┘  └──────────────┬──────────────┘    │
│         │                 │                          │                   │
│         ▼                 ▼                          ▼                   │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │              Services  (21 packages, 80+ endpoints)               │    │
│  │  feishu | ingestion | clustering | scoring | screening | signals │    │
│  │  research | compliance | llm | users | notifications | bots      │    │
│  │  audit | subscriptions | activation | publisher | n8n            │    │
│  │  agents | content_generator | content_scorer | browser (empty)   │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                  │                                       │
│                                  ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │              Repositories (8)  +  Models (16)                    │    │
│  │  user | source | raw_item | signal | opportunity | opportunity_   │    │
│  │  source | signal_source | content_opportunity | research_job     │    │
│  │  research_report | notification | subscription | activation_code │    │
│  │  audit_log | system_job | order                                 │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
              ┌──────────────────┴──────────────────┐
              │          PostgreSQL 16               │
              └──────────────────────────────────────┘

Frontend (Next.js :3000) ── HTTP ──► Backend
                                  ⚠  frontend 已停止进入 MVP（simplify §49）
```

**未出现在主流程但被部署**：
- `worker` Docker 服务（仅心跳，无任务）
- 11 个 alembic migration 中 3 个只支撑 FREEZE 模块（`activation_codes`、`orders`、`user.subscription_*` 镜像字段）

---

## §2. KEEP 模块（MVP 主流程必需）

### 2.1 Services（按 simplify §4 11 个核心模块映射）

| simplify §4 模块 | 当前实现 | 关键文件 | 备注 |
|---|---|---|---|
| 1. Source Collector | `services/ingestion/` | `sources/*.py`（github / hn / reddit / producthunt / rss） | KEEP |
| 2. RawItem | `services/ingestion/collectors.py` + `RawItem` model | `models/__init__.py:130-151` | KEEP |
| 3. Deduplication | `services/ingestion/dedup.py` | URL hash + 标题归一化 | KEEP（simplify §19 字面要求） |
| 4. AI Processing | `services/llm/` + `services/signals/processor.py` | MiniMax/OpenAI/Anthropic provider + JSON schema | KEEP（simplify §22-25） |
| 5. Signal | `Signal` model + `services/signals/` | simplify §20 简化为 9 字段；当前 27 字段，**只保留 KEEP 子集** | KEEP（精简字段） |
| 6. Feishu Writer | `services/feishu/docx.py` + `bitable.py` | Docx 文档 + Bitable 多维表 | KEEP |
| 7. Feishu Bot | `services/feishu/inbound.py` | 5 个 MVP 命令 dispatcher | KEEP（**当前仅 2/5 命令可用**） |
| 8. Scheduler | `n8n/workflows/daily-opportunity-discovery.json` + `feishu-daily-digest.json` | UTC 02:00 / 02:30 | KEEP |
| 9. 基础数据库 | `sources / raw_items / signals / runs` | **需新增 `runs` 表**（当前不存在，simplify §37 要求） | KEEP + 缺失 |
| 10. 基础日志 | `app/utils/logger.py` + `structlog` | INFO/WARNING/ERROR | KEEP（simplify §44） |
| 11. 基础 Compliance | `services/compliance/` | Source Policy A-E + 5 detector + 4 risk level | KEEP（simplify §39-41） |

### 2.2 Models（KEEP 主表）

| # | Model | 当前字段数 | MVP 必需字段 | 备注 |
|---|---|---|---|---|
| 1 | User | 17 | `id`, `email`, `feishu_open_id`, `created_at` | KEEP — simplify §38 冻结 `subscription_*` / `preferences_json` / `vertical/niche/platform/audience/tone/language` 等复杂 User Preference |
| 2 | Source | 16 | 全部 16（compliance 字段必需） | KEEP — compliance gate 依赖 |
| 3 | RawItem | 12 | 全部 12 | KEEP |
| 4 | Signal | 27 | simplify §20 字面 9 字段：`id / title / summary / why_it_matters / business_angle / category / source_urls / published_at / created_at` | KEEP — **停止写其他 18 个 V2 字段**，但**不删列** |
| 5 | Opportunity | 30+ | simplify §20 同 Signal（V2 字段停止写） | KEEP |
| 6 | OpportunitySource | 3 | 全部 | KEEP |
| 7 | Notification | 6 | 全部 | KEEP — `/status` 显示 run 状态 |
| 8 | AuditLog | 9 | 全部 | KEEP — compliance trail |
| **NEW** | **Run** | — | simplify §37：`run_id / started_at / finished_at / status / raw_count / new_count / signal_count / error` | **缺失 — 需新增 model + migration** |

### 2.3 API endpoints（KEEP）

| Router | URL | 用途 | simplify §对应 |
|---|---|---|---|
| `/api/health` `health_router` | liveness | simplify §34 |
| `/api/health/ready` `readiness_router` | readiness | KEEP |
| `/api/opportunities` `opportunities_router` | 公开列表 | KEEP |
| `/api/sources` `sources_router` | 公开源列表（`/sources` 命令用） | simplify §14 |
| `/api/feishu/event` `feishu_inbound_router` | Feishu bot 事件回调 | simplify §31-32 |
| `/api/internal/discovery/run` `internal_router` | n8n cron 触发 | simplify §12 |
| `/api/internal/notifications/digest/send` `internal_router` | n8n daily digest | KEEP |
| `/api/internal/pipeline/run` `internal_router`（**新增**） | `/run` 命令 → 全管线 | **缺失** |
| `/api/internal/status` `internal_router`（**新增**） | `/status` 命令 → run 表摘要 | **缺失** |
| `/api/internal/sources/healthy` `internal_router`（**新增**） | `/sources` 命令 → 源健康状态 | **缺失** |
| `/api/metrics` `metrics_router` | Prometheus 抓取 | KEEP（ops） |

### 2.4 Bot 命令（KEEP — simplify §10 规定的 5 个）

| 命令 | 当前状态 | 缺口 | 修复方案 |
|---|---|---|---|
| `/today` | ✅ **已实现**（`feishu/inbound.py` + paywall SADD quota） | 无 | 直接 KEEP |
| `/help` | ✅ **已实现**（返回菜单） | 无 | 直接 KEEP |
| `/run` | ❌ **未实现** | 需新 dispatcher + `/api/internal/pipeline/run` endpoint | **Phase C 新增** |
| `/status` | ❌ **未实现** | 需新 dispatcher + `/api/internal/status` endpoint（读 `runs` 表） | **Phase C 新增** |
| `/sources` | ❌ **未实现** | 需新 dispatcher + `/api/internal/sources/healthy` endpoint（轮询每个 source 的 `last_collected_at`） | **Phase C 新增** |

### 2.5 n8n workflows（KEEP）

| Workflow | Cron (UTC) | 触发器 | simplify §对应 |
|---|---|---|---|
| `daily-opportunity-discovery.json` | `0 2 * * *` | scheduleTrigger | simplify §34（每日 08:00 CST） |
| `feishu-daily-digest.json` | `30 2 * * *` | scheduleTrigger | simplify §34（08:30 CST） |
| `manual-pipeline.json` | webhook | 操作员手动 | simplify §35 |
| `research-opportunity.json` | webhook | 操作员手动 | KEEP（可选） |

---

## §3. FREEZE 模块（simplify §5 + §38 冻结清单）

### 3.1 完全冻结（迁移到 `experimental/`）

| 模块 | 当前代码 | 当前 caller | simplify 引用 |
|---|---|---|---|
| Subscription | `models/Subscription` + `services/subscriptions/` + `services/subscriptions/paywall.py` | `feishu/inbound.py` paywall gate | §5 / §38 / §50 |
| ActivationCode | `models/ActivationCode` + `services/activation/` + `/api/admin/activation/*` | admin 面板 | §5 / §38 |
| Paywall (Redis SADD/INCR quota) | `services/subscriptions/paywall.py` | `/today` `/top` `/search` 等 | §5 / §51 |
| Order | `models/Order` + `repositories/orders.py` + `/api/internal/orders/*` | admin 销售看板 | §5 / §38 |
| Renewal | `services/notification/subscription_renewal.py` + `/api/internal/subscriptions/send_renewal_reminders` | n8n cron | §5 |
| ContentRadar (4 generator) | `services/content_generator/`（wechat / xhs / xianyu / daily） | Content Center V2 | §19 / §52 |
| ContentOpportunity | `models/ContentOpportunity` + `services/content_scorer/` + `services/agents/` | Content Radar V2 | §19 / §38 |
| Publisher | `services/publisher/` (wechat_mp / xhs / xianyu / feishu_bot stub) | `/api/internal/content/publish` | §5 |
| ResearchJob | `models/ResearchJob` + `services/research/` (on_demand) | `/api/research/{id}` | §5 / §54 |
| ResearchReport | `models/ResearchReport` + `services/research/synthesizer.py` | docx/bitable 写报告 | §5 / §54 |
| 复杂 User Preference | `User.vertical/niche/platform/audience/tone/language/preferences_json` 7 字段 | `/preferences` 命令 | §5 / §38 |
| 复杂 Recommendation | `services/signals/consolidator.py` + `SignalSource` model | `/api/signals` admin | §5 |
| 复杂 Admin Dashboard | `/api/admin/*` 20 endpoints + `frontend/app/admin/*` 11 页 | sole-operator console | §5 / §17 |
| 复杂 Analytics | `services/scoring/` V2 + `Signal.sub_score_*` 7 列 | 多维评分 | §5 / §21 |
| 复杂 Notification | `services/notification/telegram.py` + `telegram_adapter.py` | Telegram digest 推送 | §5 |
| SignalSource | `models/SignalSource` | `signals/consolidator.py` | §5 |
| ContentOpportunity metadata | `models/ContentOpportunity.metadata_json` | admin 内容机会页 | §5 |
| User subscription mirror | `User.subscription_status/expires_at` 2 列（migration `9e1c8a7f5b3d`） | `/preferences` 镜像 | §38 |

### 3.2 部分冻结（保留代码但停用）

| 模块 | 当前状态 | 冻结策略 |
|---|---|---|
| `backend/app/worker.py` | idle heartbeat stub | docker 服务保留作为 placeholder，**代码保留但加 deprecation note** |
| `app/api/trends.py` | hard-coded fixture | 保留但停用，标 `deprecated` |
| `frontend/` 整个目录 | Next.js 14 + 26 页 + 28 组件 | simplify §49「**暂停 Frontend**，不进入部署」 → 整目录移到 `experimental/frontend/` |
| 5 个 n8n workflows 中的 `subscription-renewal-reminders.json` | n8n FREEZE | 删除该文件 |

### 3.3 配置 / 部署项（FREEZE 后可清理）

| 项 | simplify 引用 | 处置 |
|---|---|---|
| `config.py` `telegram_*` 3 keys | §5 | 保留（注释「FREEZE — unused」） |
| `config.py` `n8n_base_url` / `n8n_api_key` | §5 | 保留（n8n 服务 KEEP，但 `scripts/n8n_sync.py` 移 experimental） |
| `config.py` Phase 23 `subscription_renewal_*` 4 keys | §5 | 清理 |
| `config.py` `send_activation_code_via_im` | §5 | 清理 |
| `config.py` `feishu_drive_root_folder_token` / `feishu_bitable_*` 3 keys | §5 | 保留（docx/bitable 仍 KEEP） |
| `docker-compose.yml` `worker` 服务 | §5 | 保留为 placeholder，加注释 |
| `postgres/init/01-create-databases.sh` n8n DB 创建 | §5 | 保留（n8n KEEP） |

---

## §4. REMOVE 候选（无功能价值死代码）

| 路径 | 行数 | 理由 | 处置 |
|---|---|---|---|
| `backend/app/services/browser/__init__.py` | 0 | **空文件** | **直接删** |
| `backend/app/services/signals/detector.py` | 0 | **空文件** | **直接删** |
| `backend/app/services/signals/service.py` | 0 | **空文件** | **直接删** |
| `backend/app/models/SystemJob` | ~10 行 + migration `b715c3f3259b:71-82` | grep 验证零引用 | **从 model 删除 + 新增 down-migration** |
| `backend/app/api/trends.py` | ~40 行 | hard-coded fixture 不读 DB | **FREEZE**（在 router 注释「deprecated」 + `_DEPRECATED = True` 标记，不删以免破坏测试） |
| `frontend/src/app/settings/page.tsx` | ~15 行 | 注释明确「stub」 | **直接删** |
| `frontend/src/lib/auth.ts` | ~30 行 | 只被 FREEZE admin login 使用 | 移到 `experimental/frontend/src/lib/auth.ts` |
| `frontend/src/lib/adminCrud.ts` | ~50 行 | 只被 FREEZE admin 面板使用 | 移到 experimental |
| `frontend/src/lib/auditLogs.ts` | ~30 行 | 只被 FREEZE admin 面板使用 | 移到 experimental |
| `frontend/src/lib/contentOpportunities.ts` | ~50 行 | 只被 FREEZE 内容中心使用 | 移到 experimental |
| `frontend/src/components/{AdminGuard,ActivationCodesPanel,AuditLogsPanel,CompliancePanel,ContentCenter,ContentEditor,ContentOpportunitiesPanel,ContentOpportunityDetail,ContentPieceCard,ContentVersionHistory,DashboardPanel,MessagesPanel,NotificationHistory,OnDemandPanel,OrderDialog,OrdersPanel,QualityBadge,SignalsPanel,SourcesPanel,SubscriptionsPanel}.tsx` | ~20 文件 | 仅 FREEZE 页面使用 | 移到 `experimental/frontend/src/components/` |
| `frontend/src/app/admin/**` 11 个 page | ~11 文件 | 仅 FREEZE admin | 移到 `experimental/frontend/src/app/admin/` |
| `frontend/src/app/content-center/**` | 1 文件 | FREEZE | 移到 experimental |
| `frontend/src/app/orders/**` | 1 文件 | FREEZE | 移到 experimental |
| `frontend/src/app/on-demand/**` | 1 文件 | FREEZE | 移到 experimental |
| `frontend/src/app/dashboard/**` | 1 文件 | FREEZE | 移到 experimental |
| `frontend/package.json` 未使用依赖 | 4 个 | `swr` / `zod` / `class-variance-authority` / `lucide-react` 全部 grep 零 import | 从 deps 移除 |
| `n8n/workflows/subscription-renewal-reminders.json` | 1 文件 | FREEZE | 移到 `experimental/n8n/workflows/` |
| `backend/tests/` 47 个 FREEZE-feature 测试 | 47 文件 | 随 FREEZE 代码归档 | 移到 `experimental/backend/tests/` |
| `frontend/tests/` admin / content / signals / orders / compliance / dashboard / content-opportunities 测试 | ~8 文件 | 随 FREEZE frontend 归档 | 移到 `experimental/frontend/tests/` |

> ⚠ **不删除 .py / .ts 文件的实际行**（除上面 3 个空文件 + 1 个 stub page.tsx）。**整目录移动**到 `experimental/`（per simplify §6「不要立即删除大量代码」）。

---

## §5. MVP 最短执行路径（simplify §一/§二十八）

```text
┌──────────────────────────────────────────────────────────────────┐
│ Public Sources                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐  │
│  │  Hacker  │ │  Reddit  │ │  GitHub  │ │ Product │ │   RSS   │  │
│  │   News   │ │          │ │          │ │  Hunt   │ │         │  │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘ └────┬────┘ └────┬────┘  │
└────────┼────────────┼────────────┼───────────┼───────────┼────────┘
         │            │            │           │           │
         └────────────┴─────┬──────┴───────────┴───────────┘
                            ▼
              ┌─────────────────────────┐
              │     Source Collector    │  services/ingestion/
              │  (no auth-bypass /      │
              │   stop on 403/429/      │
              │   CAPTCHA/PAYWALL)      │  simplify §17
              └──────────┬──────────────┘
                         ▼
                     RawItem  (id / source / source_url / title / content /
                                published_at / collected_at / author / metadata)
                         │  simplify §18
                         ▼
              ┌─────────────────────────┐
              │   Clean / Dedup         │  services/ingestion/dedup.py
              │  URL hash + title norm  │  simplify §19
              └──────────┬──────────────┘
                         ▼
                     Signal  (id / title / summary / why_it_matters /
                              business_angle / category / source_urls /
                              published_at / created_at)
                         │  simplify §20
                         ▼
              ┌─────────────────────────┐
              │    AI Processor         │  services/llm/ + signals/processor
              │  keep / category /      │  simplify §22-25
              │  summary / why /        │
              │  business / importance  │
              └──────────┬──────────────┘
                         ▼
              ┌─────────────────────────┐
              │   Feishu Writer         │  services/feishu/docx.py
              │  Docx + Bitable         │  simplify §26-30
              └──────────┬──────────────┘
                         ▼
                    Feishu Doc
                         ▲
                         │
              ┌──────────┴──────────────┐
              │     Feishu Bot          │  services/feishu/inbound.py
              │  /today /run /status    │
              │  /sources /help         │  simplify §10
              └─────────────────────────┘
                         ▲
                         │
              ┌──────────┴──────────────┐
              │     Scheduler           │  n8n (KEEP) 或 Python cron
              │  08:00 daily + manual   │  simplify §34-35
              └─────────────────────────┘
                         │
                         ▼
              ┌─────────────────────────┐
              │      Runs (新增表)       │  simplify §37
              │  run_id / started_at /   │
              │  finished_at / status /  │
              │  raw_count / new_count / │
              │  signal_count / error   │
              └─────────────────────────┘
```

**关键路径上的 5 个 KEEP 节点**：
1. Source Collector
2. Clean / Dedup
3. AI Processor
4. Feishu Writer
5. Feishu Bot（5 命令）

**支撑层**：
- 基础数据库（`sources / raw_items / signals / runs`）
- 基础日志
- 基础 Compliance（5 detector）

---

## §6. 当前 Feishu 功能 vs MVP 需求（simplify §二十九 第 9 项）

### 6.1 当前 Feishu Bot 命令（`backend/app/services/feishu/inbound.py:267-297` `_COMMAND_ALK`）

```text
/help, /today, /top, /research, /refresh, /score, /daily, /report,
/doc, /table, /activate, /search, /preferences, /content
```

14 个命令，其中**只有 2 个是 MVP 必需**。

### 6.2 MVP vs 当前对照

| MVP 命令（simplify §10） | 当前状态 | 缺口 |
|---|---|---|
| `/today` | ✅ 完整（返回今日 top signals + 文档链接） | 无 |
| `/help` | ✅ 完整（返回菜单） | 无 |
| `/run` | ❌ 无 dispatcher | **Phase C 新增**：调用 `/api/internal/pipeline/run` → 全管线 → 异步 |
| `/status` | ❌ 无 dispatcher | **Phase C 新增**：调用 `/api/internal/status` → 读 `runs` 表 + 健康状态 |
| `/sources` | ❌ 无 dispatcher | **Phase C 新增**：调用 `/api/internal/sources/healthy` → 每个 source 的 `last_collected_at` |

### 6.3 飞书文档写入（simplify §26-30）

| 能力 | 当前实现 | MVP 满足？ |
|---|---|---|
| Docx 文档写入 | `services/feishu/docx.py`（Phase 7） | ✅ |
| Bitable 多维表 | `services/feishu/bitable.py`（Phase 7） | ✅ |
| 每日报告格式（simplify §28） | 当前 `notifications/format_digest.py` 已生成「TOP 1 / 发生了什么 / 为什么 / 商业意义 / 来源」格式 | ✅ |
| 失败通知（simplify §43） | `notifications/service.py` 已支持 | ✅ |
| 加密事件（Phase 25） | `inbound.py:174-177` 是 stub | ⚠ Phase C 实现 AES-256-CBC + SHA256 |
| Token verify | 已实现（`verify_event`） | ✅ |

**结论**：Feishu 写入与 bot 调度框架已就绪，**仅缺 3 个 bot 命令 dispatcher + 1 个加密事件 stub 实现**。

---

## §7. 当前自动采集 vs MVP 需求（simplify §二十九 第 10 项）

### 7.1 simplify §16/§17 要求

| 要求 | 当前状态 | 满足？ |
|---|---|---|
| GitHub / HN / Reddit / Product Hunt | `services/ingestion/{github,hackernews,reddit,producthunt}.py` 已实现 | ✅ |
| RSS | `services/ingestion/rss.py` | ✅ |
| 禁止绕登录 / 验证码 / 反爬 / 盗 Cookie / 破解 API / 付费墙（simplify §17） | ingest 严格用 `403 / 429 / CAPTCHA / LOGIN_REQUIRED / PAYWALL` 停止（simplify §17 字面要求） | ✅ |
| 多源 → RawItem 统一 | `RawItem` 12 字段符合 simplify §18 字面要求 | ✅ |

### 7.2 缺口

| 缺口 | 修复 |
|---|---|
| 无 `runs` 表记录每次采集状态 | **Phase C 新增 `Run` model + migration**（simplify §37） |
| `Source.last_collected_at` 字段缺失 | `Source` 当前 16 字段无此列；**Phase C 加列**或停止检查（simplify §14 `/sources` 命令只显示「5 / 5 healthy」即可） |
| Compliance gate 接入采集（Phase 24 已实现） | ✅ `services/compliance/service.py` 已挂 |

**结论**：自动采集基本满足 MVP，**仅缺 `runs` 表 + 源健康检查字段**。

---

## §8. 当前 Scheduler vs MVP 需求（simplify §二十九 第 11 项）

### 8.1 simplify §34-35 要求

| 要求 | 当前实现 | 满足？ |
|---|---|---|
| 每天 08:00 CST 定时（UTC 00:00） | n8n `daily-opportunity-discovery.json` cron `0 2 * * *` UTC = **10:00 CST**，不是 08:00 | ⚠ 时间**不符**，需调整为 UTC 0 = 08:00 CST |
| 手动执行（`/run`） | `manual-pipeline.json` webhook + `research-opportunity.json` webhook | ✅ 框架已有，**只缺 `/run` 命令 dispatch 到 webhook** |
| 同一 service 调用 | n8n 都打 `/api/internal/*` endpoint | ✅ |

### 8.2 缺口

| 缺口 | 修复 |
|---|---|
| 08:00 CST 时间错误 | **Phase C 调整 cron 为 `0 0 * * *` UTC** |
| `/run` 命令触发全管线 | **Phase C 新增 dispatcher + webhook URL env** |
| `/status` / `/sources` 命令无对应 cron / endpoint | **Phase C 新增 `/api/internal/status` + `/api/internal/sources/healthy`** |

### 8.3 替代方案（Python cron）

如要彻底摆脱 n8n：
- 用 `apscheduler` + `asyncio` 在 `app/main.py` lifespan 启动 1 个 scheduler
- 移除 `n8n` docker 服务 + 5 个 workflow + `scripts/n8n_sync.py` + `Makefile n8n-validate / n8n-sync`

**建议保留 n8n**（操作员手动触发的两个 webhook workflow 仍有用）。

**结论**：Scheduler 框架已就绪，**仅需修时间 + 补 3 个 endpoint + 3 个 bot dispatcher**。

---

## §9. 数据库 / Migration 评估（simplify §二十九 第 8 项）

### 9.1 当前 schema 状态

```text
Linear chain: b715c3f3259b → 2a31b2e94c5f → 3b7c9d2a1f4e → 4c9e2a8f1b3d
            → 5d3b1f7a8c2e → 6e4c2d9b3a5f → 7f8a9b6c1d2e → 8a1b9d2e5f6c
            → 9e1c8a7f5b3d → f7a2c9d4e8b1 → 2e3f4a5b6c7d
Head: 2e3f4a5b6c7d
```

### 9.2 MVP 必需变更

| 变更 | 必要性 | Migration |
|---|---|---|
| 新增 `runs` 表（simplify §37） | **必需** | **新增 alembic migration** |
| `sources.last_collected_at` 字段（`/sources` 命令用） | 可选（可只用 existing `last_compliance_check`） | 可选 |
| 删除 `system_jobs` 表 | REMOVE 候选（dead table） | **新增 down-migration**（per simplify §6「不立即删除代码」可保留但 stop using） |

### 9.3 FREEZE 表（停止使用，不删除）

| 表 | 当前数据 | simplify §对应 |
|---|---|---|
| `orders` | FREEZE-only commerce | §5 / §38 |
| `activation_codes` | FREEZE-only commerce | §5 / §38 |
| `signal_sources` | FREEZE-only | §5 |
| `content_opportunities` | FREEZE-only | §38 |

**结论**：**只需要 1 个新 migration（建 `runs` 表）**。其他 schema 不动，仅停止使用 FREEZE 表。

---

## §10. 当前测试情况（simplify §二十九 第 12 项）

### 10.1 当前测试覆盖

| 套件 | 文件数 | KEEP 路径 | FREEZE 路径 |
|---|---|---|---|
| `backend/tests/` | 96 | 49 | 47 |
| `frontend/tests/` | 26 | ~18 | ~8 |
| `backend/tests/test_n8n_workflows.py` | 1 | KEEP（workflow 契约） | — |

### 10.2 KEEP 测试路径（simplify §Phase D 要求）

| 测试文件 | 覆盖 | MVP 必需？ |
|---|---|---|
| `test_ingestion_service.py` + `test_connectors.py` | Collector | ✅ |
| `test_clustering_service.py` + `test_embedding.py` | Dedup | ✅ |
| `test_signals_api.py` / `test_signal_consolidator.py` / `test_signal_score.py` | Signal（部分 FREEZE） | 部分 |
| `test_scoring*.py` | Scoring V2（部分 FREEZE） | 部分 |
| `test_screening*.py` | Screening | ✅ |
| `test_research_*.py` | Research（FREEZE） | ❌ 随 FREEZE 归档 |
| `test_notification_formatting.py` / `test_notification_service.py` | Notification（部分 FREEZE） | 部分 |
| `test_feishu_inbound.py` | Feishu 事件解析 | ✅ |
| `test_feishu.py` / `test_telegram_provider.py` | Feishu / Telegram 客户端（部分 FREEZE） | 部分 |
| `test_compliance*.py` / `test_pii.py` / `test_prompt_injection.py` / `test_content_safety.py` / `test_copyright_risk.py` / `test_source_policy.py` | Compliance | ✅（基础 compliance KEEP） |
| `test_pipeline_instrumentation.py` | Pipeline 指标 | ✅ |
| `test_health_probes.py` / `test_readiness.py` | Health | ✅ |
| `test_n8n_workflows.py` | n8n 契约 | ✅ |

### 10.3 缺口（simplify §Phase D 必须测试）

| 缺口 | 修复 |
|---|---|
| `/today` e2e 测试 | 当前覆盖在 `test_feishu_inbound.py` + `test_notification_formatting.py`（部分） |
| `/run` e2e 测试 | **缺失** — Phase C 新增 `test_bot_run_command.py` |
| `/status` e2e 测试 | **缺失** — Phase C 新增 `test_bot_status_command.py` |
| `/sources` e2e 测试 | **缺失** — Phase C 新增 `test_bot_sources_command.py` |
| `/help` e2e 测试 | ✅ 已有 `test_router_help_returns_menu` |
| 自动定时任务 e2e | **缺失** — Phase C 新增 `test_daily_pipeline.py`（mock n8n + 验证 pipeline 启动） |
| `runs` 表读写测试 | **缺失** — Phase C 新增 `test_runs_repo.py` |

### 10.4 CI 现状

**没有任何 CI**（无 `.github/workflows`）。所有测试通过 `make test` / `make test-backend` / `make test-frontend` 本地执行。

**结论**：测试基本满足 MVP 核心路径，**缺 4 个 bot 命令 e2e 测试 + 1 个 daily pipeline 测试**。

---

## §11. 预计修改 / 删除 / 冻结文件清单（simplify §二十九 第 6/7 项）

### 11.1 直接删除（4 个）

```text
backend/app/services/browser/__init__.py              # 空文件
backend/app/services/signals/detector.py              # 空文件
backend/app/services/signals/service.py               # 空文件
frontend/src/app/settings/page.tsx                    # stub placeholder
```

### 11.2 整目录移动到 `experimental/`（FREEZE）

```text
experimental/backend/app/services/activation/        # ActivationCode + flow
experimental/backend/app/services/subscriptions/      # Subscription + paywall
experimental/backend/app/services/research/           # ResearchJob + ResearchReport + on_demand
experimental/backend/app/services/content_generator/  # 4 generators
experimental/backend/app/services/content_scorer/     # LLM-as-judge
experimental/backend/app/services/publisher/          # 4 publisher stubs
experimental/backend/app/services/agents/             # ContentRadar + LLM content
experimental/backend/app/repositories/orders.py       # OrderRepository
experimental/backend/app/api/admin/                   # FREEZE admin endpoints
experimental/backend/tests/                           # 47 个 FREEZE-feature 测试

experimental/frontend/                                # 整个 frontend 目录
experimental/frontend/src/app/admin/                  # admin 11 页
experimental/frontend/src/app/content-center/
experimental/frontend/src/app/orders/
experimental/frontend/src/app/on-demand/
experimental/frontend/src/app/dashboard/
experimental/frontend/src/components/{admin,content}*
experimental/frontend/src/lib/{auth,adminCrud,auditLogs,contentOpportunities}.ts
experimental/frontend/tests/                          # FREEZE 测试

experimental/n8n/workflows/subscription-renewal-reminders.json
```

### 11.3 修改（KEEP 但需调整）

```text
backend/app/main.py                                   # 删除 admin_router + internal_router FREEZE 路由；保留 KEEP 部分
backend/app/api/internal.py                           # 新增 /pipeline/run /status /sources/healthy；删除 orders/activation/renewal
backend/app/api/trends.py                             # 标记 _DEPRECATED = True
backend/app/services/feishu/inbound.py                # 新增 /run /status /sources dispatcher；删除 FREEZE-only command (activate, search, content, preferences, research, top, refresh, score, daily, report, doc, table)
backend/app/services/feishu/inbound.py:174-177        # 实现 AES-256-CBC 加密事件 (Phase 25 F.1)
backend/app/services/feishu/inbound.py:_COMMAND_ALK   # 精简到 5 个 MVP 命令
backend/app/models/__init__.py                        # 新增 Run model；删除 SystemJob
backend/app/services/signals/__init__.py              # 删除空文件 detector.py / service.py 引用
backend/app/services/browser/__init__.py              # 已删除
backend/app/api/admin.py                              # 整个文件移 experimental（FREEZE）
backend/alembic/versions/                             # 新增建 runs 表的 migration + 删除 system_jobs 的 down-migration
backend/app/worker.py                                 # 加 deprecation 注释（KEEP as placeholder）
docker-compose.yml                                    # 注释 frontend + worker 服务；保留 n8n
n8n/workflows/daily-opportunity-discovery.json        # cron 0 2 → 0 0 (08:00 CST)
frontend/package.json                                 # 删除 4 个未使用依赖（swr / zod / class-variance-authority / lucide-react）
Makefile                                              # 删除 n8n-validate / n8n-sync（如果 n8n 也移 experimental）；或保留但移到 experimental Makefile
README.md                                             # 大幅重写（simplify §46）
.env.example                                          # 删除 FREEZE env vars
```

### 11.4 新增文件（Phase C 实施时）

```text
backend/app/models/run.py                             # Run model
backend/alembic/versions/<hash>_runs_table.py         # 新 migration
backend/app/services/pipeline.py                      # /run 命令 → 全管线串联 (collector → dedup → ai → feishu)
backend/app/services/pipeline_status.py               # /status 命令 → 读 runs 表 + 健康
backend/app/services/sources_health.py                # /sources 命令 → 源健康
backend/app/repositories/runs.py                      # RunRepository
backend/tests/test_bot_run_command.py
backend/tests/test_bot_status_command.py
backend/tests/test_bot_sources_command.py
backend/tests/test_runs_repo.py
backend/tests/test_daily_pipeline.py
```

---

## §12. 风险评估

### 12.1 高风险（需 Phase C 谨慎处理）

| 项 | 风险 | 缓解 |
|---|---|---|
| `feishu/inbound.py:_COMMAND_ALK` 精简到 5 命令 | 14 → 5 命令会破坏 `test_feishu_inbound.py` 中 `/research` `/daily` `/refresh` 等用例 | 那些测试随 `experimental/` 一起归档，**主测试集只保留 `/help /today`** |
| `app/main.py` 移除 `admin_router` | 20 个 FREEZE admin endpoint 失效 | FREEZE 测试同时归档 |
| `internal.py` 中 orders/activation/renewal 路由删除 | 影响 `subscription-renewal-reminders.json` n8n workflow | FREEZE 整体归档（workflow 移到 experimental） |
| Phase 25 加密事件实现 | 实现错误会拒所有飞书事件 | 先在 `_encrypted_event_stub` 单测，再 e2e 测试 |

### 12.2 中风险

| 项 | 风险 |
|---|---|
| 新增 `runs` 表 migration | 11 个 migration 链是线性的，新 migration 接在 `2e3f4a5b6c7d` 之后 |
| `n8n cron 0 2 → 0 0 UTC` 调整 | 08:00 CST → 实际 UTC 00:00，需确认 `feishu-daily-digest.json` cron `30 2 → 30 0` |
| 删除 `SystemJob` model | 已零引用，但需检查是否有外部 SQL / SQLAlchemy reflection 依赖 |

### 12.3 低风险

| 项 | 风险 |
|---|---|
| 3 个空 Python 文件删除 | 零引用 |
| `frontend/src/app/settings/page.tsx` 删除 | stub |
| 4 个未使用 npm 依赖删除 | grep 验证零 import |
| 文档 / README 重写 | 纯文本 |

### 12.4 无风险

- 单纯目录移动（mv 而非删除）
- FREEZE 测试归档（实验性代码有实验性测试）
- 模型字段停止写（不删列）

---

## §13. 不在 Phase A 范围（明确边界）

按 simplify §二十九「第一阶段禁止修改业务代码」：

| 不做 | 原因 |
|---|---|
| ❌ 任何代码修改 | simplify §二十九 |
| ❌ 任何 migration 改动 | 同上 |
| ❌ 任何 docker-compose 改动 | 同上 |
| ❌ 任何 frontend 移动 | 同上 |
| ❌ 任何 backend 目录移动 | 同上 |
| ❌ 删除 git 跟踪的文件 | 同上 |
| ❌ git commit / push | simplify §6「不要立即删除代码」 |
| ❌ Phase B 分类（KEEP/FREEZE/REMOVE 已经是本文件） | — |
| ❌ Phase D 测试 | simplify §Phase D 在 C 之后 |
| ❌ Phase E 真实数据 24h 测试 | simplify §Phase E |
| ❌ Phase F 5~20 真实用户 | simplify §Phase F |

---

## §14. 决策点（等待用户确认）

按 simplify §二十九「然后：停止。等待用户确认后再修改代码」。

**用户需对以下决策点逐项确认**：

1. **FREEZE 目录策略**：是统一 `experimental/` 还是按 simplify §6 分 `archive/` + `experimental/`？
2. **`worker` docker 服务**：保留（注释 placeholder）还是彻底删？
3. **`n8n` 调度器**：保留（用 n8n cron）还是替换为 Python `apscheduler`？
4. **新增 `runs` 表**：是否在 Phase C 一起做？
5. **删除 4 个 FREEZE 路由（admin/orders/activation/renewal）**：是只从 `app/main.py` 摘掉 import，还是整文件归档到 experimental？
6. **3 个空 Python 文件 + `SystemJob` model**：是否在 Phase C 直接删（含 down-migration）？
7. **Frontend 处理**：是整目录移到 `experimental/frontend/`，还是保留在原位但加 README「MVP 阶段暂停」？
8. **README.md**：是否在 Phase C 重写为「MVP 5 命令 + 飞书 + cron」？
9. **Phase C 实施顺序**：建议
   - C1: 新增 `Run` model + migration + `/api/internal/{pipeline/run,status,sources/healthy}` endpoint + 3 个 bot dispatcher
   - C2: 移动 FREEZE 代码到 experimental/（按 simplify §6「不立即删除」）
   - C3: 删除 3 个空文件 + SystemJob + settings stub + 4 个未使用 npm 依赖
   - C4: Phase 25 F.1 加密事件实现 + F.2 事件幂等（Redis SETNX）
   - C5: 调整 n8n cron 时间 + 测试 5 个 bot 命令 e2e + daily pipeline e2e
   - C6: README 重写 + .env.example 精简

---

## §15. 单一交付物声明

**本文件**：
- 路径：`/Users/jiang/development/AI Opportunity Radar/MVP_REFACTOR_PLAN.md`
- 状态：新建，**不 `git add`**
- 内容来源：3 份 Explore Agent 审计 + 直接 Read 的关键源文件
- 用途：用户审阅 → 逐项确认 §14 决策点 → 进入 Phase C 实施
