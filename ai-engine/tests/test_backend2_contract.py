"""Integration contract with Backend 2 (Supabase), as documented in docs/API_CONTRACT.md and
docs/SUPABASE_INTEGRATION.md. These tests freeze the behaviour Backend 2 relies on.

Backend 2 calls the engine server-to-server with a short-lived "engine token" (HS256 JWT signed with
the engine's JWT_SECRET) carrying the Supabase user id, the business (tenant) id and the engine role.
A raw Supabase Auth access token is NOT accepted. All ids and data below are fictional.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from tests.conftest import KB_COFFEE_RU, ScriptedLLM, json_reply

ENGINE_SECRET = "test-engine-secret-not-a-real-one-0123456789abcdef"
SUPABASE_SECRET = "test-supabase-secret-not-a-real-one-0123456789"
AUD, ISS = "ai-engine", "marketplace-backend"

RUN_RESPONSE_KEYS = {"run_id", "agent", "agent_version", "status", "response", "language", "sources", "usage",
                     "execution_time_ms", "mode", "model", "data", "warnings"}
USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens", "estimated_cost", "currency"}
RUN_RECORD_KEYS = {"id", "agent_slug", "agent_version", "status", "input_tokens", "output_tokens", "estimated_cost",
                   "execution_time_ms", "error_code", "language", "llm_mode", "model", "file_count", "source_count",
                   "created_at"}


def engine_token(sub: str, company_id: str, role: str = "member", *, ttl: int = 300, secret: str = ENGINE_SECRET,
                 aud: str | None = AUD, iss: str | None = ISS, **extra) -> str:
    now = int(time.time())
    claims = {"sub": sub, "company_id": company_id, "role": role, "iat": now, "exp": now + ttl, **extra}
    if aud:
        claims["aud"] = aud
    if iss:
        claims["iss"] = iss
    return jwt.encode(claims, secret, algorithm="HS256")


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def b2(make_env):
    def factory(llm=None, **overrides):
        return make_env(llm=llm, jwt_secret=ENGINE_SECRET, jwt_audience=AUD, jwt_issuer=ISS, **overrides)
    return factory


def test_raw_supabase_access_token_is_rejected(b2):
    env = b2()
    now = int(time.time())
    supabase_like = {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
                     "email": "fictional@example.com", "iat": now, "exp": now + 3600, "iss": "https://x.supabase.co/auth/v1"}
    for secret in (SUPABASE_SECRET, ENGINE_SECRET):  # neither its own key nor the engine key makes it valid
        r = env.client.get("/agents", headers=h(jwt.encode(supabase_like, secret, algorithm="HS256")))
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_engine_token_from_backend2_end_to_end(b2):
    llm = ScriptedLLM([json_reply(status="answered", intent="question", answer="Доставка стоит 1 000 тенге.",
                                  source_ids=["S1"], handoff=None)])
    env = b2(llm=llm)
    business_id, owner_id = str(uuid.uuid4()), str(uuid.uuid4())
    owner = h(engine_token(owner_id, business_id, "admin", company_name="Fictional Coffee LLP"))

    agents = env.client.get("/agents", headers=owner)
    assert agents.status_code == 200 and {a["slug"] for a in agents.json()["agents"]} >= {"marketing", "support", "hr"}

    up = env.client.post("/files/upload", headers=owner, data={"purpose": "knowledge_base"},
                         files={"file": ("kb.txt", KB_COFFEE_RU.encode(), "text/plain")})
    assert up.status_code == 201
    r = env.client.post("/agents/support/run", headers=owner,
                        json={"message": "Сколько стоит доставка?", "file_ids": [up.json()["id"]]})
    assert r.status_code == 200 and "x-request-id" in r.headers
    body = r.json()
    assert set(body) == RUN_RESPONSE_KEYS and set(body["usage"]) == USAGE_KEYS
    assert body["status"] == "success" and body["sources"][0]["file_id"] == up.json()["id"]

    run = env.client.get(f"/runs/{body['run_id']}", headers=owner)
    assert run.status_code == 200 and set(run.json()) == RUN_RECORD_KEYS
    assert run.json()["status"] == "success"

    from app.database.models import Company, User
    with env.db() as db:  # identities are stored under the marketplace (Supabase) ids
        company = db.query(Company).filter_by(external_id=business_id).one()
        assert company.name == "Fictional Coffee LLP"
        assert db.query(User).filter_by(company_id=company.id, external_id=owner_id).one().role == "admin"


@pytest.mark.parametrize("marketplace_role,engine_role", [
    ("admin", "admin"), ("member", "member"), ("business", "member"), ("developer", "member"),
    ("authenticated", "member"),
])
def test_role_mapping(b2, marketplace_role, engine_role):
    env = b2()
    token = h(engine_token(str(uuid.uuid4()), str(uuid.uuid4()), marketplace_role))
    r = env.client.post("/files/upload", headers=token, data={"purpose": "knowledge_base"},
                        files={"file": ("kb.txt", KB_COFFEE_RU.encode(), "text/plain")})
    # Only the engine role "admin" may change the company knowledge base.
    assert r.status_code == (201 if engine_role == "admin" else 403)


def test_tenant_isolation_by_company_claim(b2):
    env = b2()
    user = str(uuid.uuid4())
    a = h(engine_token(user, "business-a", "admin"))
    b = h(engine_token(user, "business-b", "admin"))  # same Supabase user, other business
    fid = env.client.post("/files/upload", headers=a, data={"purpose": "knowledge_base"},
                          files={"file": ("kb.txt", KB_COFFEE_RU.encode(), "text/plain")}).json()["id"]
    run_id = env.client.post("/agents/marketing/run", headers=a, json={"message": "Пост"}).json()["run_id"]
    assert env.client.get(f"/files/{fid}", headers=b).status_code == 404
    assert env.client.get(f"/runs/{run_id}", headers=b).status_code == 404
    assert env.client.get("/runs", headers=b).json()["total"] == 0
    body = env.client.post("/agents/support/run", headers=b, json={"message": "Сколько стоит доставка?"}).json()
    assert body["status"] == "escalation_required" and body["sources"] == []


@pytest.mark.parametrize("make_token,code", [
    (lambda: engine_token("u", "c", ttl=-120), "token_expired"),
    (lambda: engine_token("u", "c", aud="other-service"), "invalid_credentials"),
    (lambda: engine_token("u", "c", iss="someone-else"), "invalid_credentials"),
    (lambda: engine_token("u", "c", secret="wrong-secret-wrong-secret-wrong-secret"), "invalid_credentials"),
    (lambda: jwt.encode({"sub": "u", "company_id": "c", "aud": AUD, "iss": ISS}, ENGINE_SECRET, algorithm="HS256"),
     "invalid_credentials"),  # no exp
    (lambda: engine_token("u", "", "admin"), "invalid_credentials"),  # no company
])
def test_invalid_engine_tokens(b2, make_token, code):
    env = b2()
    r = env.client.get("/agents", headers=h(make_token()))
    assert r.status_code == 401 and r.json()["error"]["code"] == code


def test_error_contract_for_backend2(b2):
    env = b2(daily_run_limit_per_user=1)
    t = h(engine_token(str(uuid.uuid4()), str(uuid.uuid4()), "member"))
    r = env.client.post("/agents/hr/run", headers=t, json={"message": "Сделай сводку резюме"})
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "resume_required" and err["details"]["run_id"]
    assert set(err) >= {"code", "message", "request_id"}
    r = env.client.post("/agents/marketing/run", headers=t, json={"message": "Пост", "company_id": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"  # identity never from body
    assert env.client.post("/agents/marketing/run", headers=t, json={"message": "Пост"}).status_code == 200
    r = env.client.post("/agents/marketing/run", headers=t, json={"message": "Пост"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "daily_limit_exceeded"
    assert int(r.headers["Retry-After"]) > 0


def test_health_is_public_for_monitoring(b2):
    env = b2()
    h_ = env.client.get("/health").json()
    assert h_["status"] == "ok" and h_["llm_provider"] == "mock" and set(h_["agents"]) >= {"marketing", "support", "hr"}


def test_language_codes(b2):
    """Kazakh is "kk" (ISO 639-1). "kz" is a country code and is rejected."""
    env = b2()
    t = h(engine_token(str(uuid.uuid4()), str(uuid.uuid4())))
    assert env.client.post("/agents/marketing/run", headers=t,
                           json={"message": "Пост", "language": "kk"}).json()["language"] == "kk"
    r = env.client.post("/agents/marketing/run", headers=t, json={"message": "Пост", "language": "kz"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
