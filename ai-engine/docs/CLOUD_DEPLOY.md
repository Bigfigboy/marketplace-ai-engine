# Запуск в облаке без VS Code и локального терминала

Цель — показать AI Engine команде: открыть ссылку в браузере, ввести ключ и проверить трёх агентов.
Всё делается в веб-интерфейсах GitHub и Render. **Сам деплой выполняет человек из команды** — в рамках
разработки ничего не публиковалось и платных операций не выполнялось.

По умолчанию `render.yaml` запускает движок в **mock-режиме без API-ключей** (бюджет 0). Подключение
бесплатной настоящей модели (Groq, Gemini, OpenRouter) — шаг 3, подробности и условия тарифов —
[FREE_LLM_PROVIDERS.md](FREE_LLM_PROVIDERS.md).

## Render (Docker, бесплатный план)

Почему Render: собирает наш `Dockerfile` прямо из GitHub, настройки задаются в браузере, в репозитории
уже есть `render.yaml`, есть бесплатный план, HTTPS включён автоматически. Возраст — **16+** по условиям Render.

Ограничения бесплатного плана (на 20.11.2026; проверьте при регистрации):
- 512 MB RAM, 0.1 CPU, 750 часов в месяц на workspace;
- после ~15 минут без запросов сервис «засыпает», первый запрос будит его около минуты;
- диск не сохраняется: при перезапуске база SQLite, загруженные файлы и история **сбрасываются**
  (для демо нормально: `DEMO_API_KEY` создаётся заново при старте);
- карта: официально не требуется, но поддержка Render упоминает возможную проверку карты ($1).

> Сервис получает публичный адрес `https://<имя>.onrender.com`. Без ключа доступа API и данные
> недоступны (открыты только `/health`, `/docs` и статическая страница `/demo`), но адрес публичный.

### Шаг 1. Ключ демо-доступа

Строка, начинающаяся с `aie_`, и затем не менее 32 случайных букв/цифр (например, сгенерируйте в
менеджере паролей пароль из 40 символов без спецсимволов и допишите спереди `aie_`). Это пароль для
команды — передавайте только в закрытом чате.

### Шаг 2. Создайте сервис (mock, без API-ключей)

1. https://render.com → **Sign in with GitHub** → разрешите доступ к `bigfigboy/marketplace-ai-engine`.
2. **New → Blueprint** → репозиторий → ветка **`claude/keen-shannon-qawv3n`** (или та, куда команда
   смержит работу).
3. Render покажет сервис `ai-engine-demo` и попросит одно значение — `DEMO_API_KEY` (шаг 1).
4. **Apply / Deploy**. Сборка — несколько минут (вкладка **Logs**; в логе `"event": "startup"`,
   `"llm_provider": "mock"`).

Если Blueprint недоступен: **New → Web Service** → репозиторий → Runtime **Docker**, Dockerfile Path
`./ai-engine/Dockerfile`, Docker Build Context `./ai-engine`, Instance Type **Free**, Health Check Path
`/health`; во вкладке **Environment** добавьте переменные из `render.yaml` вручную.

Проверка:
1. `https://<имя>.onrender.com/health` → `"status": "ok"`, `"llm_provider": "mock"`.
2. `https://<имя>.onrender.com/demo/` → вставьте ключ → **OK**.
3. **Marketing**: «Создай контент-план для кофейни в Алматы» → структура черновика с плашкой MOCK.
4. **Support**: в «Файлы» загрузите `kb_coffee_ru.txt` (GitHub → `ai-engine/examples/files/kb_coffee_ru.txt`
   → Download raw file) как «База знаний»; «Сколько стоит доставка?» → ответ-цитата с источником;
   «Есть ли парковка?» → `escalation_required` и черновик обращения «не отправлен».
5. **HR**: загрузите `resume_fictional_ru.docx` как «Резюме», отметьте, «Сделай сводку резюме» →
   локальный разбор (навыки, опыт, образование). «Ранжируй кандидатов» → отказ (`blocked`).
6. Swagger: `/docs` → **Authorize** → ключ → любой endpoint.

### Шаг 3. Подключите бесплатную модель (по желанию)

Нужен ключ провайдера, полученный совершеннолетним участником (Groq, Gemini, OpenRouter — 18+).
Render → сервис → **Environment** → **Add Environment Variable** → **Save** (перезапуск автоматически):

| Провайдер | Переменные |
|---|---|
| Groq | `LLM_PROVIDER=groq`, `GROQ_API_KEY=...` |
| Google Gemini | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=...` (проект без биллинга) |
| OpenRouter | `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY=...` (модель только `:free`) |

Проверка: `/health` → `"llm_provider": "groq"` (и т.д.), `"llm_billing": "free"`, `"llm_configured": true`;
в демо у ответа чип `groq · openai/gpt-oss-20b`, стоимость `0`. Вернуться в mock — `LLM_PROVIDER=mock`.

OpenAI (платно) подключается так же (`LLM_PROVIDER=openai`, `OPENAI_API_KEY`, цены
`PRICE_INPUT_PER_1M_TOKENS`/`PRICE_OUTPUT_PER_1M_TOKENS`) — только при появлении бюджета.

### Обновление и остановка

- Новый код: сервис → **Manual Deploy → Deploy latest commit** (`autoDeploy` выключен).
- Остановить: сервис → **Settings → Suspend** (или Delete). Ключи провайдеров можно отозвать в их консолях.

### Частые проблемы

| Симптом | Причина / решение |
|---|---|
| `/health` → `"llm_problem": "llm_not_configured"` | для выбранного `LLM_PROVIDER` не задан ключ (`GROQ_API_KEY`, `GEMINI_API_KEY`, ...) |
| `"llm_problem": "llm_model_not_allowed"` | модель не из бесплатного allowlist / у OpenRouter нет `:free` — см. FREE_LLM_PROVIDERS.md |
| 401 в демо | ключ введён с ошибкой — сверьте с `DEMO_API_KEY` в Environment |
| Деплой падает при старте, в логах `API keys must start with 'aie_'` | `DEMO_API_KEY` не начинается с `aie_` или короче 24 символов |
| 429 `llm_free_quota_exhausted` | исчерпан бесплатный дневной лимит провайдера; ждать `Retry-After` (движок не переключается на платные модели) |
| 429 `llm_rate_limited` | слишком часто для бесплатного тарифа; подождать |
| 413 `llm_request_too_large` | лимит токенов бесплатного тарифа; уменьшить `MAX_INPUT_TOKENS` (например, 6000) |
| 502 `llm_empty_output` | рассуждающая модель исчерпала `MAX_OUTPUT_TOKENS` — увеличить |
| 502 `llm_auth_failed` | ключ отозван/неверный |
| 429 `daily_limit_exceeded` | демо-лимит 10 запросов/день на пользователя (`DAILY_RUN_LIMIT_PER_USER`) |
| «Пропали» файлы и история | бесплатный инстанс перезапустился; без постоянного диска это ожидаемо |

## Альтернативы

- **Локально с Qwen через Ollama** — без ключей и аккаунтов, см. FREE_LLM_PROVIDERS.md, вариант 3
  (нужен компьютер ~8 GB RAM; Render Free для Ollama мал).
- **GitHub Codespaces** — для разработчиков (веб-IDE с терминалом); порт по умолчанию приватный.
- **Google Cloud Run / Railway / Fly.io** — требуют привязки карты; для постоянного стенда с PostgreSQL
  это следующий шаг.
