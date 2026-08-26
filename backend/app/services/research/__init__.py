"""Research service package — Phase 7 / Phase 11.

Public surface:

    ResearchService           orchestrator (find pending jobs → scrape → LLM → persist)
    ResearchReportSummary     batch outcome
    ResearchReportOutcome     per-job outcome
    parse_research_report     strict LLM-output validator
    build_web_data_provider   picks the configured WebDataProvider chain
    WebDataProvider / SourceDoc
    FirecrawlWebDataProvider / BrowserUseWebDataProvider / FallbackWebDataProvider
    MockWebDataProvider / MockResearchLLMProvider
"""

from app.services.research.browser_use_provider import BrowserUseWebDataProvider
from app.services.research.fallback_provider import FallbackWebDataProvider
from app.services.research.firecrawl_provider import FirecrawlWebDataProvider
from app.services.research.mock_llm import MockResearchLLMProvider
from app.services.research.mock_web_data import MockWebDataProvider
from app.services.research.parsers import parse_research_report
from app.services.research.prompts import (
    RESEARCH_REPORT_SCHEMA,
    SYSTEM_PROMPT,
    build_synthesis_prompt,
)
from app.services.research.service import (
    ResearchReportOutcome,
    ResearchReportSummary,
    ResearchService,
)
from app.services.research.web_data import (
    SourceDoc,
    WebDataProvider,
    build_web_data_provider,
)

__all__ = [
    "BrowserUseWebDataProvider",
    "FallbackWebDataProvider",
    "FirecrawlWebDataProvider",
    "MockResearchLLMProvider",
    "MockWebDataProvider",
    "RESEARCH_REPORT_SCHEMA",
    "ResearchReportOutcome",
    "ResearchReportSummary",
    "ResearchService",
    "SYSTEM_PROMPT",
    "SourceDoc",
    "WebDataProvider",
    "build_synthesis_prompt",
    "build_web_data_provider",
    "parse_research_report",
]
