"""Pydantic schemas shared by the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Language = Literal["ru", "kk", "en"]
RunStatus = Literal["success", "escalation_required", "blocked", "error"]
_ID_PATTERN = r"^[A-Za-z0-9-]{1,64}$"


class ErrorDetail(BaseModel):
    code: str = Field(examples=["file_not_found"])
    message: str
    request_id: str | None = None
    run_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class RunRequest(BaseModel):
    """Body of ``POST /agents/{slug}/run``. User and company come from authentication."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"message": "Создай контент-план для кофейни в Алматы", "language": "ru", "file_ids": []},
    ]})

    message: str = Field(min_length=1, max_length=20_000, description="User request to the agent")
    language: Language | Literal["auto"] | None = Field(
        default=None, description="ru | kk | en. Omit or 'auto' to detect from the message.")
    file_ids: list[str] = Field(default_factory=list, max_length=10,
                                description="Ids of files uploaded via POST /files/upload")
    options: dict[str, Any] = Field(default_factory=dict,
                                    description="Agent-specific options, see the agent's input_schema")
    history: list[HistoryMessage] = Field(default_factory=list, max_length=10,
                                          description="Optional previous turns of this chat (untrusted)")

    @field_validator("message")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @field_validator("file_ids")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        import re

        for item in value:
            if not re.match(_ID_PATTERN, item):
                raise ValueError("invalid file id")
        return value


class Source(BaseModel):
    file_id: str
    filename: str
    chunk_index: int | None = None
    score: float | None = None
    excerpt: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float | None = None
    currency: str = "USD"


class RunResponse(BaseModel):
    """Unified response of every agent."""

    run_id: str
    agent: str
    agent_version: str
    status: RunStatus
    response: str
    language: Language
    sources: list[Source] = Field(default_factory=list)
    usage: Usage
    execution_time_ms: int
    mode: Literal["openai", "groq", "gemini", "openrouter", "ollama", "mock", "none"] = Field(
        description="LLM provider that produced the answer: openai | groq | gemini | openrouter | ollama "
                    "(real model calls); mock = offline mock mode, not AI; none = no LLM call was needed")
    model: str | None = None
    data: dict[str, Any] | None = Field(default=None, description="Agent-specific structured output")
    warnings: list[str] = Field(default_factory=list)


class AgentInfo(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    version: str
    category: str
    supported_languages: list[Language]
    capabilities: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    accepted_file_purposes: list[str] = Field(default_factory=list)


class AgentList(BaseModel):
    agents: list[AgentInfo]


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    purpose: str
    file_format: str
    size_bytes: int
    char_count: int
    page_count: int | None
    chunk_count: int
    created_at: datetime


class FileUploadResponse(FileInfo):
    warnings: list[str] = Field(default_factory=list)


class FileList(BaseModel):
    files: list[FileInfo]


class RunRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_slug: str
    agent_version: str
    status: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float | None
    execution_time_ms: int
    error_code: str | None
    language: str | None
    llm_mode: str | None
    model: str | None
    file_count: int
    source_count: int
    created_at: datetime


class RunList(BaseModel):
    runs: list[RunRecord]
    total: int
    limit: int
    offset: int


class AgentUsageStats(BaseModel):
    agent_slug: str
    runs: int
    success: int
    escalation_required: int
    blocked: int
    errors: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float | None
    avg_execution_time_ms: int


class UsageStats(BaseModel):
    scope: Literal["user", "company"]
    period_days: int
    agents: list[AgentUsageStats]
    today_runs_by_you: int
    daily_run_limit: int | None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    llm_mode: str = Field(description="Active LLM provider (kept for compatibility; same as llm_provider)")
    llm_provider: str
    llm_billing: Literal["paid", "free", "local", "none"]
    llm_configured: bool
    llm_problem: str | None = Field(default=None, description="Why the provider cannot be used, if it cannot")
    model: str
    database: Literal["ok", "error"]
    agents: list[str]
