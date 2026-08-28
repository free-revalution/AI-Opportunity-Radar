"""Compliance Engine — V2.0 Content Radar 商业化的合规基础.

按照 ``docs/下一阶段开发技术方案.md`` #27 / 商业参考.md "Content Safety Engine" 的
要求,本模块提供一套统一的 ``ComplianceResult`` 体系,把多类风险源(content
safety / PII / copyright / source policy / prompt injection / 金融/医疗/政治
boundary)聚合成 ``allowed / risk_score / risk_types / reason /
requires_human_review`` 五个输出位。**商业化之前必须完成** — 所有面向用户的
LLM 输出在生产路径上都要走一次 ``check()``。

设计原则:

1. **纯函数 + 显式 dataclass**,便于单元测试与离线审计。**不在内部状态里
   持有任何 IO**(PII 全部在内存里做 regex 匹配,无外发请求)。
2. **fail-closed 语义** — 任何异常/timeout/不确定输入 → 默认返回 BLOCKED。
3. **risk_score ∈ [0, 1]**,风险等级由 ``risk_score`` 离散映射到
   ``LOW / MEDIUM / HIGH / BLOCKED``,各 detector 可独立设置阈值常量。
4. **可降级** — 每个 detector 单独 ``try/except`` 包装,一个 detector 抛异常
   不影响其他 detector 的输出。
5. **同 dataclass,多入口** — ``check_content`` / ``check_signals`` / ``check_prompt``
   三个公共 API,内部共享同一套 detector。

Service layout (per 下一阶段 #75):

    services/compliance/
    ├── __init__.py           (re-exports + public API)
    ├── service.py            (ComplianceService — orchestrator)
    ├── content_safety.py     (financial / medical / political / illegal / defamation)
    ├── source_policy.py      (per-source ComplianceLevel check)
    ├── pii_detector.py       (phone / email / ID / card / address regex)
    ├── copyright_risk.py     (long verbatim copy detection)
    └── prompt_injection.py   (system-prompt-override attempt detection)
"""

from __future__ import annotations

from .models import (
    ComplianceLevel,
    ComplianceResult,
    RiskLevel,
    RiskType,
    content_safe_to_publish,
    risk_level_for_score,
)
from .service import ComplianceService, default_service

__all__ = [
    "ComplianceLevel",
    "ComplianceResult",
    "ComplianceService",
    "RiskLevel",
    "RiskType",
    "content_safe_to_publish",
    "default_service",
    "risk_level_for_score",
]