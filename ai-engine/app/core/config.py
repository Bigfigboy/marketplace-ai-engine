"""Application settings.

Every tunable value (limits, prices, model, timeouts) comes from environment variables
or a ``.env`` file. Nothing here is a secret except ``OPENAI_API_KEY`` and ``JWT_SECRET``,
which have no default and must never be committed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # .../ai-engine


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "AI Agent Marketplace Engine"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    default_language: Literal["ru", "kk", "en"] = "ru"
    enable_demo_ui: bool = True
    enable_docs: bool = True

    # ── LLM ──────────────────────────────────────────────────────────────────
    # openai: real OpenAI API (requires OPENAI_API_KEY).
    # mock:   explicitly labelled offline mode, no external calls, for architecture tests.
    llm_mode: Literal["openai", "mock"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str | None = None
    # Optional. Some reasoning models reject a non-default temperature, so it is only
    # sent when explicitly configured.
    openai_temperature: float | None = Field(default=None, ge=0, le=2)

    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_base_delay_seconds: float = Field(default=0.5, ge=0, le=30)
    request_timeout_seconds: float = Field(default=120.0, gt=0, le=900)

    max_input_tokens: int = Field(default=12_000, ge=500, le=500_000)
    max_output_tokens: int = Field(default=1_500, ge=50, le=64_000)
    max_message_chars: int = Field(default=8_000, ge=100, le=100_000)

    # ── Cost control (prices are configuration, not code) ────────────────────
    price_input_per_1m_tokens: float | None = Field(default=None, ge=0)
    price_output_per_1m_tokens: float | None = Field(default=None, ge=0)
    price_currency: str = "USD"
    daily_spend_limit: float | None = Field(default=5.0, ge=0)
    company_daily_spend_limit: float | None = Field(default=1.0, ge=0)

    # ── Demo limits ──────────────────────────────────────────────────────────
    demo_mode: bool = True
    daily_run_limit_per_user: int = Field(default=10, ge=1)
    quota_timezone: str = "Asia/Almaty"

    # ── Rate limiting ────────────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=30, ge=1)
    auth_failures_per_minute: int = Field(default=20, ge=1)

    # ── Files ────────────────────────────────────────────────────────────────
    max_file_size_mb: float = Field(default=10.0, gt=0, le=100)
    max_pdf_pages: int = Field(default=200, ge=1)
    max_extracted_chars: int = Field(default=500_000, ge=1_000)
    storage_dir: Path = BASE_DIR / "data" / "uploads"

    # ── RAG ──────────────────────────────────────────────────────────────────
    rag_chunk_chars: int = Field(default=900, ge=200, le=5_000)
    rag_chunk_overlap: int = Field(default=150, ge=0, le=1_000)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_min_score: float = Field(default=0.5, ge=0)

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'ai_engine.db').as_posix()}"
    auto_migrate: bool = True

    # ── Security ─────────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    jwt_secret: SecretStr | None = None
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_audience: str | None = None
    jwt_issuer: str | None = None
    # Optional key created on startup so a local demo can be used immediately.
    demo_api_key: SecretStr | None = None
    demo_company_name: str = "Demo Company (fictional)"

    # ── Agents ───────────────────────────────────────────────────────────────
    # Comma-separated list of trusted, in-repository agent modules to load.
    agent_modules: str = "app.agents.marketing,app.agents.support,app.agents.hr"

    @field_validator("openai_api_key", "jwt_secret", "demo_api_key", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "openai_temperature",
        "price_input_per_1m_tokens",
        "price_output_per_1m_tokens",
        "daily_spend_limit",
        "company_daily_spend_limit",
        "openai_base_url",
        "jwt_audience",
        "jwt_issuer",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        # A wildcard together with credentials is unsafe; it is never allowed.
        return [o for o in origins if o != "*"]

    @property
    def agent_module_list(self) -> list[str]:
        return [m.strip() for m in self.agent_modules.split(",") if m.strip()]

    @property
    def openai_configured(self) -> bool:
        return self.openai_api_key is not None and bool(self.openai_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
