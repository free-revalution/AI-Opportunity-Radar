"""v2.0 — Auto-generated sales copy from each Opportunity.

This package turns each opportunity the engine detects into a
self-contained sales asset that can be posted to existing platforms
(Xianyu, Xiaohongshu, WeChat, Feishu, …) without building a SaaS UI
or running a payment system of our own.

Pipeline shape
==============

```
Opportunity  +  ResearchReport  +  LLMProvider
                    │
                    ▼
            ContentGenerator.generate()
                    │
                    ▼
              GeneratedContent
                    │
                    ├──► persisted in the DB (content_status → 'generated')
                    │
                    ▼
            ContentDistributionService
                    │
                    ├──► copy → clipboard (Xianyu / 公众号 — manual)
                    ├──► markdown → Telegram / Feishu bot
                    └──► markdown → static site (post-bundle)
```

Module layout
=============

    base.py              — ContentGenerator ABC, GeneratedContent dataclass,
                          ContentRegistry singleton
    service.py           — orchestrator that fans a list of opportunities
                          through the registry and updates content_status
    daily_report.py      — Markdown daily-digest report (Feishu / email)
    xianyu_product.py    — JSON product listing for Xianyu (二手)
    xiaohongshu_post.py  — Markdown post for Xiaohongshu (小红书)

Everything here is async and goes through the LLM provider boundary so
mock mode (`MOCK_EXTERNAL_SERVICES=true`) makes the whole module
testable offline.
"""

from __future__ import annotations

from app.services.content_generator.base import (
    ContentGenerator,
    ContentRegistry,
    GeneratedContent,
    get_registry,
    register,
)
from app.services.content_generator.service import (
    ContentGeneratorService,
    GenerationResult,
)

__all__ = [
    "ContentGenerator",
    "ContentRegistry",
    "GeneratedContent",
    "ContentGeneratorService",
    "GenerationResult",
    "get_registry",
    "register",
]