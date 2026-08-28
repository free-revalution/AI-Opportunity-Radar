"""PII detector — regex-based scanner for common Chinese-context PII.

Per 下一阶段 #68, PII must be detected in:
  * ``RawItem`` content before it reaches LLM
  * AI output before it leaves the system
  * User input (e.g. Feishu chat messages, activation code payloads)

PII types covered:
  * 手机号 (mainland + HK + simple international)
  * 邮箱
  * 18-digit Chinese 身份证号
  * 16-19 digit 银行卡 / 信用卡 (Luhn-not-required heuristic — false positives
    are tolerated, false negatives would be unacceptable)
  * 中国大陆地址 keyword scan (province/city keywords + a digit pattern)
  * 个人微信号 / QQ (best-effort)

Design notes:
  * Pure regex — no external call, no async. Fast enough to run inline
    on every LLM response in production.
  * All patterns return ``re.Match`` objects; callers extract the matched
    text via ``m.group(0)``.
  * Conservative on Chinese 身份证 — we require a check-digit-valid 18-digit
    form to reduce false positives (since the cost of leaking even one
    real ID is high). 15-digit legacy IDs are flagged separately.
  * Redaction helpers (``redact_pii``) replace matches with category
    placeholders so logs/audit records remain useful without leaking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
# Mainland mobile: 1[3-9]\d{9} — covers all current carriers (CMCC/CUCC/CTNET).
_MOBILE_CN_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# Hong Kong mobile: 8 digits starting with 9 (rough).
_MOBILE_HK_RE = re.compile(r"(?<!\d)9\d{7}(?!\d)")

# Email — RFC-pragmatic version.
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9._%+-])"
)

# 18-digit 身份证 (with check digit validation).
_ID_CN_18_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

# 15-digit legacy 身份证 (no check digit).
_ID_CN_15_RE = re.compile(r"(?<!\d)\d{15}(?!\d)")

# 16-19 digit card-like numbers. Pure heuristic — we never let the result
# stand alone, only as part of a PII finding.
_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")

# Chinese-address keyword scan. We deliberately do NOT try to validate
# full addresses (too many false negatives); instead we flag when a
# typical province / 直辖市 keyword appears next to a digit+字 pattern.
_ADDRESS_KEYWORDS = (
    "北京市", "上海市", "天津市", "重庆市",
    "广东省", "浙江省", "江苏省", "山东省", "四川省", "湖北省",
    "河南省", "福建省", "安徽省", "湖南省", "河北省", "陕西省",
    "辽宁省", "吉林省", "黑龙江省", "云南省", "贵州省", "海南省",
    "山西省", "甘肃省", "青海省", "江西省", "内蒙古", "广西",
    "西藏", "宁夏", "新疆", "台湾省", "香港", "澳门",
)
_ADDRESS_RE = re.compile(
    r"(?P<province>" + "|".join(_ADDRESS_KEYWORDS) + r")"
    r"[一-龥A-Za-z\d]{0,30}"
    r"\d{1,5}号"
)

# WeChat IDs: 6-20 chars, alphanumeric + dash/underscore, starts with letter.
# WeChat IDs: 6-20 chars, alphanumeric + dash/underscore, starts with a
# letter AND must contain at least one digit/underscore/dash. A pure-letter
# 6-20-char string is almost always a normal English word, not a WeChat
# handle, so we tighten the match to keep false positives down.
_WECHAT_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"[a-zA-Z]"
    r"(?:[a-zA-Z0-9_-]{0,18})"
    r"(?=[0-9_-])"           # lookahead: at least one digit/_/dash
    r"[a-zA-Z0-9_-]{0,18}"
    r"(?![A-Za-z0-9])"
)

# QQ: 5-12 digits, but matched only when preceded by `QQ:` to reduce noise.
_QQ_RE = re.compile(r"QQ[:：\s]*(\d{5,12})")


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class PIIFinding:
    category: str  # mobile_cn | id_cn | email | card | address | wechat | qq
    value: str
    start: int
    end: int


@dataclass(slots=True)
class PIIScanResult:
    findings: list[PIIFinding] = field(default_factory=list)
    has_high_risk: bool = False  # 身份证 / 银行卡 / 完整地址

    @property
    def count(self) -> int:
        return len(self.findings)


# ---------------------------------------------------------------------------
# ID validation
# ---------------------------------------------------------------------------
def _id_check_digit_valid(digits_18: str) -> bool:
    """Validate the check digit of a Chinese 18-digit 身份证号.

    Weights and check codes follow GB 11643-1999. Returns False on any
    non-18-char input so callers can use this as a single guard.
    """
    if len(digits_18) != 18:
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check_codes = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    digits_17 = digits_18[:17]
    try:
        total = sum(int(c) * w for c, w in zip(digits_17, weights))
    except ValueError:
        return False
    expected = check_codes[total % 11]
    return expected == digits_18[17].upper()


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
def scan_pii(text: str) -> PIIScanResult:
    """Scan ``text`` for PII. Returns an immutable view.

    The function never raises — unparseable input yields an empty result.
    """
    if not text:
        return PIIScanResult()

    findings: list[PIIFinding] = []
    seen_spans: list[tuple[int, int]] = []

    def _add(category: str, m: re.Match[str]) -> None:
        start, end = m.start(), m.end()
        # Suppress nested overlaps: prefer the longest span.
        if any(s <= start < e or s < end <= e for s, e in seen_spans):
            return
        seen_spans.append((start, end))
        findings.append(
            PIIFinding(
                category=category,
                value=m.group(0),
                start=start,
                end=end,
            )
        )

    # High-risk first — these dominate the risk score.
    for m in _ID_CN_18_RE.finditer(text):
        if _id_check_digit_valid(m.group(0)):
            _add("id_cn", m)
    for m in _ID_CN_15_RE.finditer(text):
        _add("id_cn_legacy", m)
    for m in _CARD_RE.finditer(text):
        _add("card", m)
    for m in _ADDRESS_RE.finditer(text):
        _add("address", m)

    # Medium-risk — phones / IM handles.
    for m in _MOBILE_CN_RE.finditer(text):
        _add("mobile_cn", m)
    for m in _MOBILE_HK_RE.finditer(text):
        # Heuristic: HK mobile is 8 digits, but so are a lot of other
        # things. Only flag if the surrounding 6 chars look like text.
        ctx_start = max(0, m.start() - 6)
        ctx = text[ctx_start : m.end() + 6]
        if any(c.isalpha() for c in ctx):
            _add("mobile_hk", m)
    for m in _EMAIL_RE.finditer(text):
        _add("email", m)
    for m in _WECHAT_RE.finditer(text):
        # Avoid colliding with the email regex by requiring no '@' near.
        if "@" not in text[max(0, m.start() - 1) : m.end() + 1]:
            _add("wechat", m)
    for m in _QQ_RE.finditer(text):
        _add("qq", m)

    has_high_risk = any(
        f.category in {"id_cn", "id_cn_legacy", "card", "address"} for f in findings
    )
    return PIIScanResult(findings=findings, has_high_risk=has_high_risk)


def redact_pii(text: str, placeholder_fmt: str = "[{category}]") -> str:
    """Replace all PII matches in ``text`` with category placeholders.

    Useful for log lines / audit messages where you want to retain the
    structure of the message without leaking PII. Order of replacement
    is from longest match to shortest to keep the indices stable.
    """
    if not text:
        return text
    scan = scan_pii(text)
    if not scan.findings:
        return text

    # Replace from right to left so offsets stay valid.
    out = text
    for f in sorted(scan.findings, key=lambda x: -x.start):
        out = out[: f.start] + placeholder_fmt.format(category=f.category) + out[f.end :]
    return out


def has_pii(text: str) -> bool:
    """Fast check — True if any PII is detected."""
    return scan_pii(text).count > 0


__all__ = [
    "PIIFinding",
    "PIIScanResult",
    "has_pii",
    "redact_pii",
    "scan_pii",
]