"""Per-source compliance defaults — Phase 29.

Each registered source slug ships with a known-good compliance posture
(``compliance_level`` + ``access_method`` + ``commercial_use_status``
+ ``terms_url`` + ``robots_url`` + ``rate_limit``). The
``IngestionService._persist()`` flow consults this table when upserting
a new ``sources`` row so first-time connectors don't default to the
most-conservative ``E`` (block) posture.

This is **not** a compliance audit — it's a baseline. Operators can
override per-row in the admin console. The matrix used to live inline
inside ``service.py``; it was extracted here so the same defaults can
be (a) unit-tested in isolation, (b) referenced by the operator docs,
and (c) re-applied via SQL for existing rows that pre-date the fix.

Conventions:
  * ``A`` — official API / explicit allow (GitHub, HN, arxiv).
  * ``B`` — public, but with ToS caveats that may require auth (Reddit,
            YouTube Data API v3 needs a key for >quotas).
  * ``C`` — public page scraping with unclear commercial-use language;
            flagged for human review.
  * ``D`` — known ToS-forbidden (we never reach here today).
  * ``E`` — unknown / undeclared (the default for any slug NOT in the
            table — kept conservative per ``evaluate_source_policy``).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.compliance.models import ComplianceLevel


@dataclass(slots=True, frozen=True)
class SourceComplianceDefault:
    compliance_level: str
    access_method: str  # official_api | public_page | rss | search_api | crawler | unknown
    commercial_use_status: str  # allowed | conditional | forbidden | unknown
    terms_url: str | None
    robots_url: str | None = None
    rate_limit: int | None = None  # requests / minute


# Phase 29 — hand-curated baseline. The values below reflect a quick
# review of each source's public-facing ToS as of 2026-08-31.
SOURCE_COMPLIANCE_DEFAULTS: dict[str, SourceComplianceDefault] = {
    "github": SourceComplianceDefault(
        compliance_level=ComplianceLevel.A.value,
        access_method="official_api",
        commercial_use_status="allowed",
        terms_url="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
        robots_url="https://github.com/robots.txt",
        rate_limit=60,  # unauthenticated GitHub Search API: 10/min, with token 30/min
    ),
    "reddit": SourceComplianceDefault(
        compliance_level=ComplianceLevel.B.value,
        access_method="public_page",
        commercial_use_status="conditional",
        terms_url="https://www.redditinc.com/policies/user-agreement",
        robots_url="https://www.reddit.com/robots.txt",
        rate_limit=60,
    ),
    "hackernews": SourceComplianceDefault(
        compliance_level=ComplianceLevel.A.value,
        access_method="official_api",
        commercial_use_status="allowed",
        terms_url="https://www.ycombinator.com/legal/",
        rate_limit=None,
    ),
    "producthunt": SourceComplianceDefault(
        compliance_level=ComplianceLevel.A.value,
        access_method="official_api",
        commercial_use_status="allowed",
        terms_url="https://www.producthunt.com/terms",
        rate_limit=None,
    ),
    "rss": SourceComplianceDefault(
        compliance_level=ComplianceLevel.A.value,
        access_method="rss",
        commercial_use_status="allowed",
        terms_url=None,  # per-feed — see DEFAULT_FEEDS metadata
        rate_limit=None,
    ),
    "youtube": SourceComplianceDefault(
        compliance_level=ComplianceLevel.B.value,
        access_method="official_api",
        commercial_use_status="conditional",
        terms_url="https://www.youtube.com/static?template=terms",
        rate_limit=100,  # Data API v3 default daily quota
    ),
    "arxiv": SourceComplianceDefault(
        compliance_level=ComplianceLevel.A.value,
        access_method="rss",
        commercial_use_status="allowed",
        terms_url="https://arxiv.org/help/license",
        rate_limit=None,
    ),
    "huggingface": SourceComplianceDefault(
        compliance_level=ComplianceLevel.A.value,
        access_method="official_api",
        commercial_use_status="allowed",
        terms_url="https://huggingface.co/terms-of-service",
        rate_limit=None,
    ),
    "douyin": SourceComplianceDefault(
        # ToS forbids scraping without explicit partnership; left at E
        # until a user manually configures a proxy + access_method.
        compliance_level=ComplianceLevel.E.value,
        access_method="crawler",
        commercial_use_status="forbidden",
        terms_url="https://www.douyin.com/agreement",
        rate_limit=None,
    ),
    "weibo": SourceComplianceDefault(
        compliance_level=ComplianceLevel.E.value,
        access_method="crawler",
        commercial_use_status="forbidden",
        terms_url="https://weibo.com/signup/v5/privacy",
        rate_limit=None,
    ),
    "zhihu": SourceComplianceDefault(
        compliance_level=ComplianceLevel.E.value,
        access_method="crawler",
        commercial_use_status="forbidden",
        terms_url="https://www.zhihu.com/term",
        rate_limit=None,
    ),
    "amazon_best": SourceComplianceDefault(
        # Public listing pages, but Amazon ToS is strict on
        # automated access. Flagged for human review.
        compliance_level=ComplianceLevel.C.value,
        access_method="public_page",
        commercial_use_status="conditional",
        terms_url="https://www.amazon.com/gp/help/customer/display.html",
        rate_limit=None,
    ),
    "wallstreetcn_hot": SourceComplianceDefault(
        compliance_level=ComplianceLevel.C.value,
        access_method="crawler",
        commercial_use_status="conditional",
        terms_url="https://www.wallstreetcn.com/terms",
        rate_limit=None,
    ),
}


def get_default(slug: str) -> SourceComplianceDefault | None:
    """Return the curated compliance default for ``slug``, or ``None``
    when the slug is unknown (caller should fall back to ``E``)."""
    return SOURCE_COMPLIANCE_DEFAULTS.get(slug)


__all__ = [
    "SOURCE_COMPLIANCE_DEFAULTS",
    "SourceComplianceDefault",
    "get_default",
]