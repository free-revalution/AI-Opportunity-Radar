"""Content-safety detector — financial / medical / political / illegal /
defamation risk.

Per 下一阶段 #28 + #30 + #107:

> 风险类型至少支持 privacy / pii / copyright / misinformation / defamation
> / illegal_content / financial_advice / medical_advice / political_risk /
> prompt_injection / source_policy.
>
> 第一阶段不做金融产品。如果 Source 出现股票/期货/基金/加密资产/证券,
> 可以作为公开市场信息进行分类,但不得生成「买入 / 卖出 / 目标价 /
> 保证收益 / 明日上涨 / 内幕消息 / 代客交易」。
>
> 如果 AI 生成结果疑似投资建议 → BLOCK / HUMAN_REVIEW。

This module focuses on **content produced by the LLM** (not user input).
For user-input risk we lean on ``prompt_injection.py`` + PII detection.

We deliberately avoid semantic ML — the goal is to catch obvious
template / listicle patterns ("3 stocks to buy now", "guaranteed
10x return") cheaply enough to run inline. Anything subtle is the LLM
judge's job (see ``content_scorer`` for the model-side check).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------
# Financial advice: explicit buy/sell/target-price language in Chinese
# and English. Each pattern is anchored — we don't want to flag a benign
# "investing is risky" educational article. Word boundaries (`\b`) only
# work between ASCII characters; for Chinese keywords we rely on the
# keyword length itself to limit false positives.
_FINANCIAL_ADVICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:买入|加仓|建仓|抄底|满仓|全仓|梭哈)"),
    re.compile(r"(?:卖出|清仓|割肉|止损|止盈|抛售)"),
    re.compile(r"(?:目标价|止损价|止盈价)[:：]?\s*\d"),
    re.compile(r"(?:保证|稳赚|翻倍|十倍|100%)\s*(?:收益|回报|盈利)"),
    re.compile(r"(?:内幕消息|主力动向|庄家|代客理财|代客操盘)"),
    re.compile(r"\b(?:buy|short|sell|long)\s+(?:the\s+)?(?:stock|ticker|coin)\b", re.IGNORECASE),
    re.compile(r"\bguaranteed\s+(?:\d+x|\d+%|returns?)\b", re.IGNORECASE),
    re.compile(r"\btarget\s+price\s*[:=]?\s*\$?\d", re.IGNORECASE),
)

# Medical advice: prescription-style recommendations or diagnostic claims.
_MEDICAL_ADVICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:确诊|诊断|处方|开药|用药剂量|服用方法|停药)"),
    re.compile(r"(?:这病是|你得了|你是\S{0,4}病)"),
    re.compile(r"\bdiagnose(?:d|s)?\s+you\b|\bprescribe\b|\bdosage\b", re.IGNORECASE),
)

# Political risk: a deliberately small set — we want to flag obvious
# political-call-to-action, not "the election was held last week".
_POLITICAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:推翻|打倒|清算|上台|下台|政变|革命|起义)"),
    re.compile(r"\boverthrow\b|\bregime change\b|\binsurrection\b", re.IGNORECASE),
)

# Illegal content: drug synthesis, weapon construction, credential theft,
# hacking instructions. We flag the language pattern, not the topic
# itself — academic discussion of "what is SQL injection" is fine; "here
# is a step-by-step SQL injection tutorial for …" is not.
_ILLEGAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:制作|合成|提炼)\s*(?:冰毒|海洛因|大麻|毒品)"),
    re.compile(r"(?:制造|组装|改装)\s*(?:枪支|炸弹|爆炸物|武器)"),
    re.compile(r"(?:如何|教|步骤)\S{0,8}(?:入侵|破解|绕过|盗号|撞库|洗钱)"),
    re.compile(r"\bhow\s+to\s+(?:make|synthesise)\s+(?:meth|cocaine|fentanyl)\b", re.IGNORECASE),
    re.compile(r"\bstep[- ]by[- ]step\s+(?:guide|tutorial)\s+to\s+(?:hack|crack)\b", re.IGNORECASE),
)

# Defamation: explicit accusations of named real people / companies with
# criminal language. We can't detect every form — false positives here
# are worse than false negatives, so this is a soft signal.
_DEFAMATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:某某|此人|他|她)\s*(?:是|就是)\s*(?:骗子|罪犯|小偷|贪污犯)"),
    re.compile(r"\b\w+\s+is\s+(?:a\s+)?(?:fraud|criminal|thief|scammer)\b", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class SafetyFinding:
    category: str  # financial | medical | political | illegal | defamation
    pattern: str  # regex label (truncated match)
    weight: float


@dataclass(slots=True)
class SafetyScanResult:
    findings: list[SafetyFinding] = field(default_factory=list)
    raw_score: float = 0.0

    @property
    def is_high_risk(self) -> bool:
        # BLOCKED territory for content_safety.
        return self.raw_score >= 0.5


# ---------------------------------------------------------------------------
# Per-category weights
# ---------------------------------------------------------------------------
_CATEGORY_WEIGHTS: dict[str, float] = {
    "financial": 0.55,    # per 下一阶段 #30 — auto-BLOCK on hit
    "medical": 0.45,
    "political": 0.40,
    "illegal": 0.65,      # auto-BLOCK on hit
    "defamation": 0.35,
}


def _scan_category(
    name: str,
    patterns: tuple[re.Pattern[str], ...],
    text: str,
) -> tuple[list[SafetyFinding], float]:
    findings: list[SafetyFinding] = []
    weight = _CATEGORY_WEIGHTS[name]
    for pat in patterns:
        m = pat.search(text)
        if not m:
            continue
        findings.append(
            SafetyFinding(
                category=name,
                pattern=m.group(0)[:60],
                weight=weight,
            )
        )
        # One hit per category is enough — additional hits don't
        # raise the score further (we already exceed 0.5 → BLOCKED).
        break
    return findings, sum(f.weight for f in findings)


def scan_content_safety(text: str) -> SafetyScanResult:
    """Run all content-safety detectors against ``text``.

    Each category contributes at most one finding (the first match wins).
    This keeps the result small and bounded while still surfacing every
    category that tripped.
    """
    if not text:
        return SafetyScanResult()

    findings: list[SafetyFinding] = []
    score = 0.0

    for name, patterns in (
        ("financial", _FINANCIAL_ADVICE_PATTERNS),
        ("medical", _MEDICAL_ADVICE_PATTERNS),
        ("political", _POLITICAL_PATTERNS),
        ("illegal", _ILLEGAL_PATTERNS),
        ("defamation", _DEFAMATION_PATTERNS),
    ):
        cat_findings, cat_score = _scan_category(name, patterns, text)
        findings.extend(cat_findings)
        score += cat_score

    return SafetyScanResult(findings=findings, raw_score=min(1.0, score))


__all__ = [
    "SafetyFinding",
    "SafetyScanResult",
    "scan_content_safety",
]