"""Multi-provider LLM layer: Groq, Gemini, OpenRouter, Ollama, OpenAI, Mock.

No real network calls and no real API keys: the official ``openai`` SDK is pointed at an
in-process ``httpx.MockTransport`` that imitates each provider's OpenAI-compatible endpoint
(including their rate-limit error formats). All data is fictional.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.core.errors import LLMError
from app.services.jsonutil import parse_json_object
from app.services.llm import (
    LLMRequest,
    MockProvider,
    OpenAICompatibleProvider,
    UnconfiguredProvider,
    build_llm_provider,
    strip_reasoning,
)
from app.services.llm_providers import PROFILES, resolve_provider
from tests.conftest import KB_COFFEE_RU, make_settings

FAKE_KEYS = {"groq": {"groq_api_key": "gsk_test_not_real"}, "gemini": {"gemini_api_key": "gemini-test-not-real"},
             "openrouter": {"openrouter_api_key": "sk-or-test-not-real"}, "ollama": {},
             "openai": {"openai_api_key": "sk-test-not-real"}}


def completion(text: str = "ok", prompt_tokens: int = 40, completion_tokens: int = 9, model: str = "m"):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens}}


class Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request, len(self.requests))

    def body(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)


def provider(tmp_path, name: str, handler, **overrides):
    rec = Recorder(handler)
    settings = make_settings(tmp_path, llm_provider=name, llm_retry_base_delay_seconds=0.0,
                             **FAKE_KEYS.get(name, {}), **overrides)
    config = resolve_provider(settings)
    assert config.problem_code is None, config.problem_message
    p = OpenAICompatibleProvider(settings, config, sleep=rec.sleeps.append,
                                 http_client=httpx.Client(transport=httpx.MockTransport(rec)))
    return p, rec


REQ = LLMRequest(messages=[{"role": "system", "content": "Reply with a JSON object."},
                           {"role": "user", "content": "Вымышленный вопрос"}], max_output_tokens=300, json_mode=True)


def deadline(s: float = 30) -> float:
    return time.monotonic() + s


# ── selection and configuration ──────────────────────────────────────────────
@pytest.mark.parametrize("name,host,path,model", [
    ("groq", "api.groq.com", "/openai/v1/chat/completions", "openai/gpt-oss-20b"),
    ("gemini", "generativelanguage.googleapis.com", "/v1beta/openai/chat/completions", "gemini-3.5-flash-lite"),
    ("openrouter", "openrouter.ai", "/api/v1/chat/completions", "google/gemma-4-26b-a4b-it:free"),
    ("ollama", "localhost", "/v1/chat/completions", "qwen3:4b"),
    ("openai", "api.openai.com", "/v1/chat/completions", "test-model"),
])
def test_each_provider_sends_openai_compatible_request(tmp_path, name, host, path, model):
    p, rec = provider(tmp_path, name, lambda r, n: httpx.Response(200, json=completion('{"a": 1}')))
    result = p.complete(REQ, deadline=deadline())
    req = rec.requests[0]
    assert req.url.host == host and req.url.path == path
    body = rec.body()
    assert body["model"] == model and result.mode == name
    assert result.input_tokens == 40 and result.output_tokens == 9
    assert body["response_format"] == {"type": "json_object"}
    assert "models" not in body  # never a fallback model list
    token_param = PROFILES[name].max_tokens_param
    assert body[token_param] == 300
    expected_effort = {"groq": "low", "gemini": "low", "ollama": "none"}.get(name)
    assert body.get("reasoning_effort") == expected_effort
    auth = req.headers["authorization"]
    assert auth == ("Bearer ollama" if name == "ollama" else f"Bearer {next(iter(FAKE_KEYS[name].values()))}")
    if name == "openrouter":
        assert req.headers["x-title"].startswith("AI Agent Marketplace Engine")


def test_build_provider_from_settings(tmp_path):
    assert isinstance(build_llm_provider(make_settings(tmp_path, llm_provider="mock")), MockProvider)
    # Legacy LLM_MODE still works when LLM_PROVIDER is not set.
    assert isinstance(build_llm_provider(make_settings(tmp_path, llm_mode="mock")), MockProvider)
    ollama = build_llm_provider(make_settings(tmp_path, llm_provider="ollama", ollama_model="qwen3:8b"))
    assert isinstance(ollama, OpenAICompatibleProvider) and ollama.model == "qwen3:8b"
    for name, env in (("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("openrouter", "OPENROUTER_API_KEY"),
                      ("openai", "OPENAI_API_KEY")):
        p = build_llm_provider(make_settings(tmp_path, llm_provider=name))
        assert isinstance(p, UnconfiguredProvider) and p.code == "llm_not_configured" and env in p.message
        with pytest.raises(LLMError) as exc:
            p.complete(REQ, deadline=deadline())
        assert exc.value.status_code == 503


def test_llm_model_override_and_custom_ollama_url(tmp_path):
    cfg = resolve_provider(make_settings(tmp_path, llm_provider="ollama", llm_model="qwen3:1.7b",
                                         ollama_base_url="http://ollama:11434/v1"))
    assert cfg.model == "qwen3:1.7b" and cfg.base_url == "http://ollama:11434/v1" and cfg.problem_code is None


# ── no paid models, no automatic switching ───────────────────────────────────
def test_openrouter_only_explicit_free_models(tmp_path):
    def cfg(model, **kw):
        return resolve_provider(make_settings(tmp_path, llm_provider="openrouter", openrouter_model=model,
                                              openrouter_api_key="k", **kw))
    assert cfg("google/gemma-4-26b-a4b-it:free").problem_code is None
    for paid in ("openai/gpt-4o", "anthropic/some-model", "openrouter/auto", "openrouter/free",
                 "google/gemma-4-26b-a4b-it"):
        assert cfg(paid).problem_code == "llm_model_not_allowed", paid
    # Even an explicit allowlist cannot enable a non-":free" model.
    assert cfg("openai/gpt-4o", llm_allowed_models="openai/gpt-4o").problem_code == "llm_model_not_allowed"
    # A ":free" model outside the built-in list needs an explicit allowlist entry.
    assert cfg("vendor/new-model:free").problem_code == "llm_model_not_allowed"
    assert cfg("vendor/new-model:free", llm_allowed_models="vendor/new-model:free").problem_code is None


def test_free_providers_reject_models_outside_allowlist(tmp_path):
    s = make_settings(tmp_path, llm_provider="groq", groq_api_key="k", groq_model="some-paid-or-retired-model")
    p = build_llm_provider(s)
    assert isinstance(p, UnconfiguredProvider) and p.code == "llm_model_not_allowed"
    s = make_settings(tmp_path, llm_provider="gemini", gemini_api_key="k", gemini_model="gemini-3.1-pro-preview")
    assert resolve_provider(s).problem_code == "llm_model_not_allowed"


def test_daily_quota_is_not_retried_and_never_switches_model(tmp_path):
    body = {"error": {"message": "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000.",
                      "code": 429}}
    p, rec = provider(tmp_path, "openrouter", lambda r, n: httpx.Response(429, json=body), llm_max_retries=3)
    with pytest.raises(LLMError) as exc:
        p.complete(REQ, deadline=deadline())
    assert exc.value.code == "llm_free_quota_exhausted" and exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) > 0
    assert len(rec.requests) == 1 and rec.body()["model"] == "google/gemma-4-26b-a4b-it:free"


# ── free-tier rate limits ────────────────────────────────────────────────────
def test_short_retry_after_is_honored(tmp_path):
    def handler(r, n):
        if n == 1:
            return httpx.Response(429, headers={"retry-after": "2"},
                                  json={"error": {"message": "Rate limit reached for model on requests per minute"}})
        return httpx.Response(200, json=completion("{}"))
    p, rec = provider(tmp_path, "groq", handler)
    assert p.complete(REQ, deadline=deadline()).attempts == 2
    assert rec.sleeps == [2.0]


def test_long_retry_after_fails_fast_with_header(tmp_path):
    p, rec = provider(tmp_path, "groq", lambda r, n: httpx.Response(
        429, headers={"retry-after": "120"}, json={"error": {"message": "requests per minute (RPM)"}}),
        llm_max_retry_after_seconds=20)
    with pytest.raises(LLMError) as exc:
        p.complete(REQ, deadline=deadline())
    assert exc.value.code == "llm_rate_limited" and exc.value.headers["Retry-After"] == "120"
    assert len(rec.requests) == 1 and rec.sleeps == []


def test_groq_daily_token_limit_message(tmp_path):
    msg = ("Rate limit reached for model `openai/gpt-oss-20b` on tokens per day (TPD): Limit 200000, "
           "Used 199990. Please try again in 7m12.5s.")
    p, _ = provider(tmp_path, "groq", lambda r, n: httpx.Response(429, json={"error": {"message": msg}}))
    with pytest.raises(LLMError) as exc:
        p.complete(REQ, deadline=deadline())
    assert exc.value.code == "llm_free_quota_exhausted" and exc.value.retry_after == pytest.approx(432.5)


def test_gemini_resource_exhausted_retry_delay(tmp_path):
    body = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota.",
                       "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                    "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
                                   {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}]}}]

    def handler(r, n):
        return httpx.Response(429, json=body) if n == 1 else httpx.Response(200, json=completion("{}"))
    p, rec = provider(tmp_path, "gemini", handler)
    assert p.complete(REQ, deadline=deadline()).attempts == 2 and rec.sleeps == [3.0]


def test_gemini_daily_quota_detected(tmp_path):
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": "Quota exceeded for metric generate_content_free_tier_requests, "
                                 "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}
    p, rec = provider(tmp_path, "gemini", lambda r, n: httpx.Response(429, json=body), llm_max_retries=3)
    with pytest.raises(LLMError) as exc:
        p.complete(REQ, deadline=deadline())
    assert exc.value.code == "llm_free_quota_exhausted" and len(rec.requests) == 1


def test_engine_side_guard_stops_before_provider_limit(tmp_path):
    p, rec = provider(tmp_path, "openrouter", lambda r, n: httpx.Response(200, json=completion("{}")),
                      llm_requests_per_minute=2)
    p.complete(REQ, deadline=deadline())
    p.complete(REQ, deadline=deadline())
    with pytest.raises(LLMError) as exc:
        p.complete(REQ, deadline=deadline())
    assert exc.value.code == "llm_rate_limited" and exc.value.status_code == 429 and "Retry-After" in exc.value.headers
    assert len(rec.requests) == 2  # third request was never sent
    p2, rec2 = provider(tmp_path, "gemini", lambda r, n: httpx.Response(200, json=completion("{}")),
                        llm_requests_per_day=1)
    p2.complete(REQ, deadline=deadline())
    with pytest.raises(LLMError) as exc:
        p2.complete(REQ, deadline=deadline())
    assert exc.value.code == "llm_free_quota_exhausted" and len(rec2.requests) == 1


def test_paid_openai_has_no_engine_side_free_guard(tmp_path):
    p, rec = provider(tmp_path, "openai", lambda r, n: httpx.Response(200, json=completion("{}")),
                      llm_requests_per_minute=1)
    p.complete(REQ, deadline=deadline())
    p.complete(REQ, deadline=deadline())
    assert len(rec.requests) == 2


def test_request_too_large_for_free_tier(tmp_path):
    p, _ = provider(tmp_path, "groq", lambda r, n: httpx.Response(
        413, json={"error": {"message": "Request too large for model on tokens per minute (TPM)"}}))
    with pytest.raises(LLMError) as exc:
        p.complete(REQ, deadline=deadline())
    assert exc.value.code == "llm_request_too_large" and exc.value.status_code == 413


def test_ollama_not_running(tmp_path):
    def handler(r, n):
        raise httpx.ConnectError("connection refused", request=r)
    p, _ = provider(tmp_path, "ollama", handler, llm_max_retries=0)
    with pytest.raises(LLMError) as exc:
        p.complete(REQ, deadline=deadline())
    assert exc.value.code == "llm_unavailable" and "Ollama" in exc.value.message


# ── structured JSON compatibility ────────────────────────────────────────────
@pytest.mark.parametrize("raw", [
    '{"status": "answered", "source_ids": ["S1"]}',
    '```json\n{"status": "answered", "source_ids": ["S1"]}\n```',
    '<think>Нужно найти доставку во фрагменте S1.</think>\n{"status": "answered", "source_ids": ["S1"]}',
    'Вот ответ:\n{"status": "answered", "source_ids": ["S1"]}\nГотово.',
])
def test_json_output_variants_parse(tmp_path, raw):
    p, _ = provider(tmp_path, "ollama", lambda r, n: httpx.Response(200, json=completion(raw)))
    parsed = parse_json_object(p.complete(REQ, deadline=deadline()).text)
    assert parsed == {"status": "answered", "source_ids": ["S1"]}


def test_strip_reasoning():
    assert strip_reasoning("<think>a\nb</think>\n\nОтвет") == "Ответ"
    assert strip_reasoning("<think>never closed") == ""  # -> llm_empty_output upstream
    assert strip_reasoning("Обычный текст") == "Обычный текст"


def test_json_mode_unsupported_falls_back_to_prompt_mode(tmp_path):
    def handler(r, n):
        body = json.loads(r.content)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format json_object is not supported "
                                                                 "by this model"}})
        return httpx.Response(200, json=completion('{"ok": true}'))
    p, rec = provider(tmp_path, "openrouter", handler, llm_max_retries=0)
    result = p.complete(REQ, deadline=deadline())
    assert parse_json_object(result.text) == {"ok": True}
    assert "json_mode_prompt_fallback" in result.warnings
    second = rec.body(1)
    assert "response_format" not in second
    assert "Return ONLY one valid JSON object" in second["messages"][0]["content"]
    # Remembered: later calls go straight to prompt mode.
    p.complete(REQ, deadline=deadline())
    assert len(rec.requests) == 3 and "response_format" not in rec.body(2)


def test_reasoning_effort_dropped_when_unsupported(tmp_path):
    def handler(r, n):
        if "reasoning_effort" in json.loads(r.content):
            return httpx.Response(400, json={"error": {"message": "Unrecognized parameter: reasoning_effort"}})
        return httpx.Response(200, json=completion("{}"))
    p, rec = provider(tmp_path, "gemini", handler, llm_max_retries=0)
    result = p.complete(REQ, deadline=deadline())
    assert "reasoning_effort_not_supported" in result.warnings and "reasoning_effort" not in rec.body(1)


def test_reasoning_effort_can_be_disabled(tmp_path):
    p, rec = provider(tmp_path, "ollama", lambda r, n: httpx.Response(200, json=completion("{}")),
                      llm_reasoning_effort="off")
    p.complete(REQ, deadline=deadline())
    assert "reasoning_effort" not in rec.body()


# ── end to end through the API with each free provider ───────────────────────
def _support_reply(r, n):
    return httpx.Response(200, json=completion(
        '<think>S1 содержит цену.</think>```json\n{"status": "answered", "intent": "question", '
        '"answer": "Доставка стоит 1 000 тенге.", "source_ids": ["S1"], "handoff": null}\n```'))


@pytest.mark.parametrize("name", ["groq", "gemini", "openrouter", "ollama"])
def test_agents_work_unchanged_with_free_providers(make_env, tmp_path, name):
    p, rec = provider(tmp_path, name, _support_reply)
    env = make_env(llm=p, price_input_per_1m_tokens=5.0, price_output_per_1m_tokens=5.0)
    assert env.upload(env.a.admin, "kb.txt", KB_COFFEE_RU.encode()).status_code == 201
    body = env.run(env.a.member, "support", "Сколько стоит доставка?").json()
    assert body["status"] == "success" and body["mode"] == name, body
    assert body["sources"] and "1 000 тенге" in body["response"]
    assert body["usage"]["estimated_cost"] == 0.0  # free provider: never priced, even if prices are set
    assert "reasoning_removed" in body["warnings"]
    # Marketing and HR go through the same interface.
    rec.handler = lambda r, n: httpx.Response(200, json=completion("## Контент-план\n| День | Пост |"))
    m = env.run(env.a.member, "marketing", "Контент-план для вымышленной кофейни").json()
    assert m["status"] == "success" and m["mode"] == name
    rec.handler = lambda r, n: httpx.Response(200, json=completion(json.dumps(
        {"job_title": "Бариста", "questions": [{"question": "Опишите ваш опыт", "category": "experience"}]},
        ensure_ascii=False)))
    h = env.run(env.a.member, "hr", "Вопросы для собеседования бариста").json()
    assert h["status"] == "success" and h["mode"] == name and "Опишите ваш опыт" in h["response"]


def test_free_quota_error_reaches_client_with_retry_after(make_env, tmp_path):
    body = {"error": {"message": "Rate limit exceeded: free-models-per-day"}}
    p, _ = provider(tmp_path, "openrouter", lambda r, n: httpx.Response(429, json=body))
    env = make_env(llm=p)
    r = env.run(env.a.member, "marketing", "Пост")
    assert r.status_code == 429 and r.json()["error"]["code"] == "llm_free_quota_exhausted"
    assert int(r.headers["Retry-After"]) > 0
    run = env.client.get(f"/runs/{r.json()['error']['details']['run_id']}", headers=env.a.member).json()
    assert run["status"] == "error" and run["error_code"] == "llm_free_quota_exhausted"


def test_health_reports_provider(make_env):
    env = make_env(llm_provider="mock")
    h = env.client.get("/health").json()
    assert h["status"] == "ok" and h["llm_provider"] == "mock" and h["llm_billing"] == "none"
    env2 = make_env(llm_provider="groq")  # no key: degraded, clear reason, nothing sent
    h2 = env2.client.get("/health").json()
    assert h2["status"] == "degraded" and h2["llm_problem"] == "llm_not_configured" and h2["llm_billing"] == "free"
    r = env2.run(env2.a.member, "marketing", "Пост")
    assert r.status_code == 503 and "GROQ_API_KEY" in r.json()["error"]["message"]


def test_render_mock_mode_needs_no_api_key(make_env):
    """render.yaml default: LLM_PROVIDER=mock, no provider keys at all."""
    env = make_env(llm_provider="mock", openai_api_key=None)
    assert env.client.get("/health").json()["status"] == "ok"
    body = env.run(env.a.member, "marketing", "Пост для вымышленной кофейни").json()
    assert body["mode"] == "mock" and "MOCK" in body["response"]
