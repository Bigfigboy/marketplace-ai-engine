# AI Engine — маркетплейс AI-агентов для бизнеса Казахстана

MVP движка, через который маркетплейс запускает AI-агентов по единому API.

| Агент | Slug | Что делает | Статус |
|---|---|---|---|
| AI Marketing Agent | `marketing` | Анализ ЦА, стратегия, рекламные тексты, контент-планы, посты Instagram/TikTok/Telegram, КП, email-кампании, рекомендации для МСБ. Только черновики. | Готов (MVP) |
| AI Customer Support Agent | `support` | База знаний компании (RAG), ответы с источниками, FAQ, жалобы, `escalation_required` + черновик обращения менеджеру | Готов (MVP) |
| AI HR Assistant | `hr` | Резюме PDF/DOCX → структурированная сводка, требования вакансии, подтверждённые навыки и недостающая информация, вопросы для интервью, черновик вакансии. Без решений и рейтингов. | Готов (MVP) |

Языки: русский (`ru`), казахский (`kk`), английский (`en`), автоопределение.

> Реальные вызовы OpenAI API в этой среде **не выполнялись** (ключа не было). Интеграция с OpenAI
> проверена тестами через официальный SDK с имитацией HTTP-ответов. Подробно — `docs/TEST_REPORT.md`.

## Быстрый старт (локально)

Требуется Python 3.11+.

```bash
cd ai-engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

В `.env` укажите ключ OpenAI и ключ доступа к демо:

```bash
OPENAI_API_KEY=sk-...                 # ключ только здесь, никогда в коде
OPENAI_MODEL=gpt-4.1-mini             # любая модель с Chat Completions
DEMO_API_KEY=aie_<сгенерируйте>       # python -c "import secrets; print('aie_' + secrets.token_urlsafe(32))"
PRICE_INPUT_PER_1M_TOKENS=...         # цены модели из прайса провайдера (для расчёта стоимости)
PRICE_OUTPUT_PER_1M_TOKENS=...
```

Запуск:

```bash
uvicorn app.main:app --reload --port 8000
```

- Swagger UI: http://localhost:8000/docs → **Authorize** → вставьте `DEMO_API_KEY`
- Демо-интерфейс: http://localhost:8000/demo/
- Health: http://localhost:8000/health

Без ключа OpenAI можно проверить архитектуру в явно обозначенном mock-режиме:
`LLM_MODE=mock` (ответы помечены `"mode": "mock"` и баннером «MOCK-РЕЖИМ … не является результатом работы ИИ»).

### Docker

```bash
cd ai-engine
cp .env.example .env   # заполните OPENAI_API_KEY, DEMO_API_KEY
docker compose up --build
```

С PostgreSQL: `docker compose --profile postgres up --build`, в `.env` задайте `POSTGRES_PASSWORD` и
`DATABASE_URL_DOCKER=postgresql+psycopg://ai_engine:<пароль>@postgres:5432/ai_engine`.

## Ключи доступа

Пользователь и компания определяются **только** по проверенным учётным данным:

- `Authorization: Bearer aie_...` или `X-API-Key: aie_...` — ключ движка (в БД хранится только SHA-256);
- `Authorization: Bearer <JWT>` — токен, выпущенный бэкендом маркетплейса (если задан `JWT_SECRET`).

```bash
python -m app.cli seed-demo                                   # 2 вымышленные компании × (admin, member)
python -m app.cli create-company --name "ТОО Пример"          # -> company_id
python -m app.cli create-key --company-id <id> --role admin   # ключ показывается один раз
python -m app.cli revoke-key --key-prefix aie_AbCdEf
```

## Запуск агентов

```bash
KEY=aie_...
# Marketing
curl -s localhost:8000/agents/marketing/run -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"message":"Создай контент-план для кофейни в Алматы","language":"ru","file_ids":[]}'

# Support: загрузить базу знаний, затем спросить
curl -s localhost:8000/files/upload -H "Authorization: Bearer $KEY" \
  -F "file=@examples/files/kb_coffee_ru.txt" -F "purpose=knowledge_base"
curl -s localhost:8000/agents/support/run -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"message":"Сколько стоит доставка?"}'

# HR: загрузить вымышленное резюме и получить сводку
curl -s localhost:8000/files/upload -H "Authorization: Bearer $KEY" \
  -F "file=@examples/files/resume_fictional_ru.docx" -F "purpose=resume"
curl -s localhost:8000/agents/hr/run -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"message":"Сделай сводку резюме","file_ids":["<file_id>"]}'
```

Больше примеров: `examples/requests.http`, `examples/curl_examples.sh`, `docs/API_DOCUMENTATION.md`.

## Тесты

```bash
pytest                       # 94 теста, SQLite, без сети и без реального OpenAI
TEST_DATABASE_URL=postgresql+psycopg://user@host:5432/test_db pytest   # тот же набор на PostgreSQL
```

## Структура

```
ai-engine/
├── app/
│   ├── main.py              # фабрика FastAPI, middleware, lifespan (миграции)
│   ├── cli.py               # управление компаниями и ключами
│   ├── agents/              # base.py (BaseAgent), registry.py, marketing.py, support.py, hr.py
│   ├── api/                 # routes.py (все endpoints), deps.py
│   ├── core/                # config, errors, logging, redaction, i18n
│   ├── database/            # models.py (SQLAlchemy), session.py
│   ├── prompts/             # системные промпты агентов + общие правила безопасности/языка
│   ├── schemas/             # Pydantic-схемы запросов и ответов
│   ├── security/            # auth (API key/JWT), middleware, rate limit, prompt_guard
│   └── services/            # llm, runner, files, extraction, rag, quotas, pricing, stats, hr_tools
├── demo/                    # технический демо-интерфейс (/demo)
├── examples/                # вымышленные документы, примеры запросов
├── migrations/              # Alembic
├── tests/                   # pytest
├── docs/                    # API_DOCUMENTATION, INTEGRATION_GUIDE, ARCHITECTURE, TEST_REPORT
├── THIRD_PARTY_NOTICES.md
├── .env.example, requirements.txt, Dockerfile, docker-compose.yml, alembic.ini, pytest.ini
```

## Документация

- [API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md) — endpoints, схемы, коды ошибок
- [INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md) — подключение фронтенда маркетплейса, новые агенты, PostgreSQL, деплой
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — устройство движка, безопасность, масштабирование
- [CLOUD_DEPLOY.md](docs/CLOUD_DEPLOY.md) — запуск в облаке (Render) без терминала
- [FRONTEND_QUICKSTART.md](docs/FRONTEND_QUICKSTART.md) — памятка для frontend-разработчика
- [TEST_REPORT.md](docs/TEST_REPORT.md) — фактические результаты тестов
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — использованные open-source проекты и лицензии

## Известные ограничения

См. раздел «Ограничения» в [ARCHITECTURE.md](docs/ARCHITECTURE.md#ограничения-mvp). Кратко: реальный
OpenAI не тестировался в этой среде; RAG лексический (BM25), без эмбеддингов; rate limit в памяти
одного процесса; нет OCR; интеграции с CRM/helpdesk для передачи обращений менеджеру нет; качество
казахского языка зависит от выбранной модели и требует проверки носителем языка.
