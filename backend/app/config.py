"""Application settings loaded from environment variables.

Centralised here so every module imports a single typed Settings object.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    # — URL the *backend* uses to call itself when handling Feishu
    # command callbacks. Must point at the docker service name so the
    # loopback request doesn't accidentally hit the ngrok tunnel
    # (where `localhost` would be the ngrok agent's listener, not
    # this FastAPI process).
    feishu_internal_api_url: str = "http://backend:8000"
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
    # Primary provider is MiniMax(MiniMax), Anthropic-compatible chat endpoint at
    # https://api.minimaxi.com/anthropic. Reuses the official `anthropic` SDK
    # with a custom base_url — no fork. Available models (from /v1/models):
    #   MiniMax-M3                       (strongest, 2026-06)
    #   MiniMax-M2.7       / -highspeed  (mid)
    #   MiniMax-M2.5       / -highspeed
    #   MiniMax-M2.1       / -highspeed
    #   MiniMax-M2                        (older)
    # There is no "M1" — we use M2.7-highspeed as the cheap tier.
    # OpenAI / Anthropic / Gemini are kept as fallbacks; the runtime picks
    # one via `build_llm_provider`.
    MiniMax_api_key: str = ""
    MiniMax_model_cheap: str = "MiniMax-M2.7-highspeed"
    MiniMax_model_mid: str = "MiniMax-M2.7"
    MiniMax_model_strong: str = "MiniMax-M3"
    MiniMax_api_url: str = "https://api.minimaxi.com/anthropic"

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

    llm_default_provider: Literal["MiniMax", "openai", "anthropic", "gemini"] = "MiniMax"
    llm_embedding_model: str = "MiniMax-Embeddings"

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

    # ---------- Feishu (Phase 2 v2.0) ----------
    # Full Webhook URL for a custom robot — e.g.
    #   https://open.feishu.cn/open-apis/bot/v2/hook/<token>
    # The token is already embedded in the URL; we do not strip it.
    feishu_webhook_url: str = ""
    # Optional signing secret for "加签" custom robots. Leave empty for
    # non-signed robots. When set we send a `timestamp` header and add
    # `timestamp` + `sign` to the request body via HMAC-SHA256.
    feishu_webhook_secret: str = ""
    feishu_timeout: float = 15.0

    # ---------- Feishu App (Phase 6 v2.0 — inbound event subscription) ----------
    # These come from a 飞书开放平台 机器人 App — see 使用手册 §五之七.
    # Without them, the inbound `/api/feishu/event` endpoint accepts
    # events unverified (local dev only).
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""

    # ---------- Feishu content ecosystem (Phase 7 v2.0 — Docx + Bitable) ----------
    # Drive — target folder for `/research` and `/report` Docx creation.
    # Find in Feishu drive:  open the folder, the URL ends with the token
    #   https://<tenant>.feishu.cn/drive/folder/<token>
    # Leave empty to silently skip Docx creation (commands still reply
    # to the chat with their existing text content).
    feishu_drive_root_folder_token: str = ""

    # Bitable — daily-digest table for `/daily`. When empty, the first
    # invocation auto-creates the app and logs the new `app_token` so
    # operators can persist it back here for cross-restart reuse.
    feishu_bitable_app_token: str = ""

    # Bitable — opportunities table for `/table` (separate from digest).
    # Same auto-create behaviour as the digest table.
    feishu_bitable_opportunities_app_token: str = ""

    # ---------- Bot channel selection (Phase 6 v2.0) ----------
    # Primary channel used by `NotificationService` + n8n daily digests.
    # Phase 6 product decision: Feishu first (国内可达 + 知识库 + 权限);
    # Telegram becomes the fallback channel.
    notification_default_channel: Literal["telegram", "feishu"] = "feishu"
    # Ordered list of fallback channels tried when the primary send fails.
    # `NoDecode` keeps the env var as a raw CSV string until our
    # `_split_csv` validator runs — matches the treatment of
    # `cors_allow_origins` + `enabled_sources`. Without this annotation
    # pydantic-settings tries to JSON-decode the value first and fails
    # on the plain `"telegram"` value when the docker image is built
    # with a slightly different pydantic-settings version.
    notification_fallback_channels: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["telegram"]
    )

    # ---------- n8n ----------
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str = ""

    # ---------- CORS ----------
    # `NoDecode` prevents pydantic-settings from JSON-parsing the env var
    # before our `mode="before"` validator runs. Without it the raw CSV
    # string fails with "error parsing value for field ..." on Settings().
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ---------- Rate Limit ----------
    rate_limit_per_minute: int = 120

    # ---------- Sources ----------
    # Same NoDecode treatment — comma-separated list, not JSON.
    enabled_sources: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["github", "reddit", "hackernews", "producthunt", "rss"]
    )

    # ---------- Observability (Phase 12) ----------
    prometheus_metrics_enabled: bool = True

    # ---------- Backups (Phase 12) ----------
    backup_container_name: str = "radar-postgres"
    backup_output_dir: str = "./backups"

    # ---------- Validators ----------
    @field_validator(
        "cors_allow_origins",
        "enabled_sources",
        "notification_fallback_channels",
        mode="before",
    )
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
