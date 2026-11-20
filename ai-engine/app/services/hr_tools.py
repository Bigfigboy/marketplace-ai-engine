"""Deterministic helpers for the HR assistant.

* ``redact_resume`` - masks contact details, identifiers and protected personal
  characteristics before any text leaves the server (also keeps real PII away from
  third-party APIs during testing).
* ``find_skills`` / ``evidence_in`` - grounded skill detection used to verify the model's
  output: a skill is "confirmed" only if the resume text actually contains it.
* ``check_hr_request`` - policy guard that refuses ranking, hiring/rejection decisions and
  any evaluation by protected characteristics before the LLM is called.
* ``strip_decisions`` - removes decision/ranking language from model output.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.redaction import EMAIL_RE, IIN_RE

_FLAGS = re.IGNORECASE | re.UNICODE

# ── Redaction ────────────────────────────────────────────────────────────────
# Stricter than the log redactor so that date ranges ("01.2019 - 03.2023") survive.
_PHONE_RE = re.compile(r"(?<![\w.])(?:\+7|8|\+\d{1,3})[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\w)")
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+|\b(?:linkedin\.com|t\.me|instagram\.com|facebook\.com|vk\.com)/\S+", _FLAGS)
_PROTECTED_LINE_RE = re.compile(
    r"^\s*[-•*]?\s*(дата\s+рождения|год\s+рождения|возраст|пол|национальность|гражданство|"
    r"семейное\s+положение|дети|религия|вероисповедание|место\s+рождения|"
    r"date\s+of\s+birth|birth\s*date|born|age|gender|sex|nationality|citizenship|marital\s+status|"
    r"children|religion|place\s+of\s+birth|"
    r"туған\s+(күні|жылы|жері)|жасы|жынысы|ұлты|азаматтығы|отбасылық\s+жағдайы|балалары|діні)\b.*$",
    _FLAGS | re.MULTILINE,
)
_NAME_LINE_RE = re.compile(r"^\s*(фио|ф\.и\.о\.|имя|name|full\s+name|candidate|кандидат|аты-жөні|аты)\s*[:\-–].*$",
                           _FLAGS | re.MULTILINE)
_LIKELY_NAME_RE = re.compile(r"^[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі'’-]+(\s+[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі'’.-]+){1,3}$")


def redact_resume(text: str, *, mask_name_line: bool = True) -> tuple[str, dict[str, int]]:
    """Mask contact details, identifiers and protected characteristics.

    ``mask_name_line=False`` is used for job descriptions, whose first line is a job title.
    """
    counts: dict[str, int] = {}

    def _sub(pattern: re.Pattern[str], repl: str, value: str, key: str) -> str:
        new, n = pattern.subn(repl, value)
        if n:
            counts[key] = counts.get(key, 0) + n
        return new

    text = _sub(_PROTECTED_LINE_RE, "[redacted: personal characteristic]", text, "protected_attributes")
    text = _sub(_NAME_LINE_RE, "[redacted: name]", text, "name")
    text = _sub(EMAIL_RE, "[redacted: email]", text, "email")
    text = _sub(_URL_RE, "[redacted: link]", text, "link")
    text = _sub(IIN_RE, "[redacted: id]", text, "iin")
    text = _sub(_PHONE_RE, "[redacted: phone]", text, "phone")
    # A first line that looks like a personal name (2-4 capitalised words) is masked.
    lines = text.split("\n")
    for i, line in enumerate(lines[:3] if mask_name_line else []):
        if line.strip() and _LIKELY_NAME_RE.match(line.strip()) and len(line.strip()) < 60:
            lines[i] = "[redacted: name]"
            counts["name"] = counts.get("name", 0) + 1
            break
    return "\n".join(lines), counts


# ── Skills ───────────────────────────────────────────────────────────────────
SKILL_DICTIONARY: tuple[str, ...] = (
    "Python", "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Java", "JavaScript", "TypeScript",
    "React", "Vue", "Angular", "Node.js", "Go", "C#", "C++", "PHP", "Kotlin", "Swift", "Django", "FastAPI",
    "Flask", "Spring", "Docker", "Kubernetes", "Git", "Linux", "AWS", "Azure", "GCP", "CI/CD", "REST",
    "GraphQL", "Kafka", "RabbitMQ", "Airflow", "Spark", "Pandas", "NumPy", "Machine Learning", "PyTorch",
    "TensorFlow", "Excel", "Power BI", "Tableau", "Google Analytics", "1С", "1C", "SAP", "Bitrix24",
    "amoCRM", "CRM", "Jira", "Confluence", "Figma", "Photoshop", "Illustrator", "SMM", "SEO", "SEM",
    "Targeting", "таргет", "контекстная реклама", "копирайтинг", "Agile", "Scrum", "Kanban",
    "управление проектами", "project management", "бухгалтерский учет", "бухгалтерский учёт", "МСФО",
    "IFRS", "налоговый учет", "налоговый учёт", "продажи", "sales", "B2B", "B2C", "переговоры",
    "negotiation", "логистика", "logistics", "закупки", "procurement", "HR", "рекрутинг", "recruiting",
    "Data Analysis", "анализ данных", "customer service", "обслуживание клиентов", "Kaspi",
    "English", "английский", "казахский", "русский", "Kazakh", "Russian", "Wildberries", "Ozon",
)


def _skill_pattern(skill: str) -> re.Pattern[str]:
    escaped = re.escape(skill)
    return re.compile(rf"(?<![\w+#]){escaped}(?![\w+#])", _FLAGS)


_SKILL_PATTERNS = [(s, _skill_pattern(s)) for s in SKILL_DICTIONARY]


def _line_with(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start: line_end if line_end != -1 else len(text)].strip()
    return line[:200]


def find_skills(text: str) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for skill, pattern in _SKILL_PATTERNS:
        m = pattern.search(text)
        if m and skill.lower() not in found:
            found[skill.lower()] = {"name": skill, "evidence": _line_with(text, m.start(), m.end())}
    return list(found.values())


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("ё", "е")).strip(" .,;:-–—\"'«»")


def evidence_in(source_text: str, evidence: str | None) -> bool:
    """True when the whole ``evidence`` quote literally appears in ``source_text``."""
    if not evidence:
        return False
    needle = _norm(evidence)
    return len(needle) >= 2 and needle in _norm(source_text)


def verify_skill(source_text: str, name: str, evidence: str | None) -> str | None:
    """Return a quote proving ``name`` is in the resume, or None.

    The model's quote is kept only if it appears verbatim in the resume AND mentions the
    skill as a whole word ("Java" does not match "JavaScript"). Otherwise a line from the
    resume containing the skill is used; if there is none the skill is not confirmed.
    """
    pattern = _skill_pattern(name)
    if evidence and evidence_in(source_text, evidence) and pattern.search(evidence):
        return evidence
    match = pattern.search(source_text)
    return _line_with(source_text, match.start(), match.end()) if match else None


def looks_like_resume(text: str) -> bool:
    """Heuristic: CV-style sections plus contact details, and no vacancy sections."""
    sections = split_sections(text)
    cv_sections = sum(1 for key in ("experience", "education", "skills", "summary") if sections.get(key))
    job_sections = sum(1 for key in ("requirements", "responsibilities", "conditions", "preferred")
                       if sections.get(key))
    has_contacts = bool(EMAIL_RE.search(text) or _PHONE_RE.search(text))
    return cv_sections >= 2 and job_sections == 0 and (has_contacts or cv_sections >= 3)


# ── Sections (offline mode) ──────────────────────────────────────────────────
_SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "summary": ("о себе", "summary", "profile", "профиль", "өзім туралы", "цель", "objective"),
    "experience": ("опыт работы", "опыт", "experience", "work experience", "employment", "жұмыс тәжірибесі",
                   "тәжірибе"),
    "education": ("образование", "education", "білімі", "білім"),
    "skills": ("навыки", "ключевые навыки", "skills", "key skills", "дағдылар", "дағдылары", "компетенции"),
    "languages": ("языки", "знание языков", "languages", "тілдер", "тілдерді білуі"),
    "certifications": ("сертификаты", "курсы", "certifications", "certificates", "courses", "сертификаттар",
                       "курстар"),
    "requirements": ("требования", "requirements", "талаптар", "мы ожидаем", "what we expect", "must have"),
    "preferred": ("будет плюсом", "nice to have", "preferred", "артықшылық", "желательно"),
    "responsibilities": ("обязанности", "responsibilities", "міндеттер", "задачи"),
    "conditions": ("условия", "мы предлагаем", "conditions", "we offer", "жағдайлар", "ұсынамыз"),
}


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"_header": []}
    current = "_header"
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        key = _norm(line.rstrip(":"))
        heading = next((name for name, words in _SECTION_HEADINGS.items() if key in words), None)
        if heading and len(line) < 40:
            current = heading
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(re.sub(r"^[-•*]\s*", "", line))
    return sections


# ── Policy guard ─────────────────────────────────────────────────────────────
_DECISION_REQUEST_RE = re.compile(
    r"(ранжир|рейтинг\w*\s+кандидат|отсортир\w*\s+кандидат|кто\s+(из\s+них\s+|из\s+кандидатов\s+)?лучше|"
    r"лучш\w+\s+кандидат|выбери\s+кандидат|оцени\w*\s+кандидат|поставь\s+(ему\s+|ей\s+)?(балл|оценк)|"
    r"по\s+шкале|отклони|отказать\s+кандидат|нанять\s+ли|нанимать\s+ли|стоит\s+ли\s+(его\s+|её\s+|ее\s+)?нан|"
    r"нанимать\s+или\s+нет|брать\s+ли|прими\s+решение|отсе(ять|й|ивай)|подходит\s+ли\s+(он|она|кандидат)|"
    r"\brank\w*\b|\bscore\s+(the\s+|this\s+)?candidate|\brate\s+(the\s+|this\s+)?candidate|shortlist|"
    r"who\s+is\s+(the\s+)?best|best\s+candidate|should\s+(we|i)\s+hire|hire\s+or\s+not|"
    r"reject\s+(the\s+|this\s+)?(candidate|applicant)|decide\s+whether|screen\s+out|filter\s+out|"
    r"is\s+(he|she|the\s+candidate)\s+a\s+(good\s+)?fit|"
    r"саралап|саралаңыз|рейтинг\w*\s+жаса|үздік\s+үміткер|кімді\s+таңда|жұмысқа\s+алу\s+керек\s+пе|"
    r"қабылдау\s+керек\s+пе|бас\s+тарт|балл\s+қой|үміткерді\s+бағала)", _FLAGS)

_PROTECTED_TERM_RE = re.compile(
    r"(возраст|молод\w*|пожил\w*|старше\s+\d{2}|моложе\s+\d{2}|\bпол(а|у|ом)?\b|мужчин\w*|женщин\w*|девушк\w*|"
    r"\bпарн(я|ей|и)\b|национальн\w*|этническ\w*|славянск\w*|религи\w*|вероисповед\w*|мусульм\w*|христиан\w*|"
    r"беремен\w*|декрет\w*|семейн\w*\s+положен\w*|замуж\w*|женат\w*|дет(и|ей)\b|инвалидн\w*|внешност\w*|"
    r"привлекательн\w*|"
    r"\bage\b|\baged\b|\byoung\w*|\bold(er)?\b|gender|\bmale\b|\bfemale\b|\bmen\b|\bwomen\b|\bgirls?\b|"
    r"nationality|ethnic\w*|religio\w*|muslim|christian|pregnan\w*|marital|married|children|disabilit\w*|"
    r"appearance|attractive|"
    r"\bжасы\b|жасқа\s+дейін|жынысы|\bер\s+адам|әйел\w*|ұлты|діні|отбасылық|жүкті|балалары|мүгедек\w*|"
    r"сырт\s+келбет\w*)", _FLAGS)
_NEGATION_BEFORE_RE = re.compile(
    r"(без|не\s+указ\w*|не\s+учитыв\w*|без\s+учета|без\s+учёта|независимо\s+от|исключи\w*|убери\w*|"
    r"without|regardless\s+of|irrespective\s+of|exclude|remove|not\s+mention|do\s+not\s+consider|"
    r"ескермей|көрсетпей|қарамастан)\W+(\w+\W+){0,3}$", _FLAGS)


def check_hr_request(message: str) -> str | None:
    """Return a violation code when the request asks for a prohibited action."""
    if _DECISION_REQUEST_RE.search(message):
        return "hiring_decision_or_ranking_requested"
    for match in _PROTECTED_TERM_RE.finditer(message):
        before = message[max(0, match.start() - 60): match.start()]
        if not _NEGATION_BEFORE_RE.search(before):
            return "protected_characteristic_requested"
    return None


_DECISION_OUTPUT_RE = re.compile(
    r"[^.!?\n]*(рекоменд\w*\s+(к\s+)?(найм|нанять|принять\s+на\s+работу|отклон|отказ)|"
    r"кандидат\w*\s+(не\s+)?подходит|следует\s+(нанять|отклонить|отказать)|стоит\s+(нанять|отклонить)|"
    r"(recommend|suggest)\w*\s+(hiring|rejecting|to\s+hire|to\s+reject)|"
    r"\b(is|isn't|is\s+not)\s+a\s+(good|strong|poor|weak|great)\s+fit|should\s+(be\s+)?(hired|rejected)|"
    r"жұмысқа\s+алу\w*\s+ұсын|бас\s+тарту\w*\s+ұсын)[^.!?\n]*[.!?]?", _FLAGS)
_FORBIDDEN_KEYS = {"score", "scores", "rank", "ranking", "rating", "fit", "fit_score", "match_score",
                   "match_percentage", "recommendation", "decision", "hire", "reject", "verdict",
                   "age", "gender", "nationality", "religion", "marital_status", "date_of_birth"}


def strip_decisions(value: Any, removed: list[str]) -> Any:
    """Recursively drop forbidden keys and decision sentences from model output."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in _FORBIDDEN_KEYS:
                removed.append(f"key:{k}")
                continue
            out[k] = strip_decisions(v, removed)
        return out
    if isinstance(value, list):
        return [strip_decisions(v, removed) for v in value]
    if isinstance(value, str) and _DECISION_OUTPUT_RE.search(value):
        removed.append("decision_sentence")
        return _DECISION_OUTPUT_RE.sub("", value).strip()
    return value
