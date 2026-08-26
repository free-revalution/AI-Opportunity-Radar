# 第三方组件审计

> 生成时间:2026-08-26
> 范围:核实 MVP 将依赖的每个开源组件的许可证、集成方式与稳定性。

MVP **不** fork、不内置、不再分发任何这些项目。每一个都以**外部服务 / API** 形式消费,
这样我们可以让自己的代码库保持宽松许可证,并避免 copyleft 污染。

| 项目 | 仓库 | 许可证 | 使用方式 | 风险 |
|---|---|---|---|---|
| Firecrawl | [firecrawl/firecrawl](https://github.com/firecrawl/firecrawl) | **AGPL-3.0** | 托管云 API(REST:`/scrape`、`/crawl`、`/map`、`/search`、`/extract`) | 若自托管并修改则高风险 — MVP 阶段我们不自托管 |
| Browser Use | [browser-use/browser-use](https://github.com/browser-use/browser-use) | MIT(库)/ Cloud TOS(服务) | 优先使用云 API,可降级到自托管 | 低 |
| Deep Research | [dzhng/deep-research](https://github.com/dzhng/deep-research) | MIT | 我们**重写实现**研究循环到后端,而非 vendoring | 低 |
| TrendRadar | [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) | MIT(商用前需核实) | 仅参考其模式 — 我们自己的连接器是独立的 | 中(商用前需核实) |
| n8n | [n8n-io/n8n](https://github.com/n8n-io/n8n) | **Sustainable Use License**(2025 年起) | 仅自托管 Docker 编排 | 不能再作为托管产品对外提供 |

## 详细发现

### Firecrawl — AGPL-3.0

- **风险原因**:AGPL-3.0 要求任何通过网络公开的修改版本必须开源。若自托管并修改其代码,
  会被迫公开内部修改。
- **MVP 策略**:
  - 仅使用**托管 API**(`https://api.firecrawl.dev`)。
  - 永不把 Firecrawl 源码拷入我们的仓库。
  - 所有 Firecrawl 访问都通过一个抽象层(`FirecrawlService`)以便日后切换实现。
- **商用前核实**:复核 Firecrawl LICENSE、托管服务条款和商标指引。Firecrawl 的 API
  可能附带额外的使用限制或品牌要求。

### Browser Use

- **库**(`browser-use` on PyPI):类 MIT 的宽松许可证,可作参考。
- **云服务**(`https://api.browser-use.com`):商业 ToS,MVP 优先使用,因为可省去
  Playwright / Chromium 的运维负担。
- **降级链(强制)**:Browser Use → Firecrawl → 离线 Mock。实现为
  `backend/app/services/research/fallback_provider.py` 中的 `FallbackWebDataProvider`
  组合器 — 每当 Browser Use 抛出 `ExternalServiceError` 即被捕获,并自动尝试下一个提供方。
  链的末端始终是离线 Mock,因此单一厂商故障不会中断研究任务。
- **路线图**:原始审计中规定的 Browser Use → Firecrawl → 原始 HTTP → 跳过 中的
  原始 HTTP 步骤,推迟到后续阶段。

### Deep Research (dzhng/deep-research)

- MIT 许可证,但该项目本身使用 Firecrawl + 兼容 OpenAI 的 LLM,并运行迭代式
  **depth+breadth** 问题循环。
- 我们**不引入**该包。我们在 `backend/app/services/research/` 重写该循环,这样:
  - 可以喂入 `ResearchContext`(已抓取的 URL/文档),避免重复抓取。
  - 可替换底层的搜索 / 爬取提供方。
  - 可控制预算(`max_urls`、`max_depth`、`max_llm_calls`、`max_tokens`)。

### TrendRadar (sansan0/TrendRadar)

- 多平台(11+ 个中文平台:微博、知乎、抖音、Bilibili、今日头条 等)的热点聚合器,
  带 AI 分析与定时推送。
- 我们**不**克隆该项目。我们借鉴其**模式**(关键词可配置的热点采集 + AI 分析 + 定时推送),
  并基于同一套源 API(或 RSS 镜像)在我们自己的数据模型上编写独立连接器。
- 商用前复核许可证。

### n8n

- 2025 年许可证从 Apache 2.0 改为 **Sustainable Use License**。自托管用于内部使用仍被允许,
  但**不能把 n8n 本身作为托管服务对外提供**,也**不能用 n8n 构建竞争性产品**。
- 我们仅把 n8n 当作**工作流编排器**,用来调用我们自己的后端 HTTP API。
  所有业务逻辑(评分、聚类、研究解析)都留在后端。日后若需要去掉 n8n,
  后端仍可通过 cron + worker 进程继续工作。

## 抽象边界

```
我们的后端(类 MIT,我们自己的代码)
    ├── FirecrawlWebDataProvider   ← 调用 firecrawl.dev REST API
    ├── BrowserUseWebDataProvider  ← 调用 api.browser-use.com(或自托管)
    │     ↑ 被 ↓ 包装
    ├── FallbackWebDataProvider    ← BU → Firecrawl → Mock 链(每一步
    │                                捕获 ExternalServiceError)
    ├── ResearchService            ← 我们自己的迭代循环(借鉴 deep-research)
    ├── LLMRouter                  ← OpenAI / Anthropic / Gemini
    ├── 数据源连接器                ← GitHub、Reddit、HN、Product Hunt、RSS、…
    └── TelegramService            ← 仅 bot token,不复制源码
```

每个外部依赖都隐藏在 Python 的 `WebDataProvider`(或 `LLMProvider`、`TelegramProvider`)
接口后面,这样测试可以替换为假实现。