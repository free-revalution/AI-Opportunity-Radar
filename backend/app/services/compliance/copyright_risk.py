"""Copyright-risk detector — guard against verbatim large-block copying.

Per 下一阶段 #21 + #106:

> 不得将完整文章 / 完整新闻 / 完整帖子 / 完整视频复制到付费知识库。
> 输出以「短摘要 / 事实 / 分析 / 观点 / 原始来源链接」为主。
> 输入完整新闻文章时输出不能大段复制原文。

This module detects when the AI output contains a contiguous block that
looks like a verbatim copy of source content. We can't run a real
plagiarism check here (no vector store, no source DB), but we can run a
cheap heuristic:

  * Compare the *candidate output* against the *source content* (when
    provided) at the paragraph / sentence level.
  * If a contiguous run of N+ consecutive sentences / M+ characters
    matches the source > 85 %, flag it.

The detector accepts two inputs:
  * ``source``: the page / article / Reddit post we ingested.
  * ``output``: what the LLM produced.

If ``source`` is empty, we only do a soft "too long, no citations"
check (long marketing-grade content without source links is itself a
copyright red flag).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Thresholds (per 下一阶段 #21 — must be calibrated together with prompt)
# ---------------------------------------------------------------------------
MIN_COPY_RUN_SENTENCES: int = 3  # ≥ 3 consecutive matching sentences = block
MIN_COPY_RUN_CHARS: int = 180    # ≥ 180 matching chars = block
MIN_OUTPUT_LEN_FOR_FLAG: int = 600  # short outputs rarely trigger
COPY_MATCH_THRESHOLD: float = 0.85  # Jaccard threshold for "matching"

# Citation URL pattern — used for the "long output, no citation" check.
_URL_RE = re.compile(r"https?://[^\s\]\)\,，。；;\"'》>]+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class CopyBlock:
    """A contiguous run of matching sentences between source and output."""

    length_chars: int
    length_sentences: int
    excerpt: str  # first 100 chars for audit display


@dataclass(slots=True)
class CopyrightScanResult:
    copy_blocks: list[CopyBlock] = field(default_factory=list)
    output_length: int = 0
    has_citations: bool = False
    # Aggregate risk score 0..1 — sum of block weights, capped.
    raw_score: float = 0.0

    @property
    def is_high_risk(self) -> bool:
        return self.raw_score >= 0.5 or len(self.copy_blocks) >= 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _split_sentences(text: str) -> list[str]:
    """Cheap sentence splitter — works for both Chinese and English.

    Split on ASCII '.!?' and Chinese '。！？' followed by whitespace OR
    by another non-space character (Chinese text rarely inserts spaces).
    We don't need linguistic precision; we need stability so the same
    source+output pair gives the same result every call.
    """
    if not text:
        return []
    # Normalize Chinese punctuation to ASCII so we can split uniformly.
    normalized = text.translate(str.maketrans({"。": ".", "！": "!", "？": "?"}))
    # Two pass splits:
    #   1. space-delimited (English style)
    #   2. tight-delimited (Chinese style — no space after period)
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    out: list[str] = []
    for p in parts:
        # If a piece contains no delimiters at all, leave alone.
        if not p:
            continue
        sub = re.split(r"(?<=[.!?])(?=[^.!?\s])", p)
        out.extend(s.strip() for s in sub if s.strip())
    return out


def _jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity on lowercase whitespace tokens."""
    sa = set(re.findall(r"[\w一-鿿]+", a.lower()))
    sb = set(re.findall(r"[\w一-鿿]+", b.lower()))
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union)


def _has_citations(text: str) -> bool:
    return bool(_URL_RE.search(text or ""))


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
def scan_copyright(
    output: str,
    source: str | None = None,
) -> CopyrightScanResult:
    """Detect likely verbatim copying in ``output``.

    Behaviour:
      * If ``source`` is given and non-empty, scan sentence-by-sentence
        and flag contiguous matching runs.
      * If ``source`` is empty/None, fall back to a length+citation check
        (long output without any URLs is itself a copyright red flag).

    Never raises — bad input just yields an empty result.
    """
    output = output or ""
    source = source or ""
    result = CopyrightScanResult(output_length=len(output))
    result.has_citations = _has_citations(output)

    if not output:
        return result

    if source:
        src_sentences = _split_sentences(source)
        out_sentences = _split_sentences(output)

        # Build a hash → list of source-sentence-indices so we can match
        # quickly without an O(N*M) inner loop on every call.
        src_set: dict[str, list[int]] = {}
        for idx, sent in enumerate(src_sentences):
            tokens = tuple(sorted(re.findall(r"[\w一-鿿]+", sent.lower())))
            if not tokens:
                continue
            src_set.setdefault(" ".join(tokens), []).append(idx)

        run: list[tuple[str, int, int]] = []  # (sentence, src_idx, out_idx)
        runs: list[list[tuple[str, int, int]]] = []

        for o_idx, o_sent in enumerate(out_sentences):
            tokens = tuple(sorted(re.findall(r"[\w一-鿿]+", o_sent.lower())))
            if not tokens:
                _flush(run, runs)
                run = []
                continue
            key = " ".join(tokens)
            candidates = src_set.get(key, [])
            best_idx: int | None = None
            best_sim = 0.0
            for s_idx in candidates:
                sim = _jaccard(o_sent, src_sentences[s_idx])
                if sim > best_sim:
                    best_sim = sim
                    best_idx = s_idx
            if best_sim >= COPY_MATCH_THRESHOLD and best_idx is not None:
                run.append((o_sent, best_idx, o_idx))
            else:
                _flush(run, runs)
                run = []

        _flush(run, runs)

        for r in runs:
            excerpt = r[0][0]
            length_chars = sum(len(s[0]) for s in r)
            length_sentences = len(r)
            if (
                length_sentences >= MIN_COPY_RUN_SENTENCES
                or length_chars >= MIN_COPY_RUN_CHARS
            ):
                result.copy_blocks.append(
                    CopyBlock(
                        length_chars=length_chars,
                        length_sentences=length_sentences,
                        excerpt=excerpt[:100],
                    )
                )

        result.raw_score = min(
            1.0,
            sum(0.30 + min(b.length_sentences, 10) * 0.10 for b in result.copy_blocks),
        )
        return result

    # No source — soft check.
    if len(output) >= MIN_OUTPUT_LEN_FOR_FLAG and not result.has_citations:
        # Mild score — we don't BLOCK, but we surface MEDIUM risk + flag
        # for review.
        result.raw_score = 0.35
    return result


def _flush(run: list[tuple[str, int, int]], runs: list[list[tuple[str, int, int]]]) -> None:
    if run:
        runs.append(run)


__all__ = [
    "CopyBlock",
    "CopyrightScanResult",
    "scan_copyright",
]