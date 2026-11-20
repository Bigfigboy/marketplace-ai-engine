# Third-Party Notices

AI Engine использует идеи и адаптированные фрагменты кода из трёх open-source проектов
(все — MIT License) и ряд библиотек. Ниже — что именно взято, что сознательно **не** взято
и почему, а также тексты лицензий.

Проверка проводилась 20.11.2026 по клонам `--depth 1`:

| Проект | Коммит | Лицензия |
|---|---|---|
| [loopmark-opensource/loopmark-agent](https://github.com/loopmark-opensource/loopmark-agent) | `92f53c6` | MIT, © 2026 Marketing Agent Contributors |
| [openai/openai-cs-agents-demo](https://github.com/openai/openai-cs-agents-demo) | `bd7bfca` | MIT, © 2025 OpenAI |
| [NabiBukhsh-AI/Resume-AI-Parser](https://github.com/NabiBukhsh-AI/Resume-AI-Parser) | `c68de62` | MIT, © 2026 Nabi Bukhsh Baloch |

Код этих проектов **не запускался** на сервере движка; использованы только проверенные
вручную фрагменты, перенесённые в наш код.

## 1. loopmark-agent → Marketing Agent

**Использовано (адаптировано):** рекомендации по контенту и email из `prompts/templates.py`
(`POSTING_SYSTEM`): хук в первой строке, CTA, ограничения длины темы письма (≤ 50) и прехедера
(≤ 85), плейсхолдер `{{first_name}}`, plain-text версия, шаг drip-цепочек, работа через
сегменты аудитории. Файл: `app/prompts/marketing.py`.

**Не использовано и почему:**
- `tools/publisher_tools.py` — автопубликация в Twitter/LinkedIn и Buffer. По ТЗ агент создаёт
  только черновики и не публикует/не отправляет.
- `tools/audience_tools.py::analyze_website_for_audience` — загрузка произвольных URL с сервера
  (риск SSRF) и скрейпинг; не нужно для MVP.
- LangChain / LangGraph (ReAct-агенты с инструментами), JSON-хранилище, планировщик — избыточны
  для одношагового генератора черновиков; заменены прямым вызовом OpenAI SDK.

## 2. openai-cs-agents-demo → Customer Support Agent

**Использовано (идея):** паттерн guardrails — отдельная проверка «jailbreak / попытка обойти
инструкции» до основного агента и триаж запроса. Реализовано детерминированно (регулярные
выражения ru/kk/en) в `app/security/prompt_guard.py` и в промпте `app/prompts/support.py`.

**Не использовано и почему:**
- Авиационный домен, инструменты (бронирование, места, компенсации), ChatKit UI и Next.js фронтенд —
  не относятся к задаче.
- В демо нет RAG и изоляции по компаниям; память in-memory. RAG, изоляция и источники реализованы
  с нуля (`app/services/rag.py`, `app/agents/support.py`).
- OpenAI Agents SDK: наши агенты однопроходные без инструментов и handoff'ов; прямой SDK даёт
  точный контроль над ретраями, дедлайнами и учётом токенов (см. `docs/ARCHITECTURE.md`).
- LLM-guardrail на `gpt-4.1-mini` удваивал бы стоимость каждого запроса; детерминированная проверка
  бесплатна и тестируема офлайн.

## 3. Resume-AI-Parser → HR Assistant и обработка файлов

**Использовано (адаптировано):**
- `extraction/detection.py` → определение формата по содержимому, `sanitize_filename`
  (`app/services/extraction.py`). Зависимость `puremagic` заменена явной проверкой сигнатур и
  структуры; добавлено требование совпадения расширения и содержимого, защита от ZIP-бомб,
  лимит страниц PDF.
- `extraction/extractors.py` → извлечение текста PDF (pypdf) и DOCX (python-docx, включая таблицы,
  колонтитулы), нормализация пробелов, ошибка «нужен OCR» для сканов.
- `observability/redaction.py` → маскирование email/телефонов/секретов в логах
  (`app/core/redaction.py`); добавлены ИИН/БИН РК и ключи API.
- `llm/prompts.py` → правила «опирайся только на документ, не выдумывай работодателей, даты,
  навыки» (`app/prompts/hr.py`).

**Не использовано и почему:**
- `pipeline/matching.py` и `domain/matching.py` — скоринг и ранжирование кандидатов. По ТЗ HR-агент
  не ранжирует, не оценивает и не принимает решений о кандидатах.
- Anthropic-провайдер, Streamlit UI, кэш, CLI — не нужны в этой архитектуре.

## Лицензии исходных проектов

### loopmark-agent

```
MIT License

Copyright (c) 2026 Marketing Agent Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### openai-cs-agents-demo

```
Copyright 2025 OpenAI

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the “Software”), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Resume-AI-Parser

```
MIT License

Copyright (c) 2026 Nabi Bukhsh Baloch

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Библиотеки (runtime)

Устанавливаются через `requirements.txt`, в репозиторий не копируются.
`pip-audit -r requirements.txt` (20.11.2026): **No known vulnerabilities found**.

| Пакет | Лицензия |
|---|---|
| FastAPI, Starlette | MIT / BSD-3-Clause |
| Uvicorn | BSD-3-Clause |
| openai (Python SDK) | Apache-2.0 |
| Pydantic, pydantic-settings | MIT |
| SQLAlchemy, Alembic | MIT |
| pypdf | BSD-3-Clause |
| python-docx | MIT |
| python-multipart | Apache-2.0 |
| PyJWT | MIT |
| httpx | BSD-3-Clause |
| tzdata | Apache-2.0 |
| psycopg (драйвер PostgreSQL) | LGPL-3.0 (используется как немодифицированная библиотека) |
| pytest (тесты) | MIT |

Транзитивные зависимости (проверено `pip-licenses` в чистом окружении Python 3.11 из `requirements.txt`):
MIT, BSD-2/3-Clause, Apache-2.0, PSF-2.0 (`typing_extensions`), а также `certifi` — MPL-2.0
(файловый copyleft; используется без изменений) и `uvloop` — MIT/Apache-2.0. Лицензий GPL/AGPL
в дереве зависимостей нет. Для коммерческого использования ограничений не выявлено при условии
сохранения уведомлений об авторских правах; LGPL-библиотека `psycopg` используется как отдельный
немодифицированный пакет (допускается замена пользователем).
