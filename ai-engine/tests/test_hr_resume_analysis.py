"""HR Assistant: analysis of an uploaded DOCX resume (bug: template "Требования вакансии" with status success).

Root cause reproduced here: the demo stored the DOCX as a knowledge-base file (default upload type),
the HR assistant therefore received no resume, routed the request to "job_requirements" treating the
user's own message as the vacancy, and a small model (Ollama qwen3:1.7b) echoed the JSON template.
All resumes below are fictional.
"""

from __future__ import annotations

import json

import httpx

from tests.conftest import ScriptedLLM, json_reply, make_docx
from tests.test_llm_providers import completion, provider

RESUME = [
    "Тестов Тест Тестович",
    "Email: test.candidate@example.com",
    "О себе",
    "Backend-разработчик (вымышленное резюме для автотестов).",
    "Опыт работы",
    "2021 - 2024 Backend-разработчик, ТОО «Вымышленный Софт», Алматы",
    "- Разработка REST API на FastAPI",
    "- Оптимизация запросов SQL в PostgreSQL",
    "Образование",
    "2020, Вымышленный технический университет, бакалавр, информационные системы",
    "Навыки",
    "Python, FastAPI, SQL, Git",
]
USER_MESSAGE = ("Проанализируй загруженное резюме. Перечисли профессиональные навыки кандидата, "
                "опыт работы и образование. Не принимай решение о найме")
KNOWN_SKILLS = {"Python", "FastAPI", "SQL", "Git"}

# What qwen3:1.7b produced: the JSON skeleton copied from the prompt.
TEMPLATE_ECHO = json.dumps({
    "candidate_headline": "<role/specialisation as written, no personal data>",
    "summary": "<3-5 sentence neutral professional summary>",
    "experience": [{"title": "", "company": "", "start": "", "end": "", "highlights": [""]}],
    "education": [{"institution": "", "degree": "", "field": "", "year": ""}],
    "skills": [{"name": "", "evidence": "<verbatim quote from resume>"}],
    "languages": [""], "certifications": [""],
    "missing_information": ["<important info not stated in the resume>"],
})
GOOD_ANSWER = json_reply(
    candidate_headline="Backend-разработчик",
    summary="Backend-разработчик с опытом разработки REST API.",
    experience=[{"title": "Backend-разработчик", "company": "ТОО «Вымышленный Софт»", "start": "2021",
                 "end": "2024", "highlights": ["Разработка REST API на FastAPI"]}],
    education=[{"institution": "Вымышленный технический университет", "degree": "бакалавр",
                "field": "информационные системы", "year": "2020"}],
    skills=[{"name": "Python", "evidence": "Python, FastAPI, SQL, Git"},
            {"name": "FastAPI", "evidence": "Разработка REST API на FastAPI"},
            {"name": "SQL", "evidence": "Оптимизация запросов SQL в PostgreSQL"},
            {"name": "Git", "evidence": "Python, FastAPI, SQL, Git"}],
    languages=[], certifications=[], missing_information=["Уровень английского языка не указан"])


def _upload_resume(env, purpose: str = "resume") -> str:
    r = env.upload(env.a.admin, "resume_fictional.docx", make_docx(RESUME), purpose=purpose)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _skills(body) -> set[str]:
    return {s["name"] for s in body["data"]["result"]["skills"]}


def test_resume_analysis_extracts_known_skills_and_education(make_env):
    llm = ScriptedLLM([GOOD_ANSWER])
    env = make_env(llm=llm)
    fid = _upload_resume(env)
    r = env.run(env.a.admin, "hr", USER_MESSAGE, file_ids=[fid])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success" and body["data"]["task"] == "resume_summary"
    assert KNOWN_SKILLS <= _skills(body)
    assert "Вымышленный технический университет" in json.dumps(body["data"]["result"]["education"], ensure_ascii=False)
    assert body["data"]["result"]["experience"]
    # The extracted DOCX text really reached the model (redacted: no e-mail).
    sent = llm.all_text()
    assert "Разработка REST API на FastAPI" in sent and "Вымышленный технический университет" in sent
    assert "test.candidate@example.com" not in sent
    for heading in ("Подтверждённые навыки", "Опыт работы", "Образование"):
        assert heading in body["response"]
    assert "Требования вакансии" not in body["response"]


def test_small_model_template_echo_is_not_returned_as_analysis(make_env):
    """qwen3:1.7b-style output: placeholders are removed, facts come from the document itself."""
    env = make_env(llm=ScriptedLLM([TEMPLATE_ECHO]))
    fid = _upload_resume(env)
    body = env.run(env.a.admin, "hr", USER_MESSAGE, file_ids=[fid]).json()
    assert body["status"] == "success" and body["data"]["task"] == "resume_summary"
    assert "<" not in body["response"] and "Требования вакансии" not in body["response"]
    assert KNOWN_SKILLS <= _skills(body)  # each with a verbatim line from the resume as evidence
    assert all(s["evidence"] for s in body["data"]["result"]["skills"])
    assert "Вымышленный технический университет" in json.dumps(body["data"]["result"]["education"], ensure_ascii=False)
    assert "template_placeholders_removed" in body["warnings"]
    assert any(w.startswith("local_parser_used:") for w in body["warnings"])


def test_resume_not_received_is_a_clear_error_not_a_template(make_env):
    """The exact bug: DOCX uploaded with the demo's default type, HR received file_ids=[]."""
    llm = ScriptedLLM([TEMPLATE_ECHO])
    env = make_env(llm=llm)
    _upload_resume(env, purpose="knowledge_base")
    r = env.run(env.a.admin, "hr", USER_MESSAGE, file_ids=[])
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "resume_required" and "Резюме" in err["message"]
    assert llm.calls == 0
    run = env.client.get(f"/runs/{err['details']['run_id']}", headers=env.a.admin).json()
    assert run["status"] == "rejected" and run["error_code"] == "resume_required"


def test_resume_with_wrong_file_type_is_rejected(env):
    fid = _upload_resume(env, purpose="knowledge_base")
    r = env.run(env.a.admin, "hr", USER_MESSAGE, file_ids=[fid])
    assert r.status_code == 422 and r.json()["error"]["code"] == "file_purpose_not_accepted"


def test_empty_template_without_document_is_an_error(make_env):
    echo = json.dumps({"job_title": "", "required_skills": [""], "preferred_skills": [""],
                       "unclear_or_missing": ["<what the posting does not specify>"]})
    env = make_env(llm=ScriptedLLM([echo]))
    r = env.run(env.a.admin, "hr", "Выдели требования вакансии",
                options={"job_description": "Требования\n- Python\nУсловия\n- Алматы"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_invalid_output"
    run = env.client.get(f"/runs/{r.json()['error']['details']['run_id']}", headers=env.a.admin).json()
    assert run["status"] == "error"


def test_short_request_is_not_treated_as_a_job_posting(make_env):
    llm = ScriptedLLM()
    env = make_env(llm=llm)
    r = env.run(env.a.admin, "hr", "Выдели требования вакансии")
    assert r.status_code == 422 and r.json()["error"]["code"] == "job_description_required"
    r = env.run(env.a.admin, "hr", "Привет")
    assert r.status_code == 422 and r.json()["error"]["code"] == "hr_task_unclear"
    assert llm.calls == 0
    posting = ("Аналитик данных\nТребования\n- SQL\n- Python\nОбязанности\n- Отчёты для отдела продаж\n"
               "Условия\n- Алматы, гибридный график")
    llm.replies.append(json_reply(job_title="Аналитик данных", required_skills=["SQL", "Python"]))
    r = env.run(env.a.admin, "hr", posting)
    assert r.status_code == 200 and r.json()["data"]["task"] == "job_requirements"


def test_no_invented_skills_and_no_hiring_decision(make_env):
    invented = json_reply(
        candidate_headline="Backend-разработчик",
        summary="Сильный разработчик. Рекомендую нанять этого кандидата.",
        experience=[], education=[],
        skills=[{"name": "Java", "evidence": "10 лет опыта Java"}, {"name": "Kubernetes", "evidence": "Kubernetes"},
                {"name": "Python", "evidence": "Python, FastAPI, SQL, Git"}],
        languages=[], certifications=[], missing_information=[], score=9, recommendation="hire")
    env = make_env(llm=ScriptedLLM([invented]))
    fid = _upload_resume(env)
    body = env.run(env.a.admin, "hr", USER_MESSAGE, file_ids=[fid]).json()
    skills = _skills(body)
    assert "Java" not in skills and "Kubernetes" not in skills and KNOWN_SKILLS <= skills
    assert {"Java", "Kubernetes"} <= set(body["data"]["unverified_items"])
    assert "нанять" not in body["response"].lower()
    assert "score" not in body["data"]["result"] and "recommendation" not in body["data"]["result"]
    assert "decision_or_score_content_removed" in body["warnings"]
    assert body["data"]["decision_support_only"] is True


def test_resume_analysis_with_ollama_qwen3_output_format(make_env, tmp_path):
    """Ollama qwen3: <think> block + fenced JSON, via the real OpenAI SDK on a mocked endpoint."""
    raw = "<think>Нужно перечислить навыки.</think>\n```json\n" + GOOD_ANSWER + "\n```"
    p, rec = provider(tmp_path, "ollama", lambda r, n: httpx.Response(200, json=completion(raw)),
                      ollama_model="qwen3:1.7b")
    env = make_env(llm=p)
    fid = _upload_resume(env)
    body = env.run(env.a.admin, "hr", USER_MESSAGE, file_ids=[fid]).json()
    assert body["status"] == "success" and body["mode"] == "ollama" and body["model"]
    assert KNOWN_SKILLS <= _skills(body)
    sent = rec.body()
    assert sent["model"] == "qwen3:1.7b" and sent["response_format"] == {"type": "json_object"}
    assert "Разработка REST API на FastAPI" in json.dumps(sent["messages"], ensure_ascii=False)


def test_resume_analysis_in_mock_mode(env):
    fid = _upload_resume(env)
    body = env.run(env.a.admin, "hr", USER_MESSAGE, file_ids=[fid]).json()
    assert body["status"] == "success" and body["mode"] == "mock"
    assert KNOWN_SKILLS <= _skills(body)
    assert "Вымышленный технический университет" in body["response"]


def test_hr_request_with_no_hiring_decision_phrase_is_not_blocked(env):
    fid = _upload_resume(env)
    assert env.run(env.a.admin, "hr", USER_MESSAGE, file_ids=[fid]).json()["status"] != "blocked"
