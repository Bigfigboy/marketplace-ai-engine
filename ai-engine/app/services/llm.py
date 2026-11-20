"""LLM access layer.

* ``OpenAIProvider`` - the OpenAI API via the official SDK (Chat Completions). Timeouts,
  a bounded retry with exponential backoff for transient errors, and actual token usage.
* ``MockProvider`` - explicitly labelled offline mode for architecture tests. It never
  pretends to be AI: every result carries ``mode="mock"`` and agents prepend a banner.
* ``UnconfiguredProvider`` - used when LLM_MODE=openai but OPENAI_API_KEY is missing;
  every call fails with ``llm_not_configured`` (HTTP 503).

The OpenAI Agents SDK was evaluated (openai-cs-agents-demo uses it); our agents are
single-call, tool-less and need precise control over retries, deadlines and token
accounting, so the plain SDK is simpler. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import Settings
from app.core.errors import LLMError
from app.services.pricing import estimate_tokens

logger = logging.getLogger("app.llm")


@dataclass
class LLMRequest:
    messages: list[dict[str, str]]
    max_output_tokens: int
    json_mode: bool = False
    temperature: float | None = None


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    mode: str  # "openai" | "mock"
    attempts: int = 1
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


MockFn = Callable[[], str]


class LLMProvider(Protocol):
    mode: str
    model: str

    def complete(self, request: LLMRequest, *, deadline: float, mock_fn: MockFn | None = None) -> LLMResult: ...


class UnconfiguredProvider:
    mode = "openai"

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, request: LLMRequest, *, deadline: float, mock_fn: MockFn | None = None) -> LLMResult:
        raise LLMError("OpenAI API key is not configured. Set OPENAI_API_KEY or use LLM_MODE=mock.",
                       code="llm_not_configured", status_code=503)


class MockProvider:
    """Offline provider. Output comes from the agent's deterministic ``mock_fn``."""

    mode = "mock"
    model = "mock"

    def complete(self, request: LLMRequest, *, deadline: float, mock_fn: MockFn | None = None) -> LLMResult:
        text = mock_fn() if mock_fn else "MOCK"
        prompt_chars = sum(len(m.get("content", "")) for m in request.messages)
        return LLMResult(text=text, input_tokens=estimate_tokens("x" * prompt_chars),
                         output_tokens=estimate_tokens(text), model=self.model, mode=self.mode)


class OpenAIProvider:
    mode = "openai"

    def __init__(self, settings: Settings, client: Any | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        import openai

        self._openai = openai
        self.settings = settings
        self.model = settings.openai_model
        self._sleep = sleep
        if client is None:
            assert settings.openai_api_key is not None
            client = openai.OpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                base_url=settings.openai_base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,  # retries are handled here, with a deadline
            )
        self.client = client

    # Map SDK exceptions to (code, http_status, retryable).
    def _classify(self, exc: Exception) -> LLMError:
        o = self._openai
        if isinstance(exc, o.APITimeoutError):
            return LLMError("The AI provider timed out", code="llm_timeout", status_code=504, retryable=True)
        if isinstance(exc, o.APIConnectionError):
            return LLMError("The AI provider is unreachable", code="llm_unavailable", status_code=502, retryable=True)
        if isinstance(exc, o.RateLimitError):
            body = getattr(exc, "body", None)
            code = body.get("code") if isinstance(body, dict) else None
            if code == "insufficient_quota":
                return LLMError("The AI provider quota is exhausted", code="llm_quota_exceeded", status_code=502)
            return LLMError("The AI provider is rate limiting requests", code="llm_rate_limited",
                            status_code=503, retryable=True)
        if isinstance(exc, (o.AuthenticationError, o.PermissionDeniedError)):
            return LLMError("The AI provider rejected the API key", code="llm_auth_failed", status_code=502)
        if isinstance(exc, o.NotFoundError):
            return LLMError("The configured model was not found (check OPENAI_MODEL)",
                            code="llm_model_not_found", status_code=502)
        if isinstance(exc, o.BadRequestError):
            return LLMError("The AI provider rejected the request", code="llm_bad_request", status_code=502)
        if isinstance(exc, o.InternalServerError):
            return LLMError("The AI provider returned a server error", code="llm_unavailable",
                            status_code=502, retryable=True)
        if isinstance(exc, o.APIStatusError):
            retryable = getattr(exc, "status_code", 0) >= 500
            return LLMError("The AI provider returned an error", code="llm_error", status_code=502,
                            retryable=retryable)
        return LLMError("Unexpected AI provider error", code="llm_error", status_code=502)

    def complete(self, request: LLMRequest, *, deadline: float, mock_fn: MockFn | None = None) -> LLMResult:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": request.messages,
            "max_completion_tokens": request.max_output_tokens,
        }
        temperature = self.settings.openai_temperature
        if temperature is not None:
            params["temperature"] = temperature
        if request.json_mode:
            params["response_format"] = {"type": "json_object"}

        max_attempts = 1 + self.settings.llm_max_retries
        last_error: LLMError | None = None
        for attempt in range(1, max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                raise last_error or LLMError("Request deadline exceeded", code="llm_timeout", status_code=504)
            try:
                response = self.client.chat.completions.create(
                    **params, timeout=min(self.settings.llm_timeout_seconds, remaining))
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = self._classify(exc)
                logger.warning("llm_call_failed", extra={"error_code": last_error.code, "attempt": attempt,
                                                         "retryable": last_error.retryable})
                if not last_error.retryable or attempt == max_attempts:
                    raise last_error from None
                delay = self.settings.llm_retry_base_delay_seconds * (2 ** (attempt - 1))
                delay += random.uniform(0, delay / 4) if delay else 0  # noqa: S311 - jitter, not crypto
                if time.monotonic() + delay >= deadline - 1.0:
                    raise last_error from None
                self._sleep(delay)
                continue
            return self._to_result(response, attempt)
        raise last_error or LLMError("AI provider call failed")  # pragma: no cover

    def _to_result(self, response: Any, attempts: int) -> LLMResult:
        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        text = (getattr(message, "content", None) or getattr(message, "refusal", None) or "") if message else ""
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        truncated = bool(choice and choice.finish_reason == "length")
        warnings = ["output_truncated_by_token_limit"] if truncated else []
        if usage is None:
            warnings.append("provider_returned_no_usage")
        return LLMResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens,
                         model=getattr(response, "model", None) or self.model, mode=self.mode,
                         attempts=attempts, truncated=truncated, warnings=warnings)


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_mode == "mock":
        logger.warning("llm_mock_mode_enabled")
        return MockProvider()
    if not settings.openai_configured:
        logger.warning("llm_not_configured")
        return UnconfiguredProvider(settings.openai_model)
    return OpenAIProvider(settings)
