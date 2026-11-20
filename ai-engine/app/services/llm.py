"""LLM access layer: one interface (``LLMProvider``), several backends.

* ``OpenAICompatibleProvider`` - OpenAI, Groq, Google Gemini (OpenAI-compatible endpoint),
  OpenRouter (free models only) and Ollama (local), via the official ``openai`` SDK.
  Timeouts, bounded retries, actual token usage, free-tier limit handling (engine-side
  request guard, ``Retry-After``, daily-quota detection) and JSON-mode compatibility
  (native ``response_format`` with a prompt-only fallback, ``<think>`` stripping).
  Provider differences live in ``app.services.llm_providers``.
* ``MockProvider`` - explicitly labelled offline mode for architecture tests. It never
  pretends to be AI: every result carries ``mode="mock"`` and agents prepend a banner.
* ``UnconfiguredProvider`` - used when the selected provider is missing a key or its model
  is not allowed; every call fails with a clear 503 and no request is sent.

Agents never see which backend is used: they call ``AgentContext.call_llm``. There is no
automatic fallback to another provider or model, so a free setup can never silently
turn into a paid one.

The OpenAI Agents SDK was evaluated (openai-cs-agents-demo uses it); our agents are
single-call, tool-less and need precise control over retries, deadlines and token
accounting, so the plain SDK is simpler. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.core.config import Settings
from app.core.errors import LLMError
from app.services.llm_providers import ProviderConfig, resolve_provider
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
    """Selected provider cannot be used (missing key, disallowed model). Never sends requests."""

    def __init__(self, model: str, *, mode: str = "openai", code: str = "llm_not_configured",
                 message: str = "OpenAI API key is not configured. Set OPENAI_API_KEY or use LLM_MODE=mock.") -> None:
        self.model = model
        self.mode = mode
        self.code = code
        self.message = message

    def complete(self, request: LLMRequest, *, deadline: float, mock_fn: MockFn | None = None) -> LLMResult:
        raise LLMError(self.message, code=self.code, status_code=503)


class MockProvider:
    """Offline provider. Output comes from the agent's deterministic ``mock_fn``."""

    mode = "mock"
    model = "mock"

    def complete(self, request: LLMRequest, *, deadline: float, mock_fn: MockFn | None = None) -> LLMResult:
        text = mock_fn() if mock_fn else "MOCK"
        prompt_chars = sum(len(m.get("content", "")) for m in request.messages)
        return LLMResult(text=text, input_tokens=estimate_tokens("x" * prompt_chars),
                         output_tokens=estimate_tokens(text), model=self.model, mode=self.mode)


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_DAILY_MARKERS = ("per day", "perday", "per_day", "requests per day", "tokens per day", "(rpd)", "(tpd)",
                  "free-models-per-day", "daily", "quota exceeded for quota metric")
_JSON_UNSUPPORTED_MARKERS = ("response_format", "json_object", "json mode", "json_mode", "structured output")
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")


def strip_reasoning(text: str) -> str:
    """Remove ``<think>...</think>`` blocks emitted by reasoning models (Qwen3, DeepSeek-R1...)."""
    cleaned = _THINK_RE.sub("", text)
    if cleaned.lstrip().lower().startswith("<think>"):  # unterminated: the whole output was reasoning
        return ""
    return cleaned.strip()


def _seconds_until_utc_midnight() -> float:
    now = datetime.now(UTC)
    return ((now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - now).total_seconds()


class ProviderRequestGuard:
    """Engine-side limiter that keeps usage below a free tier's RPM/RPD.

    Counts every HTTP attempt (including retries). In-process only; the provider's own
    429 handling remains the source of truth across restarts and replicas.
    """

    def __init__(self, per_minute: int | None, per_day: int | None) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self._minute: deque[float] = deque()
        self._day = (datetime.now(UTC).date(), 0)
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            today = datetime.now(UTC).date()
            if self._day[0] != today:
                self._day = (today, 0)
            if self.per_day is not None and self._day[1] >= self.per_day:
                raise LLMError("The engine's daily request budget for this free AI provider is used up "
                               "(LLM_REQUESTS_PER_DAY).", code="llm_free_quota_exhausted", status_code=429,
                               retry_after=_seconds_until_utc_midnight())
            while self._minute and now - self._minute[0] >= 60:
                self._minute.popleft()
            if self.per_minute is not None and len(self._minute) >= self.per_minute:
                raise LLMError("Too many requests to the free AI provider this minute (LLM_REQUESTS_PER_MINUTE).",
                               code="llm_rate_limited", status_code=429,
                               retry_after=60 - (now - self._minute[0]))
            self._minute.append(now)
            self._day = (today, self._day[1] + 1)


class OpenAICompatibleProvider:
    """Any OpenAI-compatible Chat Completions backend described by a ``ProviderConfig``."""

    def __init__(self, settings: Settings, config: ProviderConfig, client: Any | None = None,
                 sleep: Callable[[float], None] = time.sleep, http_client: Any | None = None) -> None:
        import openai

        self._openai = openai
        self.settings = settings
        self.config = config
        self.mode = config.name
        self.model = config.model
        self._sleep = sleep
        self._native_json = settings.llm_json_mode != "prompt"
        self._reasoning_unsupported = False
        self.guard = ProviderRequestGuard(None if config.is_paid else config.requests_per_minute,
                                          None if config.is_paid else config.requests_per_day)
        if client is None:
            headers = dict(config.profile.extra_headers)
            if config.name == "openrouter" and settings.openrouter_app_url:
                headers["HTTP-Referer"] = settings.openrouter_app_url
            client = openai.OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,  # retries are handled here, with a deadline
                default_headers=headers or None,
                http_client=http_client,  # tests inject an httpx.MockTransport here
            )
        self.client = client

    # ── error classification ─────────────────────────────────────────────────
    @staticmethod
    def _error_text(exc: Exception) -> str:
        body = getattr(exc, "body", None)
        return f"{exc} {body}".lower()

    @staticmethod
    def _retry_after(exc: Exception) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        for name in ("retry-after-ms", "retry-after"):
            value = headers.get(name) if hasattr(headers, "get") else None
            if value:
                try:
                    seconds = float(value)
                    return seconds / 1000 if name == "retry-after-ms" else seconds
                except ValueError:
                    pass
        # Gemini puts google.rpc.RetryInfo ("retryDelay": "37s") in the body.
        match = re.search(r"retry[_ ]?delay\W+(\d+(?:\.\d+)?)s", str(getattr(exc, "body", "")), re.IGNORECASE)
        if match:
            return float(match.group(1))
        match = re.search(r"try again in\s+((?:\d+(?:\.\d+)?\s*(?:ms|s|m|h)\s*)+)", str(exc), re.IGNORECASE)
        if match:  # Groq: "Please try again in 7m12.5s" / "in 1.2s"
            total = 0.0
            for number, unit in _DURATION_RE.findall(match.group(1).replace(" ", "")):
                total += float(number) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}.get(unit or "s", 1)
            return total or None
        return None

    def _classify(self, exc: Exception) -> LLMError:
        o = self._openai
        name = self.config.profile.label
        if isinstance(exc, o.APITimeoutError):
            return LLMError(f"{name} timed out", code="llm_timeout", status_code=504, retryable=True)
        if isinstance(exc, o.APIConnectionError):
            hint = " Is Ollama running?" if self.config.name == "ollama" else ""
            return LLMError(f"{name} is unreachable.{hint}", code="llm_unavailable", status_code=502, retryable=True)
        text = self._error_text(exc)
        if isinstance(exc, o.RateLimitError):
            body = getattr(exc, "body", None)
            code = body.get("code") if isinstance(body, dict) else None
            if code == "insufficient_quota":
                return LLMError(f"{name}: account quota is exhausted", code="llm_quota_exceeded", status_code=502)
            retry_after = self._retry_after(exc)
            if any(marker in text for marker in _DAILY_MARKERS):
                return LLMError(f"{name}: the free daily quota is exhausted. Try again later; the engine does not "
                                "switch to another (possibly paid) model.", code="llm_free_quota_exhausted",
                                status_code=429, retry_after=retry_after or _seconds_until_utc_midnight())
            return LLMError(f"{name} is rate limiting requests", code="llm_rate_limited", status_code=429,
                            retryable=True, retry_after=retry_after)
        if isinstance(exc, (o.AuthenticationError, o.PermissionDeniedError)):
            return LLMError(f"{name} rejected the API key or access is denied", code="llm_auth_failed",
                            status_code=502)
        if isinstance(exc, o.NotFoundError):
            return LLMError(f"{name}: model '{self.model}' was not found", code="llm_model_not_found",
                            status_code=502)
        status = getattr(exc, "status_code", 0) or 0
        if status == 413 or "request too large" in text or "context_length" in text or "context length" in text:
            return LLMError(f"{name}: the request is too large for this model or free-tier token limit. "
                            "Lower MAX_INPUT_TOKENS.", code="llm_request_too_large", status_code=413)
        if isinstance(exc, o.BadRequestError):
            if any(marker in text for marker in _JSON_UNSUPPORTED_MARKERS):
                return LLMError(f"{name} does not support JSON mode", code="llm_json_mode_unsupported",
                                status_code=502)
            return LLMError(f"{name} rejected the request", code="llm_bad_request", status_code=502)
        if isinstance(exc, o.InternalServerError) or status >= 500:
            return LLMError(f"{name} returned a server error", code="llm_unavailable", status_code=502,
                            retryable=True)
        if isinstance(exc, o.APIStatusError):
            return LLMError(f"{name} returned an error", code="llm_error", status_code=502)
        return LLMError(f"Unexpected error from {name}", code="llm_error", status_code=502)

    # ── request ──────────────────────────────────────────────────────────────
    def _params(self, request: LLMRequest, native_json: bool) -> dict[str, Any]:
        messages = request.messages
        if request.json_mode and not native_json:
            # Prompt-only JSON: reinforce the format requirement in the system message.
            messages = [dict(m) for m in messages]
            reminder = "\n\nReturn ONLY one valid JSON object. No Markdown fences, no text before or after it."
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += reminder
            else:
                messages.insert(0, {"role": "system", "content": reminder.strip()})
        params: dict[str, Any] = {"model": self.model, "messages": messages,
                                  self.config.profile.max_tokens_param: request.max_output_tokens}
        if self.settings.openai_temperature is not None:
            params["temperature"] = self.settings.openai_temperature
        if request.json_mode and native_json:
            params["response_format"] = {"type": "json_object"}
        if self.config.reasoning_effort and not self._reasoning_unsupported:
            params["reasoning_effort"] = self.config.reasoning_effort
        return params

    def complete(self, request: LLMRequest, *, deadline: float, mock_fn: MockFn | None = None) -> LLMResult:
        warnings: list[str] = []
        max_attempts = 1 + self.settings.llm_max_retries
        last_error: LLMError | None = None
        attempt = 0
        json_fallback_used = False
        while attempt < max_attempts:
            attempt += 1
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                raise last_error or LLMError("Request deadline exceeded", code="llm_timeout", status_code=504)
            self.guard.acquire()
            native_json = self._native_json
            try:
                response = self.client.chat.completions.create(
                    **self._params(request, native_json), timeout=min(self.settings.llm_timeout_seconds, remaining))
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = self._classify(exc)
                logger.warning("llm_call_failed", extra={"provider": self.mode, "error_code": last_error.code,
                                                         "attempt": attempt, "retryable": last_error.retryable})
                if (last_error.code == "llm_bad_request" and "reasoning" in self._error_text(exc)
                        and self.config.reasoning_effort and not self._reasoning_unsupported):
                    # Model does not accept reasoning_effort: drop it once, transparently.
                    self._reasoning_unsupported = True
                    warnings.append("reasoning_effort_not_supported")
                    attempt -= 1
                    continue
                if (last_error.code == "llm_json_mode_unsupported" and request.json_mode and native_json
                        and not json_fallback_used and self.settings.llm_json_mode == "auto"):
                    # Model rejects response_format: switch to prompt-only JSON for this provider.
                    self._native_json = False
                    json_fallback_used = True
                    warnings.append("json_mode_prompt_fallback")
                    attempt -= 1  # the fallback does not consume a retry
                    continue
                if not last_error.retryable or attempt == max_attempts:
                    raise last_error from None
                delay = self.settings.llm_retry_base_delay_seconds * (2 ** (attempt - 1))
                delay += random.uniform(0, delay / 4) if delay else 0  # noqa: S311 - jitter, not crypto
                if last_error.retry_after is not None:
                    if last_error.retry_after > self.settings.llm_max_retry_after_seconds:
                        raise last_error from None  # do not hold the request; client gets Retry-After
                    delay = max(delay, last_error.retry_after)
                if time.monotonic() + delay >= deadline - 1.0:
                    raise last_error from None
                self._sleep(delay)
                continue
            result = self._to_result(response, attempt)
            result.warnings[:0] = warnings
            if request.json_mode and not native_json and "json_mode_prompt_fallback" not in result.warnings:
                result.warnings.insert(0, "json_mode_prompt")
            return result
        raise last_error or LLMError("AI provider call failed")  # pragma: no cover

    def _to_result(self, response: Any, attempts: int) -> LLMResult:
        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        raw = (getattr(message, "content", None) or getattr(message, "refusal", None) or "") if message else ""
        text = strip_reasoning(raw)
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        truncated = bool(choice and choice.finish_reason == "length")
        warnings = ["output_truncated_by_token_limit"] if truncated else []
        if text != raw.strip():
            warnings.append("reasoning_removed")
        if usage is None:
            warnings.append("provider_returned_no_usage")
        returned_model = getattr(response, "model", None) or self.model
        return LLMResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens,
                         model=returned_model, mode=self.mode, attempts=attempts, truncated=truncated,
                         warnings=warnings)


class OpenAIProvider(OpenAICompatibleProvider):
    """Backward-compatible constructor for the paid OpenAI backend."""

    def __init__(self, settings: Settings, client: Any | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__(settings, resolve_provider(settings, "openai"), client=client, sleep=sleep)


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Create the provider selected by LLM_PROVIDER. Never falls back to another provider."""
    name = settings.active_llm_provider
    if name == "mock":
        logger.warning("llm_mock_mode_enabled")
        return MockProvider()
    config = resolve_provider(settings, name)
    if config.problem_code:
        logger.warning("llm_provider_unusable", extra={"provider": name, "error_code": config.problem_code})
        return UnconfiguredProvider(config.model, mode=name, code=config.problem_code,
                                    message=config.problem_message or "AI provider is not configured")
    logger.info("llm_provider_selected", extra={"provider": name, "model": config.model,
                                                 "billing": config.profile.billing})
    return OpenAICompatibleProvider(settings, config)
