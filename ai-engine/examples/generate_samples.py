"""Generate fictional sample documents for the demo (no real personal data).

    python examples/generate_samples.py   # writes examples/files/*
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.conftest import (  # noqa: E402  (reuses the same fictional fixtures as the tests)
    FICTIONAL_RESUME_EN_PDF,
    FICTIONAL_RESUME_RU,
    KB_COFFEE_RU,
    make_docx,
    make_pdf,
)

OUT = ROOT / "examples" / "files"
JOB_RU = """Аналитик данных (вымышленная вакансия)
Требования
- Опыт работы с SQL
- Python для анализа данных
- Опыт с Tableau
Будет плюсом
- Английский язык
Условия
- Офис в Алматы, гибридный график
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "kb_coffee_ru.txt").write_text(KB_COFFEE_RU, encoding="utf-8")
    (OUT / "job_data_analyst_ru.txt").write_text(JOB_RU, encoding="utf-8")
    (OUT / "resume_fictional_ru.docx").write_bytes(make_docx(FICTIONAL_RESUME_RU))
    (OUT / "resume_fictional_en.pdf").write_bytes(make_pdf(FICTIONAL_RESUME_EN_PDF))
    print("written:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
