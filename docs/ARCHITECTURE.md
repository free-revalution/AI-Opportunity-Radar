# 架构

## 流水线

```
互联网
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  数据源连接器(asyncio,每个平台一个)                         │
│  ─ GitHubTrendingConnector                                  │
│  ─ RedditConnector(r/SaaS、r/LocalLLaMA、r/SideProject、…)  │
│  ─ HackerNewsConnector(Algolia API)                         │
│  ─ ProductHuntConnector(REST + GraphQL)                     │
│  ─ RSSConnector(feedparser)                                 │
│  ─ YouTubeConnector                                         │
└─────────────────────────────────────────────────────────────┘
   │ 统一的 RawItem 数据类
   ▼
┌─────────────────────────────────────────────────────────────┐
│  去重                                                        │
│  ─ UNIQUE(source_id, external_id)                            │
│  ─ content_hash(对归一化后的标题 + url 做 sha256)             │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  AI 筛选(廉价 LLM,JSON 模式)                                │
│  ─ is_business_relevant                                      │
│  ─ category、problem、potential_business                     │
│  ─ trend_strength、demand_strength                           │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  Embedding + 聚类                                            │
│  ─ pgvector(规划中)或 MVP 阶段使用内存 numpy                  │
│  ─ 相似度阈值来自 EMBEDDING_CLUSTER_THRESHOLD                │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  机会评分(确定性)                                            │
│  ─ 趋势 × 0.20 + 需求 × 0.20 + 变现 × 0.20                  │
│  ─ 竞争空白 × 0.15 + 中国空白 × 0.15 + 可执行性 × 0.10       │
│  ─ total_score ≥ 70 → 触发深度研究                            │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  深度研究                                                    │
│  ─ ResearchContext = 机会 + 源 URL + 文档                    │
│  ─ 迭代式 depth+breadth 循环(最多 20 URL、3 层深度)          │
│  ─ 优先使用 Firecrawl(搜索/抓取)                             │
│  ─ 仅在 JS 重或需登录的目标使用 Browser Use                  │
│  ─ LLM 生成严格 JSON(executive_summary、…)                   │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  通知                                                        │
│  ─ Telegram 机器人(每日摘要 + 研究完成通知)                   │
│  ─ 仪表盘(Next.js 服务端组件从 API 拉取)                      │
└─────────────────────────────────────────────────────────────┘
```

## 进程拓扑

```
docker-compose 栈
├── postgres        (端口 5432)
├── redis           (端口 6379)
├── backend         FastAPI HTTP(端口 8000)
├── worker          python -m app.worker(Phase 3+)
├── frontend        next dev(端口 3000)
└── n8n             编排器(端口 5678)
```

外部(不在 compose 中):

- `api.firecrawl.dev`(Firecrawl REST)
- `api.browser-use.com`(Browser Use 云)
- `api.openai.com` / Anthropic / Gemini(LLM)
- `api.telegram.org`(Telegram 机器人 API)

## 为什么不让 n8n 直接写 Postgres?

n8n 仅调用**我们自己的后端 HTTP API**。业务逻辑、校验与去重始终留在我们的代码中,
这样日后可以把 n8n 替换为普通 cron + worker 而不需要重写系统。

## 为什么评分用纯 Python 实现?

加权公式(README §12)是核心产品 IP,需要在没有基础设施时也能单元测试。
保持其无框架依赖(无 DB、无 FastAPI)意味着测试毫秒级完成,且公式可在单文件中审计。