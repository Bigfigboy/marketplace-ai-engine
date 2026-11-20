"""LLM provider profiles and configuration resolution.

Every supported backend except ``mock`` speaks the OpenAI-compatible Chat Completions
protocol, so one client implementation (``app.services.llm.OpenAICompatibleProvider``)
serves all of them; a profile captures only what differs: base URL, API-key variable,
default model, parameter quirks, the free-model allowlist and default free-tier limits.

Cost safety rules (enforced here and in ``llm.py``):
* the provider is chosen only by ``LLM_PROVIDER``; the engine never falls back to another
  provider or model on errors or quota exhaustion;
* for free providers the configured model must be in the allowlist (built-in, or the
  explicit ``LLM_ALLOWED_MODELS``); OpenRouter models must additionally end in ``:free``
  and that rule cannot be overridden;
* OpenAI is the only paid provider and is used only when selected explicitly.

Model ids and free-tier numbers below were checked against the providers' official
documentation on 2026-11-20 (see docs/FREE_LLM_PROVIDERS.md); providers change them often.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.core.config import Settings

Billing = Literal["paid", "free", "local", "none"]


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    label: str
    billing: Billing
    base_url: str | None
    default_model: str
    key_setting: str | None          # Settings attribute holding the API key
    model_setting: str | None        # Settings attribute holding the provider-specific model
    key_env: str | None              # env var name, for messages
    max_tokens_param: str = "max_tokens"
    allowed_models: tuple[str, ...] | None = None   # None = no restriction
    require_free_suffix: bool = False
    default_rpm: int | None = None
    default_rpd: int | None = None
    default_reasoning_effort: str | None = None  # sent as reasoning_effort unless overridden
    extra_headers: dict[str, str] = field(default_factory=dict)


PROFILES: dict[str, ProviderProfile] = {
    "mock": ProviderProfile(
        name="mock", label="Mock (offline, not AI)", billing="none", base_url=None, default_model="mock",
        key_setting=None, model_setting=None, key_env=None),
    "openai": ProviderProfile(
        name="openai", label="OpenAI (paid)", billing="paid", base_url=None, default_model="gpt-4.1-mini",
        key_setting="openai_api_key", model_setting="openai_model", key_env="OPENAI_API_KEY",
        max_tokens_param="max_completion_tokens"),
    # Groq: llama-3.x models were retired on 2026-08-16; gpt-oss / qwen3.8 are current.
    # Free-plan limits are per account (console.groq.com/settings/limits); defaults are conservative.
    "groq": ProviderProfile(
        name="groq", label="Groq (free plan)", billing="free", base_url="https://api.groq.com/openai/v1",
        default_model="openai/gpt-oss-20b", key_setting="groq_api_key", model_setting="groq_model",
        key_env="GROQ_API_KEY", max_tokens_param="max_completion_tokens",
        allowed_models=("openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"),
        default_rpm=30, default_rpd=1000, default_reasoning_effort="low"),
    # Gemini: 2.5 models are served only to earlier users since 2026-09-18; 3.x thinking cannot be
    # disabled, "low" keeps it cheap in tokens. Free limits are per project; one public report shows
    # 20 requests/day for 3.8 Flash, so the default daily guard is 20.
    "gemini": ProviderProfile(
        name="gemini", label="Google Gemini API (free tier)", billing="free",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-3.5-flash-lite", key_setting="gemini_api_key", model_setting="gemini_model",
        key_env="GEMINI_API_KEY",
        allowed_models=("gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-flash-lite"),
        default_rpm=10, default_rpd=20, default_reasoning_effort="low"),
    # OpenRouter: only explicitly listed ":free" variants; 20 req/min shared, 50 req/day
    # (1000/day after a one-time $10 credit purchase). The "openrouter/free" router is not allowed:
    # the model must be chosen explicitly.
    "openrouter": ProviderProfile(
        name="openrouter", label="OpenRouter (free models only)", billing="free",
        base_url="https://openrouter.ai/api/v1",
        default_model="google/gemma-4-26b-a4b-it:free", key_setting="openrouter_api_key",
        model_setting="openrouter_model", key_env="OPENROUTER_API_KEY",
        allowed_models=("google/gemma-4-26b-a4b-it:free", "openai/gpt-oss-120b:free",
                        "nvidia/nemotron-3-ultra-550b-a55b:free"),
        require_free_suffix=True, default_rpm=20, default_rpd=50,
        extra_headers={"X-Title": "AI Agent Marketplace Engine (demo)"}),
    # Ollama: local, no key. qwen3 (Apache-2.0); reasoning_effort "none" turns thinking off.
    "ollama": ProviderProfile(
        name="ollama", label="Ollama (local model)", billing="local", base_url="http://localhost:11434/v1",
        default_model="qwen3:4b", key_setting=None, model_setting="ollama_model", key_env=None,
        default_reasoning_effort="none"),
}

PROVIDER_NAMES = tuple(PROFILES)


def is_paid_provider(name: str | None) -> bool:
    profile = PROFILES.get(name or "")
    return profile is not None and profile.billing == "paid"


@dataclass(frozen=True)
class ProviderConfig:
    profile: ProviderProfile
    model: str
    base_url: str | None
    api_key: str | None = field(repr=False)
    requests_per_minute: int | None
    requests_per_day: int | None
    allowed_models: tuple[str, ...] | None
    reasoning_effort: str | None = None
    problem_code: str | None = None      # set when the provider must not be used
    problem_message: str | None = None

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def is_paid(self) -> bool:
        return self.profile.billing == "paid"


def _secret(settings: Settings, attr: str | None) -> str | None:
    if not attr:
        return None
    value = getattr(settings, attr, None)
    raw = value.get_secret_value() if value is not None and hasattr(value, "get_secret_value") else value
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _reasoning(configured: str | None, default: str | None) -> str | None:
    if configured is None or not configured.strip():
        return default
    return None if configured.strip().lower() == "off" else configured.strip()


def resolve_provider(settings: Settings, name: str | None = None) -> ProviderConfig:
    """Resolve the active (or named) provider from settings, validating cost-safety rules."""
    profile = PROFILES[name or settings.active_llm_provider]
    model = (settings.llm_model or (getattr(settings, profile.model_setting, None) if profile.model_setting else None)
             or profile.default_model).strip()
    base_url = settings.llm_base_url or (settings.ollama_base_url if profile.name == "ollama" else None) \
        or (settings.openai_base_url if profile.name == "openai" else None) or profile.base_url
    api_key = _secret(settings, profile.key_setting)
    if profile.name == "ollama":
        api_key = "ollama"  # Ollama ignores the key, but the SDK requires a non-empty value
    allowed = settings.llm_allowed_model_list or profile.allowed_models
    cfg = dict(profile=profile, model=model, base_url=base_url, api_key=api_key,
               requests_per_minute=settings.llm_requests_per_minute or profile.default_rpm,
               requests_per_day=settings.llm_requests_per_day or profile.default_rpd,
               allowed_models=allowed,
               reasoning_effort=_reasoning(settings.llm_reasoning_effort, profile.default_reasoning_effort))

    if profile.key_setting and not api_key:
        return ProviderConfig(**cfg, problem_code="llm_not_configured",
                              problem_message=f"{profile.label}: API key is not configured. Set {profile.key_env} "
                                              f"or use LLM_PROVIDER=mock.")
    if profile.require_free_suffix and not model.endswith(":free"):
        return ProviderConfig(**cfg, problem_code="llm_model_not_allowed",
                              problem_message=f"Only free OpenRouter models (ending in ':free') are allowed; "
                                              f"'{model}' was refused to prevent paid usage.")
    if profile.billing == "free" and allowed is not None and model not in allowed:
        return ProviderConfig(**cfg, problem_code="llm_model_not_allowed",
                              problem_message=f"Model '{model}' is not in the free-model allowlist for "
                                              f"{profile.label}. Choose one of: {', '.join(allowed)} "
                                              f"(or set LLM_ALLOWED_MODELS explicitly).")
    return ProviderConfig(**cfg)
