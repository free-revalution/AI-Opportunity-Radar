"""Application settings loaded from environment variables.

Centralised here so every module imports a single typed Settings object.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level configuration.

    Loaded from `.env` (if present) and the process environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- App ----------
    app_name: str = "AI Opportunity Radar"
    app_env: Literal["local", "dev", "staging", "prod"] = "local"
    app_debug: bool = True
    app_base_url: str = "http://localhost:3000"
    app_api_base_url: str = "http://localhost:8000"
    app_secret_key: str = "change-me-in-production"
    app_log_level: str = "INFO"

    # ---------- Mocking ----------
    mock_external_services: bool = True

    # ---------- Database ----------
    database_url: str = "postgresql+asyncpg://radar:radar@localhost:5432/radar"
    database_url_sync: str = "postgresql://radar:radar@localhost:5432/radar"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ---------- Redis ----------
    redis_url: str = "redis://localhost:6379/0"
    redis_result_backend: str = "redis://localhost:6379/1"

    # ---------- LLM ----------
    openai_api_key: str = ""
    openai_model_cheap: str = "gpt-4o-mini"
    openai_model_mid: str = "gpt-4o"
    openai_model_strong: str = "gpt-4o"

    anthropic_api_key: str = ""
    anthropic_model_cheap: str = "claude-haiku-4-5"
    anthropic_model_mid: str = "claude-sonnet-5"
    anthropic_model_strong: str = "claude-opus-5"

    gemini_api_key: str = ""
    gemini_model_cheap: str = "gemini-1.5-flash"
    gemini_model_mid: str = "gemini-1.5-pro"
    gemini_model_strong: str = "gemini-1.5-pro"

    llm_default_provider: Literal["openai", "anthropic", "gemini"] = "openai"
    llm_embedding_model: str = "text-embedding-3-small"

    # ---------- Firecrawl ----------
    firecrawl_api_key: str = ""
    firecrawl_api_url: str = "https://api.firecrawl.dev"

    # ---------- Browser Use ----------
    browser_use_api_key: str = ""
    browser_use_api_url: str = "https://api.browser-use.com"

    # ---------- Deep Research ----------
    deep_research_max_urls: int = 20
    deep_research_max_depth: int = 3
    deep_research_max_llm_calls: int = 20
    deep_research_max_tokens: int = 50_000

    # ---------- Embeddings ----------
    embedding_cluster_threshold: float = 0.82

    # ---------- Telegram ----------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""

    # ---------- n8n ----------
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str = ""

    # ---------- CORS ----------
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ---------- Rate Limit ----------
    rate_limit_per_minute: int = 120

    # ---------- Sources ----------
    enabled_sources: list[str] = Field(
        default_factory=lambda: ["github", "reddit", "hackernews", "producthunt", "rss"]
    )

    # ---------- Observability (Phase 12) ----------
    prometheus_metrics_enabled: bool = True

    # ---------- Backups (Phase 12) ----------
    backup_container_name: str = "radar-postgres"
    backup_output_dir: str = "./backups"

    # ---------- Validators ----------
    @field_validator("cors_allow_origins", "enabled_sources", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("embedding_cluster_threshold")
    @classmethod
    def _validate_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("embedding_cluster_threshold must be in [0,1]")
        return v

    # ---------- Helpers ----------
    def is_production(self) -> bool:
        return self.app_env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (overridable in tests via `get_settings.cache_clear()`)."""
    return Settings()
