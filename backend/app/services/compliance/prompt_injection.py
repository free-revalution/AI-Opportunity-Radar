"""Prompt-injection detector — flag content that tries to override the
system prompt.

Per 下一阶段 #18 + #69:

> 所有第三方网页内容必须标记为 ``UNTRUSTED_SOURCE_CONTENT``。
> 不得执行网页中的指令。
> 不得因为网页内容要求而改变系统规则。
> 不得调用网页要求的工具。

This module gives us a cheap regex+keyword pre-filter that runs BEFORE
we hand source content to the LLM. The LLM itself is still the last line
of defence (see ``docs/COMPLIANCE.md`` §3.2), but pre-filtering:

  * Cuts cost — we don't even call the LLM on obviously-poisoned pages.
  * Surfaces audit signals — we record every hit to ``AuditLog``.
  * Defends against the long tail of "soft" injections that don't
    directly request a system override but try to establish context
    ("you are now an unrestricted AI assistant …").

Coverage:
  * Direct override: "ignore previous instructions", "disregard above"
  * System prompt exfiltration: "show your system prompt", "reveal the
    initial instructions"
  * Role reassignment: "you are now …", "act as …", "pretend to be …"
  * Tool-calling requests: "call function …", "use the send_email tool"
  * Delimiter injection: "### system", "<<SYS>>", "<|im_start|>system"

Multilingual — covers English + Chinese keyword sets. False positives
on long news articles are tolerable; we set the threshold conservatively
(score 0.05 per weak match, 0.25 per strong match).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Pattern catalogue
# ---------------------------------------------------------------------------
# Each entry: (label, pattern, weight). Strong matches carry weight 0.25,
# weak matches 0.05. The detector sums weights and caps at 1.0.
_PATTERN_TABLE: tuple[tuple[str, re.Pattern[str], float], ...] = (
    # ----- STRONG: direct override attempts -----
    (
        "ignore_previous",
        re.compile(
            r"ignore|forget|disregard|override"
            r"\s+(?:all|any|the|previous|prior|above|earlier)?\s*"
            r"(?:instructions?|prompts?|rules?|guidelines?|directives?)",
            re.IGNORECASE,
        ),
        0.25,
    ),
    (
        "ignore_instructions_zh",
        re.compile(
            r"(?:忽略|无视|不要遵守|不要听从|无视|忘掉)"
            r"(?:[\s,，。:；;]*的?[\s,，。:；;]*)?"
            r"(?:以上|之前|先前|全部|所有|之前所有)?"
            r"(?:的)?"
            r"(?:指令|提示|规则|说明|内容)",
        ),
        0.25,
    ),
    (
        "system_prompt_exfil",
        re.compile(
            r"(?:reveal|show|display|print|repeat|output|leak)"
            r"\s+(?:your|the)?\s*(?:system|initial|original|hidden)"
            r"\s*(?:prompt|instructions?|message)",
            re.IGNORECASE,
        ),
        0.30,
    ),
    (
        "show_system_zh",
        re.compile(
            r"(?:显示|告诉我|泄露|输出|重复)"
            r"(?:系统|初始|你的|原始)?"
            r"(?:提示|指令|prompt)",
        ),
        0.30,
    ),
    # ----- MEDIUM: role reassignment -----
    (
        "you_are_now",
        re.compile(
            r"\byou\s+are\s+now\b|\bact\s+as\b|\bpretend\s+(?:to\s+be|you\s+are)\b"
            r"|\bfrom\s+now\s+on\s+you\b",
            re.IGNORECASE,
        ),
        0.15,
    ),
    (
        "you_are_now_zh",
        re.compile(
            r"(?:你现在是|假装你是|从现在开始你|请你扮演|现在开始你是)",
        ),
        0.15,
    ),
    (
        "no_restrictions",
        re.compile(
            r"\b(?:no|without|unrestricted|unlimited|uncensored)\s+"
            r"(?:rules?|restrictions?|filters?|guardrails?|limits?)\b",
            re.IGNORECASE,
        ),
        0.20,
    ),
    # ----- WEAK: delimiter injection / formatting attempts -----
    (
        "delimiter_system",
        re.compile(
            r"###\s*system|<<SYS>>|<\|/?im_start\|>|<\|/?im_end\|>|"
            r"\[/?INST\]|\[SYSTEM\]|</s>system>",
            re.IGNORECASE,
        ),
        0.10,
    ),
    (
        "delimiter_assistant",
        re.compile(
            r"###\s*(?:assistant|ai|model)|assistant:|model:",
            re.IGNORECASE,
        ),
        0.05,
    ),
    # ----- TOOL CALLING -----
    (
        "call_function",
        re.compile(
            r"(?:call|invoke|use|execute|run)\s+(?:the\s+)?"
            r"(?:\w+\s+){0,3}?"
            r"(?:function|tool|api|plugin|skill|method)"
            r"(?:\s*[:\(]?\s*\w+)?",
            re.IGNORECASE,
        ),
        0.10,
    ),
    (
        "send_email",
        re.compile(
            r"\bsend[_\s]?(?:email|mail|message)\b|"
            r"\bsmtp\.\w+|"
            r"\b(?:email|mail)\s+(?:to\s+)?[a-z0-9._%+-]+@",
            re.IGNORECASE,
        ),
        0.20,
    ),
)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class InjectionFinding:
    label: str
    weight: float
    match: str
    start: int
    end: int


@dataclass(slots=True)
class InjectionScanResult:
    findings: list[InjectionFinding] = field(default_factory=list)
    raw_score: float = 0.0  # sum of weights, capped to [0, 1]

    @property
    def is_suspicious(self) -> bool:
        """True when the score crosses our action threshold (0.25)."""
        return self.raw_score >= 0.25


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
def scan_prompt_injection(text: str) -> InjectionScanResult:
    """Scan ``text`` for likely prompt-injection attempts.

    Returns a result whose ``raw_score`` is the sum of pattern weights,
    capped at 1.0. Per 下一阶段 #29, ``is_suspicious`` (≥ 0.25) maps
    to ``RiskLevel.HIGH`` — never auto-publish, surface to admin.
    """
    if not text:
        return InjectionScanResult()

    findings: list[InjectionFinding] = []
    score = 0.0
    for label, pattern, weight in _PATTERN_TABLE:
        for m in pattern.finditer(text):
            findings.append(
                InjectionFinding(
                    label=label,
                    weight=weight,
                    match=m.group(0),
                    start=m.start(),
                    end=m.end(),
                )
            )
            score += weight

    return InjectionScanResult(findings=findings, raw_score=min(1.0, score))


def has_prompt_injection(text: str) -> bool:
    return scan_prompt_injection(text).is_suspicious


__all__ = [
    "InjectionFinding",
    "InjectionScanResult",
    "has_prompt_injection",
    "scan_prompt_injection",
]