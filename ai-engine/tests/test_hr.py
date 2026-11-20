"""HR assistant: fictional resumes, grounding, privacy and fairness restrictions."""

from __future__ import annotations

from tests.conftest import FICTIONAL_RESUME_EN_PDF, FICTIONAL_RESUME_RU, ScriptedLLM, json_reply, make_docx, make_pdf

JOB_TEXT = """Аналитик данных
Требования
- Опыт работы с SQL
- Python для анализа данных
- Опыт с Tableau
Будет плюсом
- Знание английского языка
Условия
- Офис в Алматы, гибридный график
"""


def _upload_resume(env, headers=None, data=None, name="resume_fictional.docx"):
    r = env.upload(headers or env.a.member, name, data or make_docx(FICTIONAL_RESUME_RU), purpose="resume")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_hr_resume_summary_fictional_docx(make_env):
    llm = ScriptedLLM([json_reply(
        candidate_headline="Аналитик данных", summary="Аналитик данных в рознице.",
        experience=[{"title": "Аналитик данных", "company": "ТОО «Вымышленная Розница»", "start": "2021-03",
                     "end": None, "highlights": ["Дашборды продаж в Power BI"]}],
        education=[{"institution": "Вымышленный университет", "degree": "бакалавр",
                    "field": "информационные системы", "year": "2016"}],
        skills=[{"name": "Python", "evidence": "Python, SQL, Power BI, Excel"},
                {"name": "SQL", "evidence": "Автоматизировала отчётность на Python и SQL"},
                {"name": "Kubernetes", "evidence": "Развёртывание в Kubernetes"}],  # not in resume
        languages=["Казахский", "русский", "английский (B2)"], certifications=[],
        missing_information=["Сертификаты не указаны"])])
    env = make_env(llm=llm)
    fid = _upload_resume(env)
    r = env.run(env.a.member, "hr", "Сделай сводку резюме", file_ids=[fid], language="ru")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success" and body["data"]["task"] == "resume_summary"
    skills = [s["name"] for s in body["data"]["result"]["skills"]]
    assert skills == ["Python", "SQL"]  # hallucinated skill removed from "confirmed"
    assert body["data"]["unverified_items"] == ["Kubernetes"]
    assert "Подтверждённые навыки" in body["response"] and "Отсутствующая" in body["response"]
    assert "Решение о кандидате принимает человек" in body["response"]
    # Privacy: contact details, birth date and name never reached the LLM.
    sent = llm.all_text()
    for secret in ("aigerim.test@example.com", "+7 (701) 000-00-00", "01.01.1995", "Тестова Айгерим"):
        assert secret not in sent
    assert "03.2021" in sent  # employment dates are preserved
    assert body["data"]["redactions"]["email"] == 1 and body["data"]["redactions"]["protected_attributes"] == 1


def test_hr_resume_pdf_mock_mode(env):
    r = env.upload(env.a.member, "resume_fictional.pdf", make_pdf(FICTIONAL_RESUME_EN_PDF), purpose="resume")
    assert r.status_code == 201, r.text
    body = env.run(env.a.member, "hr", "Summarise this resume", file_ids=[r.json()["id"]]).json()
    assert body["mode"] == "mock" and body["language"] == "en"
    assert body["response"].startswith("⚠️ MOCK MODE")
    names = {s["name"] for s in body["data"]["result"]["skills"]}
    assert {"Python", "FastAPI", "PostgreSQL", "Docker", "Git"} <= names


def test_hr_requirements_check_shows_confirmed_and_missing(make_env):
    llm = ScriptedLLM([json_reply(
        job_title="Аналитик данных",
        requirements=[
            {"requirement": "SQL", "type": "required", "status": "confirmed", "evidence": "Python, SQL, Power BI, Excel"},
            {"requirement": "Tableau", "type": "required", "status": "confirmed", "evidence": "Tableau dashboards"},
            {"requirement": "Английский язык", "type": "preferred", "status": "not_found", "evidence": None}],
        missing_information=["Опыт с Tableau"], clarifying_questions=["Работали ли вы с Tableau?"],
        match_score=87, recommendation="Рекомендую нанять")])
    env = make_env(llm=llm)
    fid = _upload_resume(env)
    body = env.run(env.a.member, "hr", "Сопоставь требования вакансии с резюме", file_ids=[fid],
                   options={"job_description": JOB_TEXT}).json()
    assert body["data"]["task"] == "requirements_check"
    reqs = {r["requirement"]: r["status"] for r in body["data"]["result"]["requirements"]}
    assert reqs == {"SQL": "confirmed", "Tableau": "not_found", "Английский язык": "not_found"}
    assert "match_score" not in body["data"]["result"] and "recommendation" not in body["data"]["result"]
    assert "decision_or_score_content_removed" in body["warnings"]
    assert "подтверждено в резюме" in body["response"]


def test_hr_interview_questions_and_job_description(make_env):
    llm = ScriptedLLM([
        json_reply(job_title="Python-разработчик", questions=[
            {"question": "Расскажите о проекте на FastAPI", "category": "technical",
             "purpose": "опыт с API", "good_answer_signals": ["конкретные решения"]}]),
        json_reply(job_title="Бариста", about_company="[о компании]", responsibilities=["Готовить кофе"],
                   requirements=["Опыт от 1 года"], nice_to_have=[], conditions=["[зарплата, ₸]"],
                   how_to_apply="[контакт]", inclusion_note="Мы рассматриваем всех кандидатов."),
    ])
    env = make_env(llm=llm)
    q = env.run(env.a.member, "hr", "Подготовь вопросы для собеседования Python-разработчика").json()
    assert q["data"]["task"] == "interview_questions" and "FastAPI" in q["response"]
    jd = env.run(env.a.member, "hr", "Составь вакансию бариста для кофейни в Астане").json()
    assert jd["data"]["task"] == "job_description" and "Бариста" in jd["response"]


def test_hr_refuses_ranking_decisions_and_protected_attributes(make_env):
    llm = ScriptedLLM()
    env = make_env(llm=llm)
    fid = _upload_resume(env)
    prohibited = [
        "Ранжируй кандидатов по уровню",
        "Стоит ли нанимать этого кандидата?",
        "Отклони кандидата, если он не подходит",
        "Should we hire this candidate? Give a score from 1 to 10",
        "Оцени кандидата с учётом возраста и семейного положения",
        "Составь вакансию только для мужчин до 30 лет",
        "Үміткерді бағала",
    ]
    for msg in prohibited:
        body = env.run(env.a.member, "hr", msg, file_ids=[fid]).json()
        assert body["status"] == "blocked", msg
        assert "не может принимать решения" in body["response"] or "cannot make" in body["response"] \
            or "шешім" in body["response"], msg
    assert llm.calls == 0


def test_hr_allows_inclusive_wording(make_env):
    llm = ScriptedLLM([json_reply(job_title="Кассир", responsibilities=[], requirements=[], nice_to_have=[],
                                  conditions=[], how_to_apply="", inclusion_note="")])
    env = make_env(llm=llm)
    body = env.run(env.a.member, "hr", "Составь вакансию кассира без указания возраста и пола").json()
    assert body["status"] == "success" and llm.calls == 1


def test_hr_one_resume_per_run(env):
    f1 = _upload_resume(env)
    f2 = _upload_resume(env, name="resume2.docx")
    r = env.run(env.a.member, "hr", "Сделай сводку", file_ids=[f1, f2])
    assert r.status_code == 422 and r.json()["error"]["code"] == "one_resume_per_run"


def test_hr_strips_decision_sentences_from_output(make_env):
    llm = ScriptedLLM([json_reply(candidate_headline="Аналитик", summary="Опытный аналитик. Рекомендую нанять этого кандидата.",
                                  experience=[], education=[], skills=[], languages=[], certifications=[],
                                  missing_information=[], age=29)])
    env = make_env(llm=llm)
    fid = _upload_resume(env)
    body = env.run(env.a.member, "hr", "Сводка резюме", file_ids=[fid], options={"task": "resume_summary"}).json()
    assert "нанять" not in body["response"]
    assert "age" not in body["data"]["result"]
    assert "decision_or_score_content_removed" in body["warnings"]


def test_hr_rejects_wrong_file_purpose(env):
    kb = env.upload(env.a.admin, "kb.txt", "Текст базы знаний компании для проверки.".encode()).json()
    r = env.run(env.a.admin, "hr", "Сводка резюме", file_ids=[kb["id"]])
    assert r.status_code == 422 and r.json()["error"]["code"] == "file_purpose_not_accepted"
