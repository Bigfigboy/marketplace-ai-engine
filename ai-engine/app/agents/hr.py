"""AI HR Assistant (slug: hr).

Assists a recruiter: resume summaries, job-requirement extraction, a factual map of which
requirements the resume explicitly confirms, interview questions and job-description drafts.

Hard limits enforced in code (not only in the prompt):
* refuses hiring/rejection decisions, ranking and scoring, and evaluation by protected
  characteristics (``check_hr_request``) before calling the LLM;
* one resume per run, so candidates cannot be compared or ranked;
* contact details and protected characteristics are redacted before text leaves the server;
* model output is stripped of score/rank/decision fields and sentences;
* a skill or requirement is "confirmed" only if its evidence is found in the resume text.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.core.errors import AppError
from app.core.i18n import t
from app.prompts.hr import build_system_prompt
from app.security.prompt_guard import sanitize_document_text, wrap_untrusted
from app.services.hr_tools import (
    check_hr_request,
    evidence_in,
    find_skills,
    looks_like_resume,
    redact_resume,
    split_sections,
    strip_decisions,
    verify_skill,
)
from app.services.jsonutil import as_str, as_str_list, parse_json_object

HRTask = Literal["auto", "resume_summary", "job_requirements", "requirements_check", "interview_questions",
                 "job_description"]


class HROptions(BaseModel):
    task: HRTask = Field(default="auto", description="HR task; 'auto' detects it from the message and files")
    job_title: str | None = Field(default=None, max_length=200)
    job_description: str | None = Field(default=None, max_length=20_000,
                                        description="Vacancy text (alternative to a job_description file)")
    question_count: int = Field(default=8, ge=3, le=20)


class HROutput(BaseModel):
    task: str
    result: dict[str, Any]
    redactions: dict[str, int]
    unverified_items: list[str]
    decision_support_only: bool = True


_INTERVIEW_KW = ("вопрос", "собеседован", "интервью", "interview", "сұхбат", "сұрақ")
_JD_DRAFT_KW = ("составь вакансию", "напиши вакансию", "создай вакансию", "подготовь вакансию",
                "черновик вакансии", "текст вакансии", "описание вакансии для", "job description for",
                "draft a job", "write a job", "create a job", "job posting", "вакансия жаз", "вакансия жоба",
                "вакансия мәтін", "вакансияның сипаттамасын жаз", "вакансия дайында")
_REQ_KW = ("требовани", "requirements", "талаптар", "сопостав", "соответств", "compare", "match", "салыстыр")

_L = {
    "ru": {"summary": "Сводка резюме", "experience": "Опыт работы", "education": "Образование",
           "skills": "Подтверждённые навыки", "languages": "Языки", "certs": "Сертификаты и курсы",
           "missing": "Отсутствующая / неуказанная информация", "requirements": "Требования вакансии",
           "required": "Обязательные навыки", "preferred": "Желательные навыки", "exp_req": "Требования к опыту",
           "edu_req": "Требования к образованию", "resp": "Обязанности", "cond": "Условия",
           "unclear": "Не указано в вакансии", "check": "Требования и подтверждения в резюме",
           "confirmed": "подтверждено в резюме", "not_found": "в резюме не найдено (уточнить)",
           "questions": "Вопросы для собеседования", "clarify": "Уточняющие вопросы кандидату",
           "jd": "Черновик вакансии", "about": "О компании", "reqs": "Требования", "nice": "Будет плюсом",
           "apply": "Как откликнуться", "unverified": "Не подтверждено текстом резюме (проверьте вручную)",
           "purpose": "цель", "signals": "на что обратить внимание"},
    "kk": {"summary": "Түйіндеменің қысқаша мазмұны", "experience": "Жұмыс тәжірибесі", "education": "Білімі",
           "skills": "Расталған дағдылар", "languages": "Тілдер", "certs": "Сертификаттар мен курстар",
           "missing": "Жоқ / көрсетілмеген ақпарат", "requirements": "Вакансия талаптары",
           "required": "Міндетті дағдылар", "preferred": "Қалаулы дағдылар", "exp_req": "Тәжірибеге қойылатын талаптар",
           "edu_req": "Білімге қойылатын талаптар", "resp": "Міндеттер", "cond": "Жағдайлар",
           "unclear": "Вакансияда көрсетілмеген", "check": "Талаптар және түйіндемедегі растаулар",
           "confirmed": "түйіндемеде расталған", "not_found": "түйіндемеде табылмады (нақтылау керек)",
           "questions": "Сұхбатқа арналған сұрақтар", "clarify": "Үміткерге нақтылау сұрақтары",
           "jd": "Вакансия жобасы", "about": "Компания туралы", "reqs": "Талаптар", "nice": "Артықшылық болады",
           "apply": "Қалай өтініш беруге болады", "unverified": "Түйіндеме мәтінімен расталмаған (қолмен тексеріңіз)",
           "purpose": "мақсаты", "signals": "назар аударатын белгілер"},
    "en": {"summary": "Resume summary", "experience": "Work experience", "education": "Education",
           "skills": "Confirmed skills", "languages": "Languages", "certs": "Certifications and courses",
           "missing": "Missing / not stated information", "requirements": "Job requirements",
           "required": "Required skills", "preferred": "Preferred skills", "exp_req": "Experience requirements",
           "edu_req": "Education requirements", "resp": "Responsibilities", "cond": "Conditions",
           "unclear": "Not specified in the posting", "check": "Requirements and resume evidence",
           "confirmed": "confirmed in resume", "not_found": "not found in resume (clarify)",
           "questions": "Interview questions", "clarify": "Clarifying questions for the candidate",
           "jd": "Job description draft", "about": "About the company", "reqs": "Requirements",
           "nice": "Nice to have", "apply": "How to apply",
           "unverified": "Not confirmed by the resume text (check manually)",
           "purpose": "purpose", "signals": "what to listen for"},
}


def _bullets(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items if i]


class HRAgent(BaseAgent):
    id = "agent-hr-001"
    slug = "hr"
    name = "AI HR Assistant"
    description = ("Recruiter assistant: structured resume summaries (PDF/DOCX), job-requirement extraction, "
                   "confirmed skills vs. missing information, interview questions and job-description drafts. "
                   "Never makes hiring decisions, ranks or scores candidates.")
    version = "1.0.0"
    category = "hr"
    capabilities = ("resume_summary", "experience_extraction", "skills_and_education_extraction",
                    "job_requirements", "requirements_check", "interview_questions", "job_description")
    accepted_file_purposes = ("resume", "job_description")
    max_files = 2
    options_model = HROptions
    output_model = HROutput

    def precheck(self, message: str, options: BaseModel, ctx: AgentContext) -> AgentResult | None:
        # Everything the client controls is checked: message, every history turn and job fields.
        texts = [message, *(h.content for h in ctx.history)]
        if isinstance(options, HROptions):
            texts += [options.job_title or "", options.job_description or ""]
        violation = next((v for v in map(check_hr_request, texts) if v), None)
        if violation is None:
            return None
        return AgentResult(response=t("hr_policy_block", ctx.language), status="blocked",
                           data={"task": "policy_refusal", "violation": violation, "result": {},
                                 "redactions": {}, "unverified_items": [], "decision_support_only": True},
                           warnings=[violation])

    def run(self, message: str, options: BaseModel, ctx: AgentContext) -> AgentResult:
        assert isinstance(options, HROptions)
        resumes = [f for f in ctx.files if f.purpose == "resume"]
        job_files = [f for f in ctx.files if f.purpose == "job_description"]
        if len(resumes) > 1:
            raise AppError("Only one resume per request is allowed: the assistant does not compare or rank "
                           "candidates.", code="one_resume_per_run", status_code=422)
        if len(job_files) > 1:
            raise AppError("Only one job description file per request is allowed.",
                           code="too_many_job_descriptions", status_code=422)
        if any(looks_like_resume(f.text) for f in job_files):
            # A resume uploaded as "job_description" would bypass redaction and the one-resume rule.
            raise AppError("The job_description file looks like a resume. Upload resumes with purpose=resume.",
                           code="job_description_looks_like_resume", status_code=422)
        job_text = options.job_description or "\n\n".join(f.text for f in job_files) or ""
        if job_text:
            # Defence in depth: no contact details or personal identifiers leave the server.
            job_text, _ = redact_resume(job_text, mask_name_line=False)
        task = options.task if options.task != "auto" else self._detect_task(message, bool(resumes), bool(job_text))

        if task == "job_requirements" and not job_text:
            job_text = message
        if task in ("resume_summary", "requirements_check") and not resumes:
            raise AppError("This task needs a resume: upload it with purpose=resume and pass its id in file_ids.",
                           code="resume_required", status_code=422)
        if task == "requirements_check" and not job_text:
            raise AppError("This task needs a job description (options.job_description or a file with "
                           "purpose=job_description).", code="job_description_required", status_code=422)

        resume_raw = resumes[0].text if resumes else ""
        resume_text, redactions = redact_resume(resume_raw) if resume_raw else ("", {})
        budget = ctx.settings.max_input_tokens // 3
        blocks: list[tuple[str, str]] = []
        if resume_text:
            text, trimmed = ctx.fit_text(sanitize_document_text(resume_text).text, budget)
            blocks.append(("resume", text))
            if trimmed:
                ctx.warnings.append("resume_truncated")
        if job_text and task != "job_description":
            text, _ = ctx.fit_text(sanitize_document_text(job_text).text, budget)
            blocks.append(("job_description", text))
        elif job_text:
            blocks.append(("job_notes", ctx.fit_text(sanitize_document_text(job_text).text, budget)[0]))

        extra = []
        if options.job_title:
            extra.append(f"Job title: {options.job_title}")
        if task == "interview_questions":
            extra.append(f"Number of questions: {options.question_count}")
        request = "\n".join([*extra, f"REQUEST:\n{message}"])
        user_content = f"{wrap_untrusted(blocks)}\n\n{request}" if blocks else request
        messages = [{"role": "system", "content": build_system_prompt(ctx.language, task)},
                    *ctx.history_messages(), {"role": "user", "content": user_content}]

        result = ctx.call_llm(messages, json_mode=True, mock_fn=lambda: json.dumps(
            self._mock(task, resume_text, job_text, options, message), ensure_ascii=False))
        parsed = parse_json_object(result.text)
        warnings: list[str] = []
        if parsed is None:
            raise AppError("The AI provider returned an invalid structured response. Please retry.",
                           code="llm_invalid_output", status_code=502)

        removed: list[str] = []
        parsed = strip_decisions(parsed, removed)
        if removed:
            warnings.append("decision_or_score_content_removed")
        unverified = self._verify(task, parsed, f"{resume_raw}\n{resume_text}" if resume_raw else "")
        if unverified:
            warnings.append("unverified_items_moved")

        response = self._render(task, parsed, unverified, ctx.language)
        if result.mode == "mock":
            response = f"{t('mock_banner', ctx.language)}\n\n{response}"
        response += f"\n\n---\n_{t('hr_disclaimer', ctx.language)}_"
        data = HROutput(task=task, result=parsed, redactions=redactions, unverified_items=unverified).model_dump()
        return AgentResult(response=response, data=data, warnings=warnings)

    # ── task detection ───────────────────────────────────────────────────────
    @staticmethod
    def _detect_task(message: str, has_resume: bool, has_job: bool) -> str:
        m = message.lower()
        if any(k in m for k in _INTERVIEW_KW):
            return "interview_questions"
        if any(k in m for k in _JD_DRAFT_KW):
            return "job_description"
        if has_resume and has_job:
            return "requirements_check"
        if has_resume:
            return "resume_summary"
        if has_job or any(k in m for k in _REQ_KW):
            return "job_requirements"
        return "job_description" if "ваканс" in m or "vacanc" in m or "job" in m else "job_requirements"

    # ── grounding checks ─────────────────────────────────────────────────────
    @staticmethod
    def _verify(task: str, parsed: dict[str, Any], resume_raw: str) -> list[str]:
        """Move skills/requirements whose evidence is not in the resume out of 'confirmed'."""
        unverified: list[str] = []
        if not resume_raw:
            return unverified
        if task == "resume_summary" and isinstance(parsed.get("skills"), list):
            kept = []
            for skill in parsed["skills"]:
                name = as_str(skill.get("name") if isinstance(skill, dict) else skill, 200)
                evidence = as_str(skill.get("evidence"), 500) if isinstance(skill, dict) else ""
                proof = verify_skill(resume_raw, name, evidence) if name else None
                if proof:
                    kept.append({"name": name, "evidence": proof})
                elif name:
                    unverified.append(name)
            parsed["skills"] = kept
        if task == "requirements_check" and isinstance(parsed.get("requirements"), list):
            for req in parsed["requirements"]:
                if not isinstance(req, dict):
                    continue
                if req.get("status") not in ("confirmed", "not_found"):
                    req["status"] = "not_found"
                if req["status"] == "confirmed" and not evidence_in(resume_raw, as_str(req.get("evidence"), 500)):
                    req["status"] = "not_found"
                    req["evidence"] = None
                    unverified.append(as_str(req.get("requirement"), 200))
        return unverified

    # ── rendering ────────────────────────────────────────────────────────────
    def _render(self, task: str, d: dict[str, Any], unverified: list[str], lang: str) -> str:
        L = _L[lang]
        out: list[str] = []
        if task == "resume_summary":
            out += [f"## {L['summary']}"]
            if d.get("candidate_headline"):
                out.append(f"**{as_str(d['candidate_headline'], 300)}**")
            out += [as_str(d.get("summary")), "", f"### {L['experience']}"]
            for e in d.get("experience") or []:
                if isinstance(e, dict):
                    period = " – ".join(x for x in (as_str(e.get("start"), 20), as_str(e.get("end"), 20)) if x)
                    head = ", ".join(x for x in (as_str(e.get("title"), 200), as_str(e.get("company"), 200)) if x)
                    out.append(f"- **{head}**" + (f" ({period})" if period else ""))
                    out += [f"  - {h}" for h in as_str_list(e.get("highlights"), 10)]
            out += ["", f"### {L['education']}"]
            for e in d.get("education") or []:
                if isinstance(e, dict):
                    parts = [as_str(e.get(k), 200) for k in ("institution", "degree", "field", "year")]
                    out.append("- " + ", ".join(p for p in parts if p))
            out += ["", f"### {L['skills']}"]
            out += [f"- {s['name']} — «{s['evidence']}»" if s.get("evidence") else f"- {s['name']}"
                    for s in d.get("skills") or [] if isinstance(s, dict)]
            for key, label in (("languages", "languages"), ("certifications", "certs")):
                items = as_str_list(d.get(key), 20)
                if items:
                    out += ["", f"### {L[label]}", *_bullets(items)]
            out += ["", f"### {L['missing']}", *_bullets(as_str_list(d.get("missing_information"), 20))]
        elif task == "job_requirements":
            out.append(f"## {L['requirements']}" + (f": {as_str(d.get('job_title'), 200)}" if d.get("job_title") else ""))
            for key, label in (("required_skills", "required"), ("preferred_skills", "preferred"),
                               ("experience_requirements", "exp_req"), ("education_requirements", "edu_req"),
                               ("responsibilities", "resp"), ("conditions", "cond"), ("unclear_or_missing", "unclear")):
                items = as_str_list(d.get(key), 30)
                if items:
                    out += ["", f"### {L[label]}", *_bullets(items)]
        elif task == "requirements_check":
            out.append(f"## {L['check']}" + (f": {as_str(d.get('job_title'), 200)}" if d.get("job_title") else ""))
            out += ["", "| # | Requirement | Type | Status | Evidence |", "|---|---|---|---|---|"]
            for i, r in enumerate([r for r in d.get("requirements") or [] if isinstance(r, dict)], start=1):
                status = L["confirmed"] if r.get("status") == "confirmed" else L["not_found"]
                ev = as_str(r.get("evidence"), 200).replace("|", "/") or "—"
                out.append(f"| {i} | {as_str(r.get('requirement'), 200).replace('|', '/')} | "
                           f"{as_str(r.get('type'), 20)} | {status} | {ev} |")
            for key, label in (("missing_information", "missing"), ("clarifying_questions", "clarify")):
                items = as_str_list(d.get(key), 20)
                if items:
                    out += ["", f"### {L[label]}", *_bullets(items)]
        elif task == "interview_questions":
            out.append(f"## {L['questions']}" + (f": {as_str(d.get('job_title'), 200)}" if d.get("job_title") else ""))
            for i, q in enumerate([q for q in d.get("questions") or [] if isinstance(q, dict)], start=1):
                out.append(f"{i}. **{as_str(q.get('question'), 500)}** _({as_str(q.get('category'), 30)})_")
                if q.get("purpose"):
                    out.append(f"   - {L['purpose']}: {as_str(q.get('purpose'), 300)}")
                signals = as_str_list(q.get("good_answer_signals"), 5)
                if signals:
                    out.append(f"   - {L['signals']}: " + "; ".join(signals))
        else:  # job_description
            out.append(f"## {L['jd']}: {as_str(d.get('job_title'), 200)}")
            if d.get("about_company"):
                out += ["", f"### {L['about']}", as_str(d.get("about_company"))]
            for key, label in (("responsibilities", "resp"), ("requirements", "reqs"), ("nice_to_have", "nice"),
                               ("conditions", "cond")):
                items = as_str_list(d.get(key), 30)
                if items:
                    out += ["", f"### {L[label]}", *_bullets(items)]
            if d.get("how_to_apply"):
                out += ["", f"### {L['apply']}", as_str(d.get("how_to_apply"))]
            if d.get("inclusion_note"):
                out += ["", f"_{as_str(d.get('inclusion_note'), 500)}_"]
        if unverified:
            out += ["", f"### {L['unverified']}", *_bullets(unverified)]
        return "\n".join(out).strip()

    # ── offline (mock) mode: deterministic local extraction, no AI ───────────
    @staticmethod
    def _mock(task: str, resume: str, job: str, options: HROptions, message: str) -> dict[str, Any]:
        rs = split_sections(resume) if resume else {}
        js = split_sections(job) if job else {}
        skills = find_skills(resume) if resume else []
        title = options.job_title or (js.get("_header") or [""])[0][:100]
        if task == "resume_summary":
            missing = [name for name in ("experience", "education", "skills", "languages") if not rs.get(name)]
            roles: list[dict[str, Any]] = []
            for line in rs.get("experience", [])[:30]:
                if re.search(r"\b(19|20)\d{2}\b", line) or not roles:
                    roles.append({"title": line, "company": "", "start": "", "end": "", "highlights": []})
                else:
                    roles[-1]["highlights"].append(line)
            headline = next((ln for ln in rs.get("_header", []) if "[redacted" not in ln), "")
            return {"candidate_headline": headline[:100],
                    "summary": " ".join(rs.get("summary", [])[:3]),
                    "experience": roles[:10],
                    "education": [{"institution": line, "degree": "", "field": "", "year": ""}
                                  for line in rs.get("education", [])[:5]],
                    "skills": skills, "languages": rs.get("languages", [])[:10],
                    "certifications": rs.get("certifications", [])[:10],
                    "missing_information": [f"section not found: {m}" for m in missing]}
        if task == "job_requirements":
            return {"job_title": title, "required_skills": [s["name"] for s in find_skills("\n".join(js.get("requirements", [])))],
                    "preferred_skills": [s["name"] for s in find_skills("\n".join(js.get("preferred", [])))],
                    "experience_requirements": [r for r in js.get("requirements", []) if re.search(r"\d", r)],
                    "education_requirements": [], "responsibilities": js.get("responsibilities", [])[:15],
                    "conditions": js.get("conditions", [])[:10],
                    "unclear_or_missing": [] if js.get("conditions") else ["conditions / salary"]}
        if task == "requirements_check":
            reqs = [(r, "required") for r in js.get("requirements", [])] + [(r, "preferred") for r in js.get("preferred", [])]
            if not reqs:
                reqs = [(s["name"], "required") for s in find_skills(job)]
            rows = []
            for req, kind in reqs[:30]:
                req_skills = find_skills(req)
                hit = next((s for s in skills if any(s["name"].lower() == r["name"].lower() for r in req_skills)), None)
                rows.append({"requirement": req, "type": kind, "status": "confirmed" if hit else "not_found",
                             "evidence": hit["evidence"] if hit else None})
            return {"job_title": title, "requirements": rows,
                    "missing_information": [r["requirement"] for r in rows if r["status"] == "not_found"][:10],
                    "clarifying_questions": [f"{r['requirement']}?" for r in rows if r["status"] == "not_found"][:5]}
        if task == "interview_questions":
            topics = [s["name"] for s in (find_skills(job) or skills)] or [options.job_title or "the role"]
            qs = [{"question": f"Describe a recent project where you used {topic}.", "category": "experience",
                   "purpose": topic, "good_answer_signals": []} for topic in topics[:options.question_count]]
            return {"job_title": title, "questions": qs}
        return {"job_title": options.job_title or message[:80], "about_company": "[about the company]",
                "responsibilities": ["[responsibility]"], "requirements": ["[requirement]"],
                "nice_to_have": [], "conditions": ["[salary, ₸]", "[schedule]"],
                "how_to_apply": "[contact]", "inclusion_note": "Equal opportunity employer."}


AGENT = HRAgent()
