"""OpenAI provider: missing key, API errors, retries, timeouts and usage accounting.

The real ``openai`` SDK is used, pointed at an in-process ``httpx.MockTransport`` that
imitates the Chat Completions endpoint. No network traffic leaves the test process.
"""

from __future__ import annotations

import json
import time

import httpx
import openai
import pytest

from app.core.errors import LLMError
from app.services.llm import LLMRequest, OpenAIProvider
from tests.conftest import make_settings


def _completion(text: str = "Привет", prompt_tokens: int = 50, completion_tokens: int = 7, finish: str = "stop"):
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


def _provider(tmp_path, handler, **overrides) -> tuple[OpenAIProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    settings = make_settings(tmp_path, llm_mode="openai", openai_api_key="sk-test-not-a-real-key", **overrides)
    client = openai.OpenAI(api_key="sk-test-not-a-real-key", base_url="https://api.test.local/v1",
                           max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(_wrapped)))
    return OpenAIProvider(settings, client=client, sleep=lambda s: None), seen


REQ = LLMRequest(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                 max_output_tokens=200, json_mode=True)


def _deadline(seconds: float = 30) -> float:
    return time.monotonic() + seconds


def test_success_parses_usage_and_sends_limits(tmp_path):
    provider, seen = _provider(tmp_path, lambda req, n: httpx.Response(200, json=_completion()))
    result = provider.complete(REQ, deadline=_deadline())
    assert result.text == "Привет" and result.input_tokens == 50 and result.output_tokens == 7
    body = json.loads(seen[0].content)
    assert body["model"] == "test-model" and body["max_completion_tokens"] == 200
    assert body["response_format"] == {"type": "json_object"}
    assert "temperature" not in body  # only sent when OPENAI_TEMPERATURE is configured
    assert seen[0].headers["authorization"] == "Bearer sk-test-not-a-real-key"


def test_retries_transient_errors_then_succeeds(tmp_path):
    def handler(req, n):
        if n == 1:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        if n == 2:
            return httpx.Response(429, json={"error": {"message": "slow down", "code": "rate_limit_exceeded"}})
        return httpx.Response(200, json=_completion("ok"))

    provider, seen = _provider(tmp_path, handler, llm_max_retries=2)
    result = provider.complete(REQ, deadline=_deadline())
    assert result.text == "ok" and result.attempts == 3 and len(seen) == 3


def test_retry_is_bounded(tmp_path):
    provider, seen = _provider(tmp_path, lambda r, n: httpx.Response(503, json={"error": {"message": "x"}}),
                               llm_max_retries=2)
    with pytest.raises(LLMError) as exc:
        provider.complete(REQ, deadline=_deadline())
    assert exc.value.code == "llm_unavailable" and exc.value.status_code == 502
    assert len(seen) == 3  # 1 attempt + 2 retries


@pytest.mark.parametrize("status,body,code", [
    (401, {"error": {"message": "bad key", "code": "invalid_api_key"}}, "llm_auth_failed"),
    (404, {"error": {"message": "no model", "code": "model_not_found"}}, "llm_model_not_found"),
    (400, {"error": {"message": "bad", "code": None}}, "llm_bad_request"),
    (429, {"error": {"message": "quota", "code": "insufficient_quota"}}, "llm_quota_exceeded"),
])
def test_non_retryable_errors(tmp_path, status, body, code):
    provider, seen = _provider(tmp_path, lambda r, n: httpx.Response(status, json=body))
    with pytest.raises(LLMError) as exc:
        provider.complete(REQ, deadline=_deadline())
    assert exc.value.code == code and len(seen) == 1


def test_timeout_maps_to_504(tmp_path):
    def handler(req, n):
        raise httpx.ReadTimeout("timed out", request=req)

    provider, seen = _provider(tmp_path, handler, llm_max_retries=1)
    with pytest.raises(LLMError) as exc:
        provider.complete(REQ, deadline=_deadline())
    assert exc.value.code == "llm_timeout" and exc.value.status_code == 504 and len(seen) == 2


def test_deadline_stops_retries(tmp_path):
    provider, seen = _provider(tmp_path, lambda r, n: httpx.Response(500, json={}), llm_max_retries=5)
    with pytest.raises(LLMError):
        provider.complete(REQ, deadline=time.monotonic() + 0.5)
    assert len(seen) == 0  # not enough time left for a meaningful attempt


def test_truncated_output_warns(tmp_path):
    provider, _ = _provider(tmp_path, lambda r, n: httpx.Response(200, json=_completion(finish="length")))
    assert "output_truncated_by_token_limit" in provider.complete(REQ, deadline=_deadline()).warnings


def test_missing_api_key_returns_503_and_logs_run(make_env):
    env = make_env(llm_mode="openai", openai_api_key=None)
    health = env.client.get("/health").json()
    assert health["status"] == "degraded" and health["llm_configured"] is False
    r = env.run(env.a.member, "marketing", "Создай пост для кофейни")
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "llm_not_configured" and "OPENAI_API_KEY" in err["message"]
    assert err["details"]["run_id"]
    run = env.client.get(f"/runs/{err['details']['run_id']}", headers=env.a.member).json()
    assert run["status"] == "error" and run["error_code"] == "llm_not_configured"


def test_llm_api_error_through_the_api(make_env, tmp_path):
    provider, seen = _provider(tmp_path, lambda r, n: httpx.Response(500, json={"error": {"message": "x"}}),
                               llm_max_retries=1)
    env = make_env(llm=provider)
    r = env.run(env.a.member, "marketing", "Создай пост")
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "llm_unavailable" and "boom" not in err["message"]
    assert len(seen) == 2
    run = env.client.get(f"/runs/{err['details']['run_id']}", headers=env.a.member).json()
    assert run["status"] == "error" and run["error_code"] == "llm_unavailable"


def test_end_to_end_with_sdk_transport(make_env, tmp_path):
    provider, seen = _provider(tmp_path, lambda r, n: httpx.Response(200, json=_completion(
        "## План\n- пост", prompt_tokens=900, completion_tokens=120)))
    env = make_env(llm=provider)
    body = env.run(env.a.member, "marketing", "Контент-план для пекарни").json()
    assert body["mode"] == "openai" and body["usage"]["input_tokens"] == 900
    assert body["usage"]["output_tokens"] == 120
    assert abs(body["usage"]["estimated_cost"] - (900 * 0.4 + 120 * 1.6) / 1e6) < 1e-12
