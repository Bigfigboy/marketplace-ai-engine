"""Demo limits, rate limiting, spend limits and token limits."""

from __future__ import annotations

from tests.conftest import ScriptedLLM


def test_daily_request_limit_per_user(env):
    for i in range(10):
        r = env.run(env.a.member, "marketing", f"Идея поста номер {i}")
        assert r.status_code == 200, (i, r.text)
    r = env.run(env.a.member, "marketing", "Ещё один пост")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "daily_limit_exceeded"
    assert int(r.headers["Retry-After"]) > 0
    # Limit is per user: another user of the same company is unaffected.
    assert env.run(env.a.admin, "marketing", "Пост").status_code == 200
    stats = env.client.get("/stats/agents", headers=env.a.member).json()
    assert stats["today_runs_by_you"] == 10 and stats["daily_run_limit"] == 10


def test_rejected_requests_do_not_consume_quota(env):
    for _ in range(12):
        r = env.run(env.a.member, "marketing", "Пост", options={"task": "invalid"})
        assert r.status_code == 422
    assert env.run(env.a.member, "marketing", "Пост").status_code == 200


def test_daily_limit_disabled_outside_demo_mode(make_env):
    env = make_env(demo_mode=False, daily_run_limit_per_user=1)
    for _ in range(3):
        assert env.run(env.a.member, "marketing", "Пост").status_code == 200


def test_rate_limit_per_minute(make_env):
    env = make_env(rate_limit_per_minute=3)
    codes = [env.client.get("/agents", headers=env.a.member).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    r = env.client.get("/agents", headers=env.a.member)
    assert r.json()["error"]["code"] == "rate_limited" and "Retry-After" in r.headers


def test_failed_auth_attempts_are_throttled(make_env):
    env = make_env(auth_failures_per_minute=3)
    codes = [env.client.get("/agents", headers={"Authorization": "Bearer aie_wrong_key_000000000000000"}).status_code
             for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_company_spend_limit(make_env):
    # Each scripted call: 1_000_000 input tokens * $1 = $1.00; limit $1.50 per company/day.
    llm = ScriptedLLM(["draft"] * 5, input_tokens=1_000_000, output_tokens=0)
    env = make_env(llm=llm, price_input_per_1m_tokens=1.0, price_output_per_1m_tokens=1.0,
                   company_daily_spend_limit=1.5, daily_spend_limit=100.0)
    assert env.run(env.a.member, "marketing", "Пост 1").status_code == 200
    assert env.run(env.a.member, "marketing", "Пост 2").status_code == 200
    r = env.run(env.a.member, "marketing", "Пост 3")
    assert r.status_code == 429 and r.json()["error"]["code"] == "company_spend_limit_exceeded"
    # Other company is not blocked by company A's spend.
    assert env.run(env.b.member, "marketing", "Пост").status_code == 200


def test_global_spend_limit(make_env):
    llm = ScriptedLLM(["draft"] * 5, input_tokens=1_000_000, output_tokens=0)
    env = make_env(llm=llm, price_input_per_1m_tokens=2.0, price_output_per_1m_tokens=2.0,
                   daily_spend_limit=2.0, company_daily_spend_limit=None)
    assert env.run(env.a.member, "marketing", "Пост").status_code == 200
    r = env.run(env.b.member, "marketing", "Пост")
    assert r.status_code == 429 and r.json()["error"]["code"] == "spend_limit_exceeded"


def test_cost_is_null_when_prices_not_configured(make_env):
    env = make_env(llm=ScriptedLLM(["draft"]), price_input_per_1m_tokens=None, price_output_per_1m_tokens=None)
    body = env.run(env.a.member, "marketing", "Пост").json()
    assert body["usage"]["estimated_cost"] is None and body["usage"]["input_tokens"] == 1200


def test_message_length_and_input_token_limits(make_env):
    env = make_env(llm=ScriptedLLM(["x"]), max_message_chars=100)
    r = env.run(env.a.member, "marketing", "a" * 101)
    assert r.status_code == 422 and r.json()["error"]["code"] == "message_too_long"
    env2 = make_env(llm=ScriptedLLM(["x"]), max_input_tokens=500, max_message_chars=8000)
    r = env2.run(env2.a.member, "marketing", "слово " * 1000)
    assert r.status_code == 413 and r.json()["error"]["code"] == "input_token_limit_exceeded"


def test_output_token_limit_is_passed_to_provider(make_env):
    llm = ScriptedLLM(["x"])
    env = make_env(llm=llm, max_output_tokens=321)
    env.run(env.a.member, "marketing", "Пост")
    assert llm.requests[0].max_output_tokens == 321
