"""Marketing agent: Russian and Kazakh runs, task detection, draft-only behaviour."""

from __future__ import annotations

from app.agents.marketing import detect_platforms, detect_task
from tests.conftest import ScriptedLLM

RU_DRAFT = """## Контент-план для кофейни в Алматы (2 недели)
| День | Платформа | Формат | Тема |
|---|---|---|---|
| Пн | Instagram | Reels | Как мы готовим флэт уайт |
Цена: [укажите цену, ₸]"""

KK_DRAFT = """## Алматыдағы кофеханаға арналған контент-жоспар
| Күн | Платформа | Формат | Тақырып |
|---|---|---|---|
| Дс | Instagram | Reels | Таңғы кофе дайындау |"""


def test_marketing_ru(make_env):
    llm = ScriptedLLM([RU_DRAFT])
    env = make_env(llm=llm)
    r = env.run(env.a.member, "marketing", "Создай контент-план для кофейни в Алматы", language="ru")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "marketing" and body["status"] == "success" and body["language"] == "ru"
    assert body["mode"] == "openai" and body["model"] == "scripted-test-model"
    assert "Контент-план" in body["response"]
    assert "черновик" in body["response"].lower()  # draft disclaimer appended
    assert body["data"] == {"task": "content_plan", "platforms": [], "draft": True, "published": False,
                            "contains_numbers_to_verify": False}
    # Usage and configured-price cost: 1200 in * 0.4/1M + 300 out * 1.6/1M
    assert body["usage"]["input_tokens"] == 1200 and body["usage"]["output_tokens"] == 300
    assert abs(body["usage"]["estimated_cost"] - 0.00096) < 1e-9
    system = llm.requests[0].messages[0]["content"]
    assert "ЯЗЫК ОТВЕТА: русский" in system
    assert "TASK: content plan" in system
    assert "Do NOT invent statistics" in system and "DRAFTS only" in system


def test_marketing_kk_autodetect(make_env):
    llm = ScriptedLLM([KK_DRAFT])
    env = make_env(llm=llm)
    r = env.run(env.a.member, "marketing", "Алматыдағы кофеханаға арналған контент-жоспар жасап беріңіз")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "kk"
    assert "контент-жоспар" in body["response"]
    assert "Бұл жоба" in body["response"]  # Kazakh draft disclaimer
    system = llm.requests[0].messages[0]["content"]
    assert "ЖАУАП ТІЛІ: қазақ тілі" in system
    assert body["data"]["task"] == "content_plan"


def test_marketing_options_and_brief_are_untrusted_data(make_env):
    llm = ScriptedLLM(["Instagram post draft. Growth 300% guaranteed"])
    env = make_env(llm=llm)
    r = env.run(env.a.member, "marketing", "Напиши пост", language="ru",
                options={"task": "social_posts", "platforms": ["instagram", "telegram"],
                         "business_name": "Вымышленный Кофе", "budget_kzt": 150000, "city": "Алматы"})
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["data"]["platforms"] == ["instagram", "telegram"]
    assert "contains_numbers_verify_before_use" in body["warnings"]  # flags numeric claims
    user_msg = llm.requests[0].messages[-1]["content"]
    assert "<untrusted_documents>" in user_msg and "Budget (KZT): 150 000" in user_msg


def test_marketing_invalid_options_rejected(env):
    r = env.run(env.a.member, "marketing", "Пост", options={"task": "publish_now"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_marketing_mock_mode_is_labelled(env):
    r = env.run(env.a.member, "marketing", "Write an email campaign for a bakery in Astana")
    body = r.json()
    assert body["mode"] == "mock" and body["language"] == "en"
    assert body["response"].startswith("⚠️ MOCK MODE")
    assert body["data"]["task"] == "email_campaign"
    assert body["usage"]["estimated_cost"] == 0.0


def test_task_and_platform_detection():
    assert detect_task("Сделай коммерческое предложение для ресторана") == "commercial_offer"
    assert detect_task("Проанализируй целевую аудиторию салона красоты") == "audience_analysis"
    assert detect_task("Рекламный текст для баннера") == "ad_copy"
    assert detect_task("Как продвинуть магазин в Шымкенте?") == "smb_recommendations"
    assert detect_task("Маркетинговая стратегия на квартал") == "marketing_strategy"
    assert detect_task("Пост для TikTok и Telegram") == "social_posts"
    assert detect_platforms("Пост для TikTok и Telegram") == ["tiktok", "telegram"]
