# Content Radar — 第一个垂直 Agent

> Content Radar 是 V2.0 的第一个垂直领域,目标客户是抖音/小红书 / B 站 / 微信
> 公众号 / 自媒体创作者。详见 ``docs/下一阶段开发技术方案.md`` §3 / §15-17。

## 1. 核心架构

```
Signal (V2: 正在发生的变化)
    ↓
ContentRadarAgent.analyze(signal, context)
    ↓
ContentOpportunity (V2: 创作者能用的素材)
    ↓
content_generator/* (Phase 8: wechat_article + 3 渠道)
    ↓
ComplianceService.check_content(generated)            ← 合规闸口
    ↓
Publisher.publish(piece)                               ← 一键发布
```

`ContentOpportunity` 是 Signal 与生成内容之间的**垂直解释层**(per docs §5)。
一个 Signal 可以衍生多个 ContentOpportunity(不同 platform / audience / niche)。

## 2. Vertical Agent Protocol

`backend/app/services/agents/base.py`:

```python
class VerticalAgent(Protocol):
    name: str

    async def analyze(
        self,
        signal: Any,
        context: VerticalContext,
        *,
        report: Any | None = None,
    ) -> VerticalResult: ...
```

`VerticalContext` 携带用户画像(platform / audience / niche / tone / language),
供 agent 调 prompt 时使用。

## 3. ContentRadarAgent

Phase 12F 落地的是 **`HeuristicContentRadarAgent`** — 确定性、不调 LLM,
作为 baseline + LLM 不可用时的 fallback。LLM 版本在 Phase 13。

heuristic agent 从 Signal 自身的字段直接投影:

- `Signal.title` → `ContentOpportunity.title`
- `Signal.summary` → `ContentOpportunity.hook`(第一句)
- `Signal.keyword` + `Signal.signal_type` → 3 个 `title_candidates`
- 平台 + audience → `script_outline`(4 段 30s 口播)+ `material_ideas` + `cta`
- `Signal.risk_score` 或 `ResearchReport.confidence` → `risk_warning`

输出永远是 `draft` 状态,confidence 上限 0.7(heuristic 不能伪造事实)。

## 4. 使用方式

```python
from app.services.agents import HeuristicContentRadarAgent, VerticalContext

agent = HeuristicContentRadarAgent()
result = await agent.analyze(
    signal=signal_row,                 # Signal ORM row or dict
    context=VerticalContext(
        platform="douyin",
        audience="普通职场人",
        niche="AI 工具",
        tone="通俗",
    ),
    report=report_row,                  # optional ResearchReport
)
# result.payload 包含 title / hook / title_candidates / script_outline /
#                material_ideas / cta / risk_warning / recommended_length
# result.confidence ∈ [0.0, 0.7]
```

## 5. ContentOpportunity 表

```sql
id, signal_id (FK signals.id),
platform (douyin | xiaohongshu | bilibili | wechat | general),
audience, niche, tone,
content_angle, hook, title_candidates (JSON), material_ideas (JSON),
script_outline, recommended_length,
cta, risk_warning,
content_score (0..100),
status (draft | approved | published | archived),
created_at, updated_at
```

per docs §11。Phase 12C migration `6e4c2d9b3a5f` 创建。

## 6. 多源验证

`signal_sources` join 表(Phase 12C)记录 Signal 的每个 RawItem 来源:

```sql
signal_id, raw_item_id, relevance, evidence_type, added_at
```

`Signal.source_count` = `SELECT COUNT(*) FROM signal_sources WHERE signal_id = ?`,
用于 `evidence_from_source_count()` 计算 Evidence Score:

| source_count | Evidence Score |
|---|---|
| 0 | 0 |
| 1 | 30 |
| 2 | 60 |
| 3 | 85 |
| 4+ | 95+ |

## 7. Signal Score (per docs §7)

```
Signal Score =
    Freshness              × 0.20
  + Velocity               × 0.20
  + Evidence Confidence    × 0.20
  + Novelty                × 0.15
  + Commercial Value       × 0.10
  + Actionability          × 0.10
  + Information Scarcity   × 0.05
```

分档:`<50 LOW | 50-69 WATCH | 70-84 HOT | 85+ BREAKING`

`app.services.signals.compute_signal_score()` 是单一入口。

## 8. 内容窗口

`Signal.expiration_time` 标记内容窗口结束(per docs §14):

- `FLASH`     0–6h
- `HOT`       6–48h
- `TREND`     2–14d
- `LONG_TERM` 14d+

Content Radar 重点是 FLASH / HOT。

## 9. 风险 / 合规

`ContentRadarAgent.risk_warning` 字段始终填充,从不静默空字符串。
触发条件:

- `Signal.risk_score > 0.3` → 提示合规风险需审核
- `ResearchReport.confidence < 0.5` → 提示研究置信度不足
- 其他情况 → 默认"发布前请核对原始来源链接"

## 10. 输出合规

`HeuristicContentRadarAgent` 的输出永远不含:

- 完整文章 / 完整新闻 / 完整帖子 — per docs §21
- 任何买入/卖出/医疗处方/政治号召 — per docs §28

合规兜底由 `ComplianceService.check_content()` 在生成完成后做
(`docs/COMPLIANCE.md` §15)。

## 11. 测试

| 测试文件 | 用例数 |
|---|---|
| `tests/test_agents.py` | 24 |
| `tests/test_signal_score.py` | 31 |

## 12. 下一步

- Phase 13: `LLMContentRadarAgent` — 调 LLM 的版本,共享 `VerticalAgent` 协议。
- Phase 14: 多平台信号聚合(同时 3 个 source 触发同一个 Signal)。
- Phase 15: Signal Feedback 闭环(`SignalFeedback` 模型 + 偏好权重)。