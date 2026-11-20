"""Shared fixtures.

No test calls the real OpenAI API and no real personal data is used: every resume and
document below is fictional. LLM behaviour is simulated by ``ScriptedLLM`` (records the
exact messages the engine would send) or by the OpenAI SDK pointed at an in-process
``httpx.MockTransport`` (see test_llm_errors.py).
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.database.models import Company, User
from app.main import create_app
from app.security.auth import create_api_key
from app.services.llm import LLMRequest, LLMResult


class ScriptedLLM:
    """Test double for the LLM provider. Replies are scripted; requests are recorded."""

    mode = "openai"
    model = "scripted-test-model"

    def __init__(self, replies: list[str | Callable[[LLMRequest], str]] | None = None,
                 input_tokens: int = 1200, output_tokens: int = 300) -> None:
        self.replies = list(replies or [])
        self.requests: list[LLMRequest] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    @property
    def calls(self) -> int:
        return len(self.requests)

    def all_text(self, index: int = -1) -> str:
        return "\n".join(m["content"] for m in self.requests[index].messages)

    def complete(self, request: LLMRequest, *, deadline: float, mock_fn=None) -> LLMResult:
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else "OK"
        text = reply(request) if callable(reply) else reply
        return LLMResult(text=text, input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                         model=self.model, mode=self.mode)


@dataclass
class Tenant:
    company_id: str
    admin_key: str
    member_key: str
    admin_user_id: str
    member_user_id: str
    extra_keys: list[str] = field(default_factory=list)

    @staticmethod
    def headers(key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    @property
    def admin(self) -> dict[str, str]:
        return self.headers(self.admin_key)

    @property
    def member(self) -> dict[str, str]:
        return self.headers(self.member_key)


@dataclass
class Env:
    client: TestClient
    settings: Settings
    llm: object
    a: Tenant
    b: Tenant

    def db(self):
        return self.client.app.state.session_factory()

    def upload(self, headers: dict[str, str], filename: str, data: bytes, purpose: str = "knowledge_base"):
        return self.client.post("/files/upload", headers=headers, data={"purpose": purpose},
                                files={"file": (filename, data, "application/octet-stream")})

    def run(self, headers: dict[str, str], slug: str, message: str, **body):
        return self.client.post(f"/agents/{slug}/run", headers=headers, json={"message": message, **body})


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        app_env="test", llm_mode="mock", openai_api_key=None, openai_model="test-model",
        database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_dir=tmp_path / "uploads",
        demo_mode=True, daily_run_limit_per_user=10, rate_limit_per_minute=1000,
        price_input_per_1m_tokens=0.4, price_output_per_1m_tokens=1.6,
        daily_spend_limit=100.0, company_daily_spend_limit=50.0, llm_retry_base_delay_seconds=0.0,
        log_level="WARNING", demo_api_key=None, jwt_secret=None,
    )
    values.update(overrides)
    # Optional: run the same suite against PostgreSQL, e.g.
    # TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/ai_engine_test pytest
    if os.environ.get("TEST_DATABASE_URL") and "database_url" not in overrides:
        values["database_url"] = os.environ["TEST_DATABASE_URL"]
        _reset_postgres(values["database_url"])
    return Settings(_env_file=None, **values)


def _reset_postgres(url: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def _tenant(session_factory, name: str) -> Tenant:
    with session_factory() as db:
        company = Company(name=name)
        db.add(company)
        db.flush()
        admin = User(company_id=company.id, role="admin", display_name="admin")
        member = User(company_id=company.id, role="member", display_name="member")
        db.add_all([admin, member])
        db.flush()
        _, admin_key = create_api_key(db, company_id=company.id, user_id=admin.id)
        _, member_key = create_api_key(db, company_id=company.id, user_id=member.id)
        db.commit()
        return Tenant(company.id, admin_key, member_key, admin.id, member.id)


@pytest.fixture
def make_env(tmp_path: Path) -> Iterator[Callable[..., Env]]:
    clients: list[TestClient] = []

    def factory(llm=None, **overrides) -> Env:
        settings = make_settings(tmp_path, **overrides)
        app = create_app(settings, llm=llm)
        client = TestClient(app)
        client.__enter__()  # runs lifespan: migrations, storage dir
        clients.append(client)
        a = _tenant(app.state.session_factory, "Fictional Coffee LLP")
        b = _tenant(app.state.session_factory, "Fictional Other LLP")
        return Env(client=client, settings=settings, llm=app.state.llm, a=a, b=b)

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def env(make_env) -> Env:
    return make_env()


# ── Fictional documents ──────────────────────────────────────────────────────
def make_pdf(lines: list[str]) -> bytes:
    """Minimal valid PDF with a Helvetica text layer (ASCII text)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_image_only_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    import docx

    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    if table:
        t = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, cell in enumerate(row):
                t.cell(r, c).text = cell
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


FICTIONAL_RESUME_RU = [
    "Тестова Айгерим Ерболовна",
    "Email: aigerim.test@example.com",
    "Телефон: +7 (701) 000-00-00",
    "Дата рождения: 01.01.1995",
    "О себе",
    "Аналитик данных с опытом работы в розничной торговле.",
    "Опыт работы",
    "03.2021 - настоящее время, Аналитик данных, ТОО «Вымышленная Розница», Алматы",
    "- Построила дашборды продаж в Power BI для 12 магазинов",
    "- Автоматизировала отчётность на Python и SQL",
    "Образование",
    "2016, Вымышленный университет, бакалавр, информационные системы",
    "Навыки",
    "Python, SQL, Power BI, Excel",
    "Языки",
    "Казахский, русский, английский (B2)",
]

FICTIONAL_RESUME_EN_PDF = [
    "Test Candidate",
    "Email: test.candidate@example.com",
    "Summary",
    "Backend developer (fictional resume for automated tests).",
    "Experience",
    "2020 - 2024 Backend Developer, Fictional Fintech LLP, Astana",
    "- Built REST APIs with FastAPI and PostgreSQL",
    "Education",
    "2019 BSc Computer Science, Fictional University",
    "Skills",
    "Python, FastAPI, PostgreSQL, Docker, Git",
]

KB_COFFEE_RU = """Условия доставки кофейни «Вымышленный Кофе»
Доставка по Алматы осуществляется ежедневно с 9:00 до 21:00.
Стоимость доставки составляет 1 000 тенге, при заказе от 10 000 тенге доставка бесплатная.

Возврат и качество
Если напиток приготовлен неправильно, мы бесплатно приготовим его заново в течение 24 часов при наличии чека.

Программа лояльности
Каждая десятая чашка кофе бесплатно при использовании карты лояльности.
"""


def json_reply(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)
