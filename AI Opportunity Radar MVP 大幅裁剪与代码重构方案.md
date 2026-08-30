可以。基于我们刚才重新收敛的目标，我建议这次不要再写“下一阶段功能开发文档”，而是直接给 Claude Code 一份**减法 + 冻结 + 重构 + MVP 验证**的执行文档。

另外，公开搜索也能看到类似 Radar 项目普遍容易走向“评分、Dashboard、API、个性化、复杂分析”等重型方向；这恰恰说明你现在应该刻意避免继续堆功能。([GitHub][1])

下面这份可以直接复制给 Claude Code。

# AI Opportunity Radar

# MVP 大幅裁剪与代码重构方案 V1.0

> 本文是当前项目的最高优先级产品与技术指导。
>
> **核心原则：停止过度开发，把项目从“复杂商业信号平台”收敛为一个可以在最短时间内验证市场的情报采集与飞书交付 MVP。**

---

# 一、产品重新定义

## 1.1 当前项目真正要解决的问题

项目不是要做：

* SaaS 平台
* AI 新闻网站
* 商业情报数据库
* 全自动内容创作平台
* 用户订阅管理平台
* AI Agent 平台
* 多行业商业智能平台

当前只做：

# 「公开信息 → AI整理 → 飞书文档 → 飞书机器人控制」

---

# 2. MVP 产品定义

最终 MVP：

```text
公开合法信息源
        ↓
定时 / 手动采集
        ↓
清洗
        ↓
去重
        ↓
AI摘要
        ↓
简单分类
        ↓
简单商业价值分析
        ↓
生成日报
        ↓
同步飞书文档
        ↑
        │
   飞书机器人
        │
  控制整个流程
```

到这里：

# STOP。

---

# 3. MVP 唯一验证目标

当前项目不是验证：

> “系统能不能做成一个完整 SaaS？”

而是验证：

> **“用户是否愿意持续阅读我们整理的商业信息？”**

进一步验证：

> **“用户是否愿意为这种信息持续付费？”**

因此：

所有无法帮助验证上述问题的功能，都应该延后。

---

# 二、功能范围重新定义

## 4. MVP 必须保留

只保留以下模块：

```text
1. Source Collector
2. Raw Item
3. Deduplication
4. AI Processing
5. Signal
6. Feishu Writer
7. Feishu Bot
8. Scheduler
9. 基础数据库
10. 基础日志
11. 基础 Compliance
```

---

# 5. MVP 暂时冻结

以下模块：

```text
Subscription
ActivationCode
Paywall
Order
Renewal
ContentRadar
ContentOpportunity
Publisher
ResearchJob
ResearchReport
复杂 User Preference
复杂 Recommendation
复杂 Admin Dashboard
复杂 Analytics
复杂 Notification
```

全部进入：

```text
FROZEN
```

---

# 6. 不要立即删除 Frozen 模块

非常重要。

不要直接删除大量代码。

统一移动到：

```text
archive/
```

或者：

```text
experimental/
```

具体根据当前项目结构决定。

原则：

> 不参与 MVP 主流程，但保留未来恢复能力。

---

# 三、最终 MVP 架构

## 7. 推荐架构

```text
                    ┌──────────────────┐
                    │ Public Sources   │
                    │ RSS/API/合法网页 │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │    Collector     │
                    └────────┬─────────┘
                             ↓
                         RawItem
                             ↓
                    ┌──────────────────┐
                    │ Clean / Dedup    │
                    └────────┬─────────┘
                             ↓
                         Signal
                             ↓
                    ┌──────────────────┐
                    │   AI Processor   │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Feishu Publisher │
                    └────────┬─────────┘
                             ↓
                       Feishu Doc
                             ↑
                             │
                    ┌────────┴─────────┐
                    │   Feishu Bot     │
                    └──────────────────┘
```

---

# 8. MVP 不需要 Frontend

当前项目已有 frontend。

不要继续将其作为普通用户产品。

MVP 阶段：

# 暂停 Frontend。

如果删除成本较高：

```text
frontend/
```

移动到：

```text
experimental/frontend/
```

或者保留但不进入部署。

---

# 9. 为什么暂时不需要 Frontend

用户最终看到的是：

```text
飞书文档
```

用户控制系统：

```text
飞书 Bot
```

开发者控制：

```text
CLI / Docker / Server
```

因此：

```text
Frontend
```

不是 MVP 必需品。

---

# 四、用户使用流程

## 10. MVP 用户流程

第一版甚至不需要注册系统。

用户：

```text
购买 / 获得服务
        ↓
加入指定飞书群 / 使用飞书机器人
        ↓
阅读飞书文档
```

机器人提供：

```text
/today
/run
/status
/sources
/help
```

---

# 11. /today

功能：

获取最近一次生成的日报。

返回：

```text
AI Opportunity Radar

今日商业信号：

1. xxx
2. xxx
3. xxx
4. xxx
5. xxx

完整内容：
[飞书文档链接]
```

---

# 12. /run

功能：

手动执行一次：

```text
Collector
↓
Processing
↓
Feishu
```

执行后：

```text
任务已开始。

预计完成：
XX分钟

完成后会自动通知。
```

---

# 13. /status

返回：

```text
系统状态

Collector: OK
Database: OK
LLM: OK
Feishu: OK

Last Run:
2026-08-30 08:00

Collected:
128

New:
47

Signals:
12

Published:
12
```

---

# 14. /sources

显示：

```text
当前信息源：

✓ Hacker News
✓ Reddit
✓ GitHub
✓ Product Hunt
✓ RSS

状态：

5 / 5 healthy
```

---

# 15. /help

返回所有命令。

---

# 五、信息处理流程

## 16. Collector

第一版只支持：

```text
RSS
官方 API
合法公开网页
```

优先：

```text
GitHub
Hacker News
Reddit
Product Hunt
高质量 RSS
```

---

# 17. 严禁

禁止：

```text
绕登录
绕验证码
绕反爬
盗取 Cookie
破解 API
绕过付费墙
模拟正常用户规避平台限制
```

如果某来源：

```text
403
429
CAPTCHA
LOGIN_REQUIRED
PAYWALL
```

直接停止。

---

# 18. RawItem

第一版保持非常简单：

```python
RawItem:
    id
    source
    source_url
    title
    content
    published_at
    collected_at
    author
    metadata
```

不要加入大量字段。

---

# 19. 去重

第一版使用：

```text
URL hash
+
标题归一化
+
简单文本相似度
```

即可。

不要开发复杂的向量数据库。

---

# 20. Signal

第一版只需要：

```python
Signal:
    id
    title
    summary
    why_it_matters
    business_angle
    category
    source_urls
    published_at
    created_at
```

---

# 21. 不要使用复杂评分体系

当前已有 Signal V2：

```text
Freshness
Velocity
Evidence
Novelty
Commercial Value
Actionability
Scarcity
```

MVP 暂时不要继续扩展。

可以保留已有字段，但对外只输出一个：

```text
importance
```

例如：

```text
HIGH
MEDIUM
LOW
```

---

# 六、AI Processing

## 22. AI 的职责

AI 只做：

```text
1. 判断是否值得保留
2. 摘要
3. 分类
4. 为什么重要
5. 商业意义
```

---

# 23. AI 不做

不要：

```text
自动生成完整营销方案
自动生成10个视频脚本
自动生成商业计划书
自动预测股票
自动给投资建议
自动做深度研究
自动生成复杂用户画像
```

---

# 24. 推荐 AI Prompt

输出必须结构化：

```json
{
  "keep": true,
  "category": "AI",
  "title": "...",
  "summary": "...",
  "why_it_matters": "...",
  "business_angle": "...",
  "importance": "HIGH"
}
```

---

# 25. AI 必须保留来源

任何 Signal：

必须能够追溯到：

```text
source_url
```

禁止：

```text
只有 AI 结论
没有原始来源
```

---

# 七、飞书文档

## 26. 飞书是 MVP 的核心交付层

最终：

```text
AI
↓
Feishu
```

---

# 27. 文档结构

推荐：

```text
AI Opportunity Radar
│
├── 📌 首页
│
├── 📅 今日
│
├── 📁 每日报告
│   ├── 2026-08-30
│   ├── 2026-08-29
│   └── ...
│
└── 📚 信息源
```

---

# 28. 每日报告格式

```text
# AI 商业机会日报
2026-08-30

---

## 🔥 TOP 1

标题：

XXX

### 发生了什么？

XXX

### 为什么值得关注？

XXX

### 商业意义

XXX

### 来源

- source 1
- source 2

---

## 🔥 TOP 2

...
```

---

# 29. 不要把所有 RawItem 写进飞书

这是一个重要原则。

飞书只展示：

# 筛选后的信息。

例如：

```text
今天采集 500 条
↓
去重 350
↓
AI筛选 70
↓
高价值 15
↓
飞书日报 10
```

---

# 30. 飞书文档不是数据库

数据库：

```text
保存完整数据
```

飞书：

```text
面向用户展示
```

不要让飞书承担：

```text
查询数据库
历史数据分析
复杂筛选
用户权限
```

---

# 八、飞书机器人

## 31. Bot 是控制层

机器人不是聊天机器人项目。

第一版只负责：

```text
Command
→ Service
→ Result
```

---

# 32. 第一版支持命令

```text
/today
/run
/status
/sources
/help
```

---

# 33. 自然语言控制暂缓

例如：

```text
“帮我看看今天AI领域有什么机会”
```

暂时不要实现。

等 MVP 验证后再加入。

---

# 九、Scheduler

## 34. 定时任务

每天：

```text
08:00
```

执行：

```text
Collector
↓
Processor
↓
Feishu
```

---

# 35. 支持手动执行

```text
/run
```

调用同一个 service。

不要维护两套逻辑。

---

# 十、数据库

## 36. 数据库保持最小

只需要：

```text
sources
raw_items
signals
runs
```

---

# 37. runs

用于记录：

```text
run_id
started_at
finished_at
status
raw_count
new_count
signal_count
error
```

这样 `/status` 可以直接使用。

---

# 38. 暂时不需要

删除/冻结：

```text
subscriptions
activation_codes
orders
content_opportunities
research_jobs
notifications
user_preferences
```

如果这些表已经存在：

不要为了 MVP 强行删除 migration。

可以暂时停止使用。

---

# 十一、Compliance

## 39. Compliance 保留

虽然是 MVP：

# Compliance 不能删除。

但是：

不要继续扩展成大型规则引擎。

---

# 40. MVP Compliance

至少保证：

```text
Source Policy
Copyright
PII
Illegal Content
Financial
Medical
Political
```

基本过滤。

---

# 41. 核心原则

系统只处理：

# 合法公开信息。

并且：

```text
原文
→ 来源链接
```

不进行：

```text
大规模全文转载
```

而是：

```text
AI摘要
+
来源
```

---

# 十二、错误处理

## 42. 不要静默失败

每一次：

```text
Collector
AI
Feishu
```

失败都写入：

```text
runs
```

---

# 43. 飞书通知

如果自动任务失败：

Bot：

```text
⚠️ Radar任务失败

阶段：
Feishu Publish

错误：
XXX

请检查。
```

---

# 十三、日志

## 44. MVP 只需要基础日志

记录：

```text
INFO
WARNING
ERROR
```

不要开发复杂 Audit System。

---

# 十四、当前项目代码处理原则

## 45. 不要重写核心代码

优先：

```text
复用
删除调用
冻结模块
简化流程
```

而不是：

```text
全部重写
```

---

# 46. 先扫描当前代码

Claude Code 必须首先执行：

```bash
tree -L 4
```

然后检查：

```text
backend/
frontend/
scripts/
docker/
migrations/
```

---

# 47. 搜索过度开发模块

执行：

```bash
grep -R "Subscription" .
grep -R "ActivationCode" .
grep -R "Paywall" .
grep -R "ContentOpportunity" .
grep -R "ResearchJob" .
grep -R "Publisher" .
grep -R "UserPreference" .
```

列出：

```text
文件
调用关系
依赖关系
```

---

# 48. 不要立即删除

先生成：

```text
MVP_REFACTOR_PLAN.md
```

包含：

```text
KEEP
FREEZE
REMOVE
REFACTOR
```

---

# 十五、目录目标

最终目标结构：

```text
AI-Opportunity-Radar/
│
├── backend/
│   ├── app/
│   │   ├── collectors/
│   │   ├── processors/
│   │   ├── models/
│   │   ├── services/
│   │   │   ├── feishu/
│   │   │   ├── signals/
│   │   │   └── compliance/
│   │   ├── bot/
│   │   ├── scheduler/
│   │   └── main.py
│   │
│   └── tests/
│
├── scripts/
│
├── migrations/
│
├── docker-compose.yml
│
├── .env.example
│
├── README.md
│
└── experimental/
```

注意：

这只是目标结构。

不要为了达到这个结构而机械移动大量文件。

优先保持现有代码稳定。

---

# 十六、Frontend 处理

## 49. 当前 frontend

第一阶段：

# 不部署。

但不要立即删除。

移动：

```text
experimental/frontend/
```

如果当前 backend 强依赖 frontend：

则保留原目录，但：

```text
不继续开发
```

---

# 十七、Admin

当前 Admin Dashboard：

# 全部冻结。

原因：

你自己可以：

```text
数据库
+
日志
+
飞书 Bot
```

完成第一阶段运营。

---

# 十八、Subscription

## 50. 全部冻结

包括：

```text
Subscription
Activation
Paywall
Quota
Renewal
Order
```

---

# 51. 为什么冻结

因为当前最重要的问题不是：

> “如何自动管理1000个付费用户？”

而是：

> “有没有第一个愿意付钱的人？”

---

# 十九、Content Radar

## 52. 冻结

当前不要自动生成：

```text
抖音文案
小红书文案
视频脚本
营销方案
```

---

# 53. 未来触发条件

只有出现：

```text
用户明确提出：
“我希望它直接帮我生成内容”
```

才重新启用。

---

# 二十、Research

## 54. Deep Research 暂停

原因：

成本高。

当前：

```text
500条信息
↓
全部Research
```

是错误架构。

---

# 55. MVP 只使用：

```text
RSS/API
+
基础网页内容
+
LLM摘要
```

---

# 二十一、商业验证

## 56. MVP 不是给程序员玩的

部署后必须开始真实测试。

第一批：

```text
5~20人
```

---

# 57. 不需要自动支付

第一阶段可以：

```text
人工收款
```

然后：

```text
人工加入飞书
```

甚至：

```text
人工发送飞书文档
```

都可以。

---

# 58. 为什么？

因为：

# 人工可以验证需求。

软件自动化是：

# 验证需求之后的事情。

---

# 二十二、第一版商业产品

建议测试：

```text
AI海外热点情报
```

而不是：

```text
AI Opportunity Radar SaaS
```

---

# 59. 第一版交付

用户得到：

```text
每日AI商业热点
+
中文摘要
+
来源
+
商业意义
+
飞书文档
```

---

# 60. 不承诺

不要宣传：

```text
保证赚钱
投资建议
股票预测
保证爆款
保证流量
保证选品赚钱
```

---

# 二十三、MVP 核心指标

## 61. 不看代码指标

不要关注：

```text
代码多少行
API多少个
数据库多少张表
```

---

# 62. 只看：

```text
每日阅读人数
每日活跃人数
用户主动查看次数
用户反馈
付费意愿
```

---

# 63. 最重要的三个问题

每个测试用户问：

```text
1. 你今天看了吗？

2. 哪一条最有价值？

3. 如果每个月收费，你愿意多少钱？
```

---

# 64. 第二阶段再做

如果：

```text
用户愿意付费
```

再逐步加入：

```text
Subscription
Activation
Paywall
Content Radar
Personalization
Research
```

---

# 二十四、开发阶段

Claude Code 必须严格按照以下阶段执行。

---

## Phase A — Audit

禁止改代码。

输出：

```text
当前目录
当前模块
当前依赖
当前数据库
当前API
当前Feishu
当前Scheduler
当前测试
```

---

## Phase B — Classification

给所有模块分类：

```text
KEEP
FREEZE
REMOVE
```

---

## Phase C — MVP Refactor

只建立：

```text
Collector
Processor
Signal
Feishu
Bot
Scheduler
Run
```

---

## Phase D — Test

必须测试：

```text
/run
/today
/status
/sources
```

以及：

```text
自动定时任务
```

---

## Phase E — Real Data

使用真实公开数据运行：

```text
至少24小时
```

检查：

```text
数据质量
重复率
LLM成本
飞书稳定性
```

---

## Phase F — User Test

找：

```text
5~20个真实用户
```

---

# 二十五、最终 Definition of Done

MVP 完成条件：

```text
[ ] 可以自动采集公开信息
[ ] 可以去重
[ ] 可以AI摘要
[ ] 可以分类
[ ] 可以生成商业意义
[ ] 可以生成日报
[ ] 可以自动写入飞书
[ ] 可以通过飞书 /run
[ ] 可以通过飞书 /today
[ ] 可以通过飞书 /status
[ ] 可以通过飞书 /sources
[ ] 失败可以通过飞书发现
[ ] 所有信息有来源
[ ] 基础合规检查存在
[ ] 可以连续运行
```

除此之外：

# 全部不是 MVP 必需功能。

---

# 二十六、禁止事项

从现在开始，Claude Code 禁止主动开发：

```text
❌ 新 Dashboard
❌ 新 Landing Page
❌ 新 Subscription
❌ 新 Payment
❌ 新 Activation
❌ 新 User System
❌ 新 Recommendation Engine
❌ 新 Content Generator
❌ 新 Publisher
❌ 新 Deep Research
❌ 新 Multi-Agent System
❌ 新复杂评分模型
❌ 新向量数据库
❌ 新复杂权限系统
❌ 新营销自动化
```

除非用户明确提出。

---

# 二十七、最重要的产品原则

牢记：

> **当前不是把项目做大，而是把项目做小。**

---

> **当前不是证明技术能力，而是验证商业需求。**

---

> **当前不是建立完整 SaaS，而是建立一个可以每天稳定产生有价值信息的机器。**

---

# 二十八、最终产品

如果把整个系统压缩成一句话：

```text
收集公开信息
→ AI筛选整理
→ 自动更新飞书
→ 用户通过飞书Bot控制
```

这就是：

# AI Opportunity Radar MVP

---

# 二十九、Claude Code 执行规则

收到本文件后：

## 第一阶段禁止修改代码。

先执行：

```text
Repository Audit
```

输出：

```text
1. 当前真实架构

2. KEEP 模块

3. FREEZE 模块

4. REMOVE 候选模块

5. MVP 最短执行路径

6. 预计需要修改的文件

7. 预计需要删除/冻结的代码

8. 数据库是否需要 migration

9. 当前 Feishu 功能是否已经满足 MVP

10. 当前自动采集是否已经满足 MVP

11. 当前 Scheduler 是否已经满足 MVP

12. 当前测试情况
```

然后：

# 停止。

等待用户确认后再修改代码。

---

# 三十、最终决策

从现在开始：

```text
复杂度 ↓
功能数量 ↓
代码量 ↓
运营成本 ↓

数据质量 ↑
稳定性 ↑
信息价值 ↑
真实用户 ↑
商业验证 ↑
```

项目成功的标准不是：

> “代码很多。”

而是：

> **“每天自动产生一份用户愿意阅读的高价值信息，并且有人愿意为它付钱。”**

### 我再给你一个更重要的建议

**这次真的不要让 Claude Code 直接执行整份文档。**

先让它只执行：

> `Phase A — Audit`

也就是**只分析、不改代码**。

因为你现在最大的风险就是 Claude Code 又理解成：

> “好的，我马上重构 30 个文件，然后顺便优化一下架构，再加几个功能。”

然后两天以后又变成一个更复杂的系统。

你应该明确告诉它：

> **“本次任务第一阶段禁止修改任何业务代码。只分析当前仓库，并把现有模块分成 KEEP / FREEZE / REMOVE，等待我的确认。”**

等它给你报告之后，我可以再帮你**根据它实际扫描出来的文件逐个判断：哪些删、哪些留、哪些改**。

这一次我们应该反过来做：**先把 80% 的东西砍掉，再写剩下的 20%。**

[1]: https://github.com/DanieleGiovanardi2408/idea-radar?utm_source=chatgpt.com "GitHub - DanieleGiovanardi2408/idea-radar: Finds emerging tech before it saturates — ranks HN / GitHub / RSS signals by momentum, not stars. Semantic dedup, local-LLM insights, live radar UI, fully offline. · GitHub"
