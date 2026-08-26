# n8n 工作流

本目录存放工作流的 JSON 导出。每个工作流都是一个轻量的 HTTP 编排器,
调用我们的 FastAPI 后端的 `/api/internal/...` 端点 — **业务逻辑不放在 n8n 中**。

## 加载工作流

Docker compose 栈会把本目录挂载到 n8n 容器内的 `/tmp/workflows`,同时注入
`RADAR_WEBHOOK_SECRET` 与 `API_BASE_URL_INTERNAL` 环境变量,以便工作流中的
HTTP 节点能对请求签名并通过 docker 网络访问后端。

把本地文件推送到运行中的 n8n 有两种方式:

```bash
# CLI 助手 — 通过 n8n REST API 推送
make n8n-sync               # create-or-update + 激活
make n8n-validate           # 解析所有 JSON,不发起网络请求

# 或者,n8n 在 http://localhost:5678 跑起来后:
# 任选其一:在 n8n UI 中通过「从剪贴板导入」粘贴 JSON
#         或:把文件体 POST 到 n8n API。
```

## 鉴权

每个工作流的 HTTP 节点都发送 `X-Radar-Webhook: $env.RADAR_WEBHOOK_SECRET`。
后端在每个 `/api/internal/*` 端点上校验同一共享密钥(见 `backend/app/api/internal.py`)。

| 侧 | 变量 |
|---|---|
| 后端 | `RADAR_WEBHOOK_SECRET`(或用 `APP_SECRET_KEY` 兜底) |
| n8n | `$env.RADAR_WEBHOOK_SECRET` |
| CLI 助手 | `N8N_BASE_URL` + `N8N_API_KEY` |

若两侧都未配置,后端接受所有请求 — 便于本地开发,**生产环境绝不能如此**。

## 内置工作流

| 文件 | 触发器 | 用途 |
|---|---|---|
| `daily-opportunity-discovery.json` | Cron `0 2 * * *` UTC | 发现 → 聚类 → 筛选 → 评分 → 深度研究 → 摘要/Telegram |
| `research-opportunity.json` | Webhook `POST /webhook/research-opportunity` | 运行某一个特定研究任务,然后发送 Telegram 通知 |
| `manual-pipeline.json` | Webhook `POST /webhook/manual-pipeline` | 运维按需触发完整流水线(或任一子集) |

每日工作流是唯一默认 `active: true` 出厂启用的工作流。两个 webhook 工作流出厂为
`active: false` — 验证好密钥连通性后,通过 `make n8n-sync` 打开。

## 后端契约

每个工作流都调用以下 `POST /api/internal/...` 端点之一。它们都不会变更数据库之外的
任何状态,并且全部在任务级别幂等(重跑一次发现或聚类是安全的)。

| 端点 | Body | 说明 |
|---|---|---|
| `/api/internal/discovery/run` | `{"sources": [...], "mock": false}` | 从已启用的数据源摄取新条目 |
| `/api/internal/clustering/run` | `{"raw_item_limit": 500, "threshold": 0.82}` | Embedding + 聚类未聚类的 RawItem |
| `/api/internal/screening/run` | `{"limit": 50, "use_mock": false}` | 按机会调用 LLM 筛选 |
| `/api/internal/scoring/run` | `{"limit": 200, "trigger_threshold": 70}` | 确定性评分 + 研究资格门槛 |
| `/api/internal/research/run` | `{"limit": 10, "max_urls": 20, "use_mock_web": false, "use_mock_llm": false}` | 处理每个待处理的研究任务 |
| `/api/internal/research/run/{id}` | `{"use_mock_web": false, "use_mock_llm": false}` | 单任务变体(`research-opportunity` 使用) |
| `/api/internal/notifications/digest/send` | `{"max_entries": 5, "min_score": 70.0, "dry_run": false}` | 构建并发送 Telegram 每日摘要 |
| `/api/internal/notifications/opportunity/{id}/send` | `{"extra_note": "..."}` | 针对一条刚完成的研究报告发送 Telegram 通知 |

## 校验工作流

`backend/tests/test_n8n_workflows.py` 作为 `make test-backend` 的一部分运行,
并断言本目录中每个文件:

1. 是含 `name`、`nodes`、`connections` 的合法 JSON
2. 节点 `id` 唯一
3. 任何 schedule 触发器都有真实的 `cronExpression`(不允许空 `interval`)
4. HTTP Request 节点只命中 `/api/internal/...` URL
5. 这些请求都用 `X-Radar-Webhook: $env.RADAR_WEBHOOK_SECRET` 签名
6. 只引用 `nodes` 中真实存在的节点名

把新工作流丢进本目录,测试套件会自动帮你校验。