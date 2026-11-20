# Бесплатные LLM-провайдеры: подключение, ограничения, возрастные требования

AI Engine переключается между провайдерами одной переменной `LLM_PROVIDER`; код агентов не меняется.

| `LLM_PROVIDER` | Что это | Стоимость | Ключ | Минимальный возраст (условия сервиса) |
|---|---|---|---|---|
| `mock` | офлайн-заглушка, **не ИИ** (ответы помечены MOCK) | 0 | не нужен | — |
| `groq` | Groq API, план Free | 0 (Free plan, без карты) | `GROQ_API_KEY` | **18+** |
| `gemini` | Google Gemini API, Free Tier | 0 (проект без биллинга) | `GEMINI_API_KEY` | **18+**, только профессиональное/бизнес-использование |
| `openrouter` | OpenRouter, только модели `:free` | 0 | `OPENROUTER_API_KEY` | **18+** |
| `ollama` | локальная модель (Qwen) на своём компьютере | 0 (ваше железо) | не нужен | аккаунт не нужен |
| `openai` | OpenAI | **платно** | `OPENAI_API_KEY` | 13+ (до 18 — с согласия родителей) |
| хостинг: Render Free | запуск сервера в облаке | 0 | — | **16+** |

> **Если участнику команды нет 18 лет**, он не может сам регистрироваться в Groq, Google Gemini API и
> OpenRouter — ключ должен получить совершеннолетний участник/ментор на себя. Без ограничений по
> возрасту работают `mock` и `ollama`. Render допускает пользователей с 16 лет.

Сведения проверены по официальной документации 20.11.2026 (источники — в конце). Условия бесплатных
тарифов меняются часто: перед демо сверяйтесь с консолью провайдера.

## Как движок защищает нулевой бюджет

- **Нет автоматического переключения.** Провайдер и модель задаются только конфигурацией. При ошибках,
  исчерпании лимита или недоступности модели движок **не** переходит на другую модель/провайдера
  (тест `test_daily_quota_is_not_retried_and_never_switches_model`).
- **Allowlist бесплатных моделей.** Для `groq`, `gemini`, `openrouter` модель должна быть в списке
  бесплатных (встроенном или явно заданном `LLM_ALLOWED_MODELS`), иначе провайдер не используется,
  `/health` показывает `llm_problem: llm_model_not_allowed`, запросы не отправляются.
- **OpenRouter — только явно выбранные `:free` модели.** Модель без суффикса `:free` и роутеры
  (`openrouter/auto`, `openrouter/free`) отклоняются всегда, даже через `LLM_ALLOWED_MODELS`.
  Список запасных моделей (`models`) не отправляется никогда.
- **OpenAI** — единственный платный провайдер, включается только явно `LLM_PROVIDER=openai`.
  Для бесплатных провайдеров `estimated_cost` всегда `0.0` и лимиты расходов их не резервируют.
- Биллинг ни у одного провайдера движок не подключает. Не привязывайте карту к Google Cloud-проекту
  с ключом Gemini: после включения биллинга запросы становятся платными.

## Обработка лимитов бесплатных тарифов

| Ситуация | Поведение | Ответ клиенту |
|---|---|---|
| Свой «предохранитель» движка: `LLM_REQUESTS_PER_MINUTE` / `LLM_REQUESTS_PER_DAY` (по умолчанию — ниже лимитов тарифа) | запрос к провайдеру **не отправляется** | 429 `llm_rate_limited` / `llm_free_quota_exhausted` + `Retry-After` |
| Провайдер вернул 429 «в минуту», просит подождать ≤ `LLM_MAX_RETRY_AFTER_SECONDS` (20 с) | ждём указанное время и повторяем (не более `LLM_MAX_RETRIES`) | обычный ответ |
| 429 «в минуту» с ожиданием > 20 с | без повторов | 429 `llm_rate_limited` + `Retry-After` |
| 429 дневная квота (Groq TPD/RPD, Gemini `PerDay`, OpenRouter `free-models-per-day`) | без повторов, без смены модели | 429 `llm_free_quota_exhausted` + `Retry-After` |
| 413 / «Request too large» (лимит токенов в минуту на бесплатном тарифе) | без повторов | 413 `llm_request_too_large` — уменьшите `MAX_INPUT_TOKENS` |
| Модель не поддерживает `response_format` | один повтор без него, JSON требуется промптом | ответ + предупреждение `json_mode_prompt_fallback` |
| Модель не поддерживает `reasoning_effort` | один повтор без параметра | ответ + `reasoning_effort_not_supported` |
| Ollama не запущена | — | 502 `llm_unavailable` («Is Ollama running?») |

Счётчики движка живут в памяти процесса: после перезапуска они обнуляются, а при нескольких репликах
считаются отдельно — окончательный лимит всё равно контролирует провайдер (его 429 обрабатывается).

## Совместимость структурированных ответов (support, hr)

Агенты `support` и `hr` ждут JSON. Для всех провайдеров движок: отправляет
`response_format: {"type": "json_object"}` (при отказе модели — переходит на JSON по промпту);
удаляет блоки рассуждений `<think>…</think>` (Qwen3, DeepSeek-R1); принимает JSON в ```json-ограждении
и с текстом вокруг; проверяет результат на сервере (источники support, цитаты HR). Пустой ответ
модели (вся квота токенов ушла на рассуждения) → `502 llm_empty_output`. Проверено автотестами для
всех четырёх бесплатных провайдеров на имитации их API (`tests/test_llm_providers.py`).
**Качество реальных ответов этих моделей в среде разработки не проверялось** (нет ключей, а реестры
моделей Ollama/Hugging Face недоступны из песочницы).

## Пошаговый бесплатный запуск всех трёх агентов

### Вариант 1. Облако, без ключей и без терминала (mock) — 5 минут

Показывает интерфейс, API, загрузку файлов, поиск по базе знаний (support отвечает цитатами из
документа), разбор резюме (HR — локальный разбор без ИИ). Ответы помечены «MOCK — не ИИ».

1. Render → Sign in with GitHub → **New → Blueprint** → репозиторий `bigfigboy/marketplace-ai-engine`,
   рабочая ветка. В `render.yaml` уже `LLM_PROVIDER=mock`.
2. Render спросит только `DEMO_API_KEY`: `aie_` + 32+ случайных букв/цифр (из менеджера паролей).
3. **Apply**. После сборки откройте `https://<имя>.onrender.com/health` (`"llm_provider": "mock"`,
   `"status": "ok"`), затем `/demo/`, введите ключ и проверьте агентов (сценарий — в
   [CLOUD_DEPLOY.md](CLOUD_DEPLOY.md), шаг 2).

### Вариант 2. Облако + настоящая бесплатная модель (нужен совершеннолетний владелец ключа)

Сделайте вариант 1, затем в Render → сервис → **Environment** добавьте переменные одного провайдера и
нажмите **Save** (сервис перезапустится).

**Groq (рекомендуется первым: быстрый, без карты, данные не используются для обучения):**
1. https://console.groq.com → регистрация → **API Keys → Create API Key**.
2. В Render: `LLM_PROVIDER=groq`, `GROQ_API_KEY=<ключ>`, при желании `GROQ_MODEL=openai/gpt-oss-120b`
   (по умолчанию `openai/gpt-oss-20b`).
3. Лимиты своего аккаунта смотрите в console.groq.com → Settings → Limits; при необходимости задайте
   `LLM_REQUESTS_PER_DAY` / `LLM_REQUESTS_PER_MINUTE` ниже их.

**Google Gemini (Free Tier):**
1. https://aistudio.google.com → **Get API key** → создать ключ в проекте **без** платёжного аккаунта.
2. В Render: `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=<ключ>` (модель по умолчанию
   `gemini-3.5-flash-lite`; также разрешены `gemini-3.8-flash`, `gemini-3.1-flash-lite`).
3. Лимиты — в AI Studio (на проект). По умолчанию движок ограничивает себя 10 запросами в минуту и
   20 в день (по опубликованному пользователями лимиту 3.8 Flash); если в AI Studio у вас больше —
   поднимите `LLM_REQUESTS_PER_DAY`.
4. ⚠️ На бесплатном тарифе Google может использовать запросы и ответы для улучшения продуктов, их
   могут читать люди. Используйте только вымышленные данные.

**OpenRouter (бесплатные модели):**
1. https://openrouter.ai → регистрация → **Keys → Create Key**. Не покупайте кредиты (без них лимит
   50 запросов в день; после разовой покупки от $10 — 1000 в день).
2. Settings → Privacy: решите, разрешать ли провайдерам бесплатных моделей логировать запросы
   (некоторые бесплатные модели доступны только при разрешении). Используйте только вымышленные данные.
3. В Render: `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY=<ключ>`, при желании
   `OPENROUTER_MODEL=<модель>:free` из разрешённых (`google/gemma-4-26b-a4b-it:free` по умолчанию,
   `openai/gpt-oss-120b:free`, `nvidia/nemotron-3-ultra-550b-a55b:free`). Другую `:free` модель с
   https://openrouter.ai/models?max_price=0 можно разрешить явно через `LLM_ALLOWED_MODELS`.

Проверка после переключения: `/health` → `"llm_provider": "<имя>"`, `"llm_billing": "free"`,
`"llm_configured": true`; в демо у ответа чип `<имя> · <модель>` и стоимость `0.000000`.

Для рассуждающих моделей (Gemini 3.x, gpt-oss, qwen3) в `render.yaml` уже `MAX_OUTPUT_TOKENS=4000`.

### Вариант 3. Локально с Qwen через Ollama (без ключей и без возрастных ограничений)

Нужен компьютер с ~8 GB RAM для `qwen3:4b` (загрузка ~2.5 GB); на слабом — `qwen3:1.7b` (~1.4 GB).
Render Free (512 MB) для Ollama не подходит.

1. Установите Ollama: https://ollama.com/download (Windows/macOS — приложение, Linux — пакет).
2. Скачайте модель: в приложении Ollama выберите `qwen3:4b`, или в терминале `ollama pull qwen3:4b`.
3. Запустите движок (README → «Быстрый старт») с `.env`:
   ```
   LLM_PROVIDER=ollama
   OLLAMA_MODEL=qwen3:4b
   LLM_TIMEOUT_SECONDS=180
   REQUEST_TIMEOUT_SECONDS=300
   MAX_OUTPUT_TOKENS=4000
   ```
   или через Docker: `docker compose --profile ollama up --build`, затем
   `docker compose exec ollama ollama pull qwen3:4b`.
4. Откройте http://localhost:8000/demo/. На CPU ответ может занимать 30–120 секунд.

Движок отправляет Qwen `reasoning_effort: "none"` (отключает «мышление»); если модель всё же выводит
`<think>`, блок удаляется. Лицензия Qwen3 — Apache 2.0, Ollama — MIT; модель скачивается
пользователем и не входит в репозиторий.

## Ограничения бесплатных тарифов (на 20.11.2026)

| Провайдер | Лимиты | Прочее |
|---|---|---|
| Groq Free | на аккаунт и модель; точные цифры — в console.groq.com/settings/limits (в документации опубликованы только лимиты Developer-плана, напр. gpt-oss-120b: 30 RPM / 1K RPD / 8K TPM / 200K TPD; лимиты Free-плана там не указаны). Ошибка 429 с `retry-after` | модели `llama-3.x` выведены 16.08.2026; данные не используются для обучения, логи до 30 дней для диагностики |
| Gemini Free Tier | на проект (не на ключ), смотрятся в AI Studio; сброс дневных квот в полночь по тихоокеанскому времени; сообщество сообщает ~20 запросов/день для 3.8 Flash; Google снижает бесплатные лимиты | Pro-модели без бесплатного тарифа; модели 2.5 доступны только прежним пользователям; Казахстан в списке поддерживаемых стран; данные бесплатного тарифа могут использоваться Google |
| OpenRouter `:free` | 20 запросов/мин (общие на все `:free` модели), 50/день (1000/день после разовой покупки ≥ $10); неудачные запросы тоже считаются; отрицательный баланс блокирует и бесплатные модели | состав бесплатных моделей часто меняется; часть бесплатных провайдеров логирует запросы |
| Ollama | ограничено вашим железом | без сети после загрузки модели |
| Render Free | 512 MB RAM, 0.1 CPU, 750 ч/мес на workspace, «засыпает» после 15 мин без запросов (~1 мин на пробуждение), диск не сохраняется | на официальных страницах карта не требуется, но служба поддержки упоминает возможную проверку карты ($1 авторизация) |

## Не удалось проверить (сверьте перед демо)

- Точные бесплатные лимиты Groq и Gemini для конкретных моделей (видны только в консоли аккаунта).
- Поддержку `json_object` и `max_tokens` в OpenAI-совместимом endpoint Gemini — движок подстрахован
  переходом на JSON по промпту.
- Доступность Groq из Казахстана (официального списка стран не найдено).
- Актуальность ID бесплатных моделей OpenRouter и превью-модели Groq `qwen/qwen3.8-27b`.
- Поведение «мышления» qwen3 через `/v1` в конкретной версии Ollama.
- Можно ли оставить пустым `sync: false` значение при создании Blueprint в Render.

## Источники

Groq: console.groq.com/docs/models, /docs/deprecations, /docs/rate-limits, /docs/openai,
/docs/structured-outputs, /docs/your-data, /docs/legal/services-agreement · Gemini: ai.google.dev/gemini-api/docs/models,
/pricing, /rate-limits, /openai, /available-regions, /billing, ai.google.dev/gemini-api/terms ·
OpenRouter: openrouter.ai/pricing, /docs/api_reference/limits, /docs/guides/routing/model-variants/free,
/docs/guides/routing/provider-selection, /docs/guides/privacy/data-collection, /terms · Ollama:
docs.ollama.com/api/openai-compatibility, ollama.com/library/qwen3, github.com/ollama/ollama (LICENSE) ·
Render: render.com/docs/compute-plans, /docs/faq, /docs/blueprint-spec, render.com/terms ·
OpenAI: openai.com/policies/row-terms-of-use.
