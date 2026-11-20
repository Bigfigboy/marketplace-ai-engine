"""The standard agent contract.

Every agent - built-in or added later - is a ``BaseAgent`` subclass that declares its
metadata and implements ``run``. The API, logging, limits, billing and file access are
handled by the engine (``app.services.runner``), so adding an agent never requires API
changes. See docs/INTEGRATION_GUIDE.md -> "Adding a new agent".
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, LLMError
from app.schemas.common import AgentInfo, HistoryMessage, RunRequest, Source
from app.security.auth import Principal
from app.services.files import LoadedFile
from app.services.llm import LLMProvider, LLMRequest, LLMResult, MockFn
from app.services.pricing import estimate_tokens


@dataclass
class AgentResult:
    response: str
    status: str = "success"  # success | escalation_required | blocked
    sources: list[Source] = field(default_factory=list)
    data: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class AgentContext:
    """Everything an agent may use. Agents get no raw request, headers or other tenants' data."""

    run_id: str
    settings: Settings
    db: Session
    principal: Principal
    language: str
    files: list[LoadedFile]
    history: list[HistoryMessage]
    llm: LLMProvider
    deadline: float
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    llm_mode: str = "none"
    model: str | None = None
    warnings: list[str] = field(default_factory=list)

    def remaining_seconds(self) -> float:
        return self.deadline - time.monotonic()

    def fit_text(self, text: str, max_tokens: int) -> tuple[str, bool]:
        """Trim ``text`` to roughly ``max_tokens`` tokens. Returns (text, was_trimmed)."""
        max_chars = max_tokens * 3
        if len(text) <= max_chars:
            return text, False
        return text[:max_chars] + "\n[... truncated to fit the input token limit ...]", True

    def call_llm(self, messages: list[dict[str, str]], *, mock_fn: MockFn, json_mode: bool = False,
                 max_output_tokens: int | None = None, temperature: float | None = None) -> LLMResult:
        prompt_tokens = sum(estimate_tokens(m["content"]) for m in messages)
        if prompt_tokens > self.settings.max_input_tokens:
            raise AppError("The request is too large for the configured input token limit.",
                           code="input_token_limit_exceeded", status_code=413)
        result = self.llm.complete(
            LLMRequest(messages=messages,
                       max_output_tokens=min(max_output_tokens or self.settings.max_output_tokens,
                                             self.settings.max_output_tokens),
                       json_mode=json_mode, temperature=temperature),
            deadline=self.deadline, mock_fn=mock_fn)
        self.llm_calls += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.llm_mode = result.mode
        self.model = result.model
        self.warnings.extend(result.warnings)
        if not result.text.strip():
            # E.g. a reasoning model that spent the whole MAX_OUTPUT_TOKENS budget on reasoning.
            raise LLMError("The AI provider returned an empty response. Retry, or raise MAX_OUTPUT_TOKENS.",
                           code="llm_empty_output", status_code=502)
        return result

    def history_messages(self, max_chars: int = 6000) -> list[dict[str, str]]:
        """Previous turns supplied by the client, newest kept first within a budget."""
        out: list[dict[str, str]] = []
        used = 0
        for item in reversed(self.history):
            if used + len(item.content) > max_chars:
                break
            out.append({"role": item.role, "content": item.content})
            used += len(item.content)
        return list(reversed(out))


class BaseAgent(ABC):
    """Interface every marketplace agent implements."""

    id: ClassVar[str]
    slug: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    version: ClassVar[str]
    category: ClassVar[str]
    supported_languages: ClassVar[tuple[str, ...]] = ("ru", "kk", "en")
    capabilities: ClassVar[tuple[str, ...]] = ()
    accepted_file_purposes: ClassVar[tuple[str, ...]] = ()
    max_files: ClassVar[int] = 5
    options_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel]]

    def info(self) -> AgentInfo:
        input_schema = RunRequest.model_json_schema()
        input_schema["properties"]["options"] = self.options_model.model_json_schema()
        input_schema["title"] = f"{self.slug}_run_request"
        return AgentInfo(
            id=self.id, slug=self.slug, name=self.name, description=self.description,
            version=self.version, category=self.category,
            supported_languages=list(self.supported_languages),  # type: ignore[arg-type]
            capabilities=list(self.capabilities), input_schema=input_schema,
            output_schema=self.output_model.model_json_schema(),
            accepted_file_purposes=list(self.accepted_file_purposes),
        )

    def parse_options(self, options: dict[str, Any]) -> BaseModel:
        try:
            return self.options_model.model_validate(options)
        except ValidationError as exc:
            issues = [{"loc": ["options", *map(str, e["loc"])], "msg": e["msg"], "type": e["type"]}
                      for e in exc.errors()]
            raise AppError("Invalid agent options", code="validation_error", status_code=422,
                           details={"issues": issues}) from None

    def precheck(self, message: str, options: BaseModel, ctx: AgentContext) -> AgentResult | None:
        """Optional policy check before any LLM call. Return a result to short-circuit."""
        return None

    @abstractmethod
    def run(self, message: str, options: BaseModel, ctx: AgentContext) -> AgentResult:
        """Execute the agent. Must not perform side effects outside the engine."""


__all__ = ["AgentContext", "AgentResult", "BaseAgent", "LLMError"]
