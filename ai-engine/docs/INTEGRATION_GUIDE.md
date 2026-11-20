# Integration Guide

## 1. Подключение OpenAI API (переход из mock-режима)

Бесплатные провайдеры (Groq, Gemini, OpenRouter, локальная Qwen через Ollama) —
[FREE_LLM_PROVIDERS.md](FREE_LLM_PROVIDERS.md). Облачный вариант без терминала — [CLOUD_DEPLOY.md](CLOUD_DEPLOY.md).
OpenAI (платно) локально:

1. Получите ключ в кабинете OpenAI и запишите его **только** в `.env` (или в секреты окружения
   на сервере): `OPENAI_API_KEY=sk-...`. Файл `.env` в `.gitignore`.
2. Выберите модель: `OPENAI_MODEL=gpt-4.1-mini` (или другую с поддержкой Chat Completions и
   `response_format: json_object` — она нужна агентам `support` и `hr`).
3. Укажите тарифы этой модели из актуального прайса провайдера:
   `PRICE_INPUT_PER_1M_TOKENS`, `PRICE_OUTPUT_PER_1M_TOKENS` (в `PRICE_CURRENCY`). Без них
   `estimated_cost` = `null`, а лимиты расходов не срабатывают.
4. Проверьте: `GET /health` → `"llm_configured": true`, затем запрос к любому агенту → `"mode": "openai"`.

5. Задайте `LLM_PROVIDER=openai` (в `.env.example` по умолчанию `mock`).
6. Первый запрос делайте с вымышленными данными и проверьте в ответе: `mode == "openai"`,
   `usage.input_tokens > 0`, `usage.estimated_cost` не `null`; в `GET /runs` — запись со статусом `success`.

Модели-рассуждатели (например, семейства o-series/gpt-5) тратят часть `MAX_OUTPUT_TOKENS` на
внутренние рассуждения: при пустом ответе движок вернёт `502 llm_empty_output` — увеличьте
`MAX_OUTPUT_TOKENS` (например, до 4000) или используйте `gpt-4.1-mini`. Для них не задавайте
`OPENAI_TEMPERATURE`.

Если ключ не задан при `LLM_MODE=openai`, запуски возвращают `503 llm_not_configured`
(и логируются в `agent_runs`). Для проверки архитектуры без ключа: `LLM_MODE=mock`.

Для OpenAI-совместимого шлюза (Azure-прокси, локальный LLM-gateway): `OPENAI_BASE_URL`.

## 2. Подключение фронтенда маркетплейса

**Рекомендуемая схема: браузер → бэкенд маркетплейса → AI Engine.** Браузер не должен хранить
ключи движка.

### Вариант A (рекомендуется): JWT от бэкенда маркетплейса

1. Сгенерируйте общий секрет и задайте его движку: `JWT_SECRET=<≥32 случайных байта>`,
   `JWT_AUDIENCE=ai-engine`, `JWT_ISSUER=marketplace`.
2. Бэкенд маркетплейса после авторизации пользователя и проверки подписки компании на агента
   выпускает короткоживущий токен (5–15 минут):

```python
import time, jwt  # PyJWT
token = jwt.encode({
    "sub": str(user.id),               # id пользователя в маркетплейсе
    "company_id": str(company.id),     # id компании в маркетплейсе
    "company_name": company.name,
    "role": "admin" if user.is_company_admin else "member",
    "aud": "ai-engine", "iss": "marketplace",
    "iat": int(time.time()), "exp": int(time.time()) + 600,
}, JWT_SECRET, algorithm="HS256")
```

3. Бэкенд проксирует запросы или отдаёт токен фронтенду, который вызывает движок напрямую
   (тогда добавьте домен фронтенда в `CORS_ORIGINS`).

Компании и пользователи создаются в движке автоматически при первом токене (по их id в маркетплейсе).
**Проверку подписки на агента выполняет маркетплейс** до выдачи токена/проксирования запроса.

### Вариант B: серверный API-ключ на компанию

`python -m app.cli create-company --name "..." --external-id <id в маркетплейсе>` и
`python -m app.cli create-key --company-id <id> --role admin`. Ключ хранится в бэкенде маркетплейса;
все пользователи компании будут видны движку как один пользователь (лимиты — на ключ).

### Пример вызова из фронтенда (TypeScript)

```ts
type RunResponse = {
  run_id: string; agent: string; agent_version: string;
  status: "success" | "escalation_required" | "blocked";
  response: string; language: "ru" | "kk" | "en";
  sources: { file_id: string; filename: string; chunk_index: number | null; score: number | null; excerpt: string }[];
  usage: { input_tokens: number; output_tokens: number; total_tokens: number; estimated_cost: number | null; currency: string };
  execution_time_ms: number; mode: "openai" | "mock" | "none"; model: string | null;
  data: Record<string, unknown> | null; warnings: string[];
};

async function runAgent(slug: string, message: string, token: string, fileIds: string[] = []) {
  const res = await fetch(`${ENGINE_URL}/agents/${slug}/run`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ message, file_ids: fileIds }),   // language опционален (автоопределение)
  });
  const body = await res.json();
  if (!res.ok) throw Object.assign(new Error(body.error.message), body.error); // code, details.run_id
  return body as RunResponse;
}

async function uploadFile(file: File, purpose: "knowledge_base" | "resume" | "job_description" | "brief", token: string) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("purpose", purpose);
  const res = await fetch(`${ENGINE_URL}/files/upload`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: fd });
  const body = await res.json();
  if (!res.ok) throw Object.assign(new Error(body.error.message), body.error);
  return body; // { id, filename, chunk_count, warnings, ... }
}
```

Рекомендации для UI:
- Рендерьте `response` как Markdown **с экранированием HTML** (ответ модели — недоверенный текст).
- Показывайте `mode` (особенно `mock`), `status`, `sources` для support, `warnings`.
- `escalation_required` → покажите `data.handoff` сотруднику компании и передайте его в CRM/helpdesk
  на стороне маркетплейса (движок сам ничего не отправляет: `delivered: false`).
- Обрабатывайте 429 с `Retry-After`.
- Каталог агентов строится из `GET /agents` (имя, описание, возможности, языки, `input_schema`).

## 3. Добавление нового агента

1. Создайте `app/agents/<slug>.py`:

```python
from pydantic import BaseModel, Field
from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.prompts.common import SECURITY_RULES, language_directive

class LegalOptions(BaseModel):
    contract_type: str | None = Field(default=None, max_length=100)

class LegalOutput(BaseModel):
    checklist: list[str]

class LegalAgent(BaseAgent):
    id = "agent-legal-001"
    slug = "legal"
    name = "AI Legal Checklist"
    description = "Черновой чек-лист проверки договора."
    version = "0.1.0"
    category = "legal"
    capabilities = ("contract_checklist",)
    accepted_file_purposes = ("brief",)
    options_model = LegalOptions
    output_model = LegalOutput

    def run(self, message: str, options: BaseModel, ctx: AgentContext) -> AgentResult:
        system = "You draft contract review checklists...\n" + SECURITY_RULES + language_directive(ctx.language)
        result = ctx.call_llm(
            [{"role": "system", "content": system}, {"role": "user", "content": message}],
            mock_fn=lambda: "MOCK checklist",
        )
        return AgentResult(response=result.text, data={"checklist": []})

AGENT = LegalAgent()
```

2. Добавьте модуль в `AGENT_MODULES` (`.env`): `...,app.agents.legal`.
3. Добавьте тесты по образцу `tests/test_marketing.py`.

API, логирование, лимиты, учёт токенов/стоимости, файлы и права доступа подключаются автоматически.
Загружаются только модули из пакета `app.agents.*` (код проходит ревью в этом репозитории);
выполнение стороннего непроверенного кода на сервере не поддерживается намеренно.

Правила для агентов: использовать только `ctx.call_llm` (учёт токенов и лимиты), оборачивать
документы в `wrap_untrusted(...)` и пропускать через `sanitize_document_text(...)`, не выполнять
команд, не делать сетевых вызовов и платежей, не писать тексты пользователей в логи.

## 4. Подключение PostgreSQL

```bash
pip install -r requirements.txt     # включает psycopg[binary]
DATABASE_URL=postgresql+psycopg://ai_engine:<пароль>@<host>:5432/ai_engine
python -m app.cli migrate            # или AUTO_MIGRATE=true при старте
```

Миграции — Alembic (`migrations/`). Новая миграция после изменения моделей:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

Весь тестовый набор проверен на PostgreSQL 16:
`TEST_DATABASE_URL=postgresql+psycopg://... pytest` (тесты **очищают схему public** этой БД — используйте
отдельную тестовую базу).

## 5. Развёртывание на сервере

1. Соберите образ: `docker build -t ai-engine:1.0.0 .` (непривилегированный пользователь, healthcheck).
2. Секреты (`OPENAI_API_KEY`, `JWT_SECRET`, пароль БД) передавайте через секреты оркестратора/CI,
   не через образ.
3. Production-настройки: `APP_ENV=production`, `DEMO_MODE=false` (или оставьте лимит), заданы цены и
   лимиты расходов, `CORS_ORIGINS=https://<домен маркетплейса>`, `ENABLE_DEMO_UI=false`,
   при необходимости `ENABLE_DOCS=false`, PostgreSQL вместо SQLite.
4. Поставьте reverse proxy (nginx/Caddy) с TLS. За прокси запускайте uvicorn с
   `--proxy-headers --forwarded-allow-ips <IP прокси>` (не `*`), ограничьте `client_max_body_size 11m`.
5. Том для `STORAGE_DIR` — приватный, не раздаётся по HTTP; включите бэкапы БД и тома.
6. Одна реплика: in-memory rate limiter корректен. Несколько реплик: вынесите rate limiter в Redis и
   используйте общий том/объектное хранилище для файлов (см. ARCHITECTURE.md).
7. Мониторинг: `GET /health`, JSON-логи в stdout (без текстов запросов), `GET /stats/agents`.

Публичный деплой в рамках этой задачи **не выполнялся**.

## 6. Переменные окружения

Полный список с комментариями — `.env.example`. Обязательные для работы с OpenAI: `OPENAI_API_KEY`,
`OPENAI_MODEL`. Рекомендуемые: `PRICE_INPUT_PER_1M_TOKENS`, `PRICE_OUTPUT_PER_1M_TOKENS`,
`DATABASE_URL`, `CORS_ORIGINS`, `JWT_SECRET` (или ключи через CLI), `DEMO_API_KEY` (для локального демо).
