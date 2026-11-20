# Test Report

Дата: 20.11.2026 (итерация 6: подготовка к интеграции с Backend 2; итерация 5 — исправление анализа резюме HR Assistant; итерация 4 — загрузка PDF/DOCX на Windows 11; итерация 3 — мультипровайдерный LLM-слой). Все результаты ниже — фактический вывод
команд в среде разработки.

## Важно: что проверено, а что нет

- **Ни один реальный LLM-провайдер (OpenAI, Groq, Gemini, OpenRouter) не вызывался**, и реальная модель
  Qwen через Ollama не запускалась: ключей нет, а ollama.com / registry.ollama.ai / huggingface.co закрыты
  сетевой политикой песочницы. Провайдеры проверены тем же способом, что и OpenAI (ниже).
- **Реальный OpenAI API не вызывался** — в среде разработки не было `OPENAI_API_KEY`, а отправлять
  данные во внешние API без ключа и разрешения нельзя. Поэтому:
  - логика агентов проверена с `ScriptedLLM` (тестовый двойник, который записывает точные сообщения,
    отправляемые в модель, и возвращает заданные ответы в формате модели);
  - интеграция с OpenAI проверена **через официальный SDK `openai`** с `httpx.MockTransport`, имитирующим
    endpoint Chat Completions: формирование запроса, `max_completion_tokens`, `response_format`,
    разбор `usage`, ретраи, таймауты, коды ошибок;
  - качество текстов реальной модели (в т.ч. казахский язык) нужно оценить вручную после подключения ключа.
- Mock-режим (`LLM_MODE=mock`) явно помечается в ответе (`mode: "mock"`, баннер «не является результатом
  работы ИИ») и используется только для проверки архитектуры.
- Все резюме и документы в тестах — вымышленные.

## Команды и результаты

### Основной набор (SQLite, Python 3.13.18)

```
$ cd ai-engine && python -m pytest
128 passed in 27.88s        # 94 прежних + 34 новых (tests/test_llm_providers.py)
```

### Тот же набор на PostgreSQL 16 (локальный сервер)

```
$ TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/ai_engine_test python -m pytest
128 passed in 30.36s
```

### Другие версии Python

```
Python 3.11.18 (uv venv):              128 passed in 26.71s
Python 3.12.16 (внутри Docker-образа): 128 passed in 36.75s
```

### Линтер и аудит зависимостей

```
$ ruff check .
All checks passed!
$ pip-audit -r requirements.txt
No known vulnerabilities found
```

### Docker

- `docker build` — успешно (в песочнице сборка выполнялась с CA-сертификатом прокси песочницы,
  переданным как build-secret; сам `Dockerfile` не менялся).
- Контейнер запущен в mock-режиме: `/health` → `ok`, `POST /agents/support/run` → корректный ответ,
  процесс работает от пользователя `engine` (uid 10001); после исправлений образ пересобран, загрузка
  PDF и DOCX (изолированный процесс разбора) внутри контейнера → 201.
- Облачный деплой (Render, `render.yaml`) **не выполнялся** — нужен аккаунт и разрешение команды;
  `render.yaml` проверен только на корректность YAML.
- `docker compose` с профилем `postgres` **не запускался**.

### Ручная проверка демо-интерфейса (Playwright, Chromium, mock-режим)

Сценарий: ввод ключа → загрузка базы знаний → вопрос support (ответ с источником) → вопрос вне
базы (`escalation_required`, черновик обращения «не отправлен») → загрузка вымышленного резюме →
сводка HR → пост для marketing на казахском. Ошибок в консоли браузера нет; ширина 390 px — без
горизонтальной прокрутки. Скрипт `examples/curl_examples.sh` пройден целиком против запущенного сервера.

## Подготовка к интеграции с Backend 2 (итерация 6)

Логика движка не менялась. Добавлены интеграционные документы (`AI_ENGINE_HANDOFF.md`, `API_CONTRACT.md`,
`SUPABASE_INTEGRATION.md`, `INTEGRATION_CHECKLIST.md`) и 17 контрактных тестов
`tests/test_backend2_contract.py`: сырой токен Supabase отклоняется; engine token от Backend 2 проходит
полный путь (агенты → загрузка KB → support → `GET /runs`), ключи ответа зафиксированы; сопоставление ролей
(`business/developer/authenticated` → `member`); изоляция по `company_id` (один `sub` в двух бизнесах);
просроченный токен, чужие `aud`/`iss`/подпись, нет `exp`/`company_id` → 401; формат ошибок с `run_id`,
`Retry-After`; `kk` принимается, `kz` → 422.

Проверено вручную на PostgreSQL 16: при `DATABASE_URL=...?options=-csearch_path%3Dai_engine` все таблицы
движка создаются в схеме `ai_engine`, существующая `public.agent_runs` (имитация Backend 2) не затронута,
запуск агента записывается в `ai_engine.agent_runs`.

```
Linux (локально, Python 3.13):                      169 passed, 3 skipped (Windows-only)
Windows (GitHub Actions, Python 3.12.10):           171 passed, 1 skipped (Unix-only)
Linux (GitHub Actions ubuntu-latest, Python 3.12):  успешно
```
Прогон: https://github.com/Bigfigboy/marketplace-ai-engine/actions/runs/44835027263

## HR Assistant: шаблон «Требования вакансии» вместо анализа резюме (итерация 5)

**Симптом (Windows 11, Ollama qwen3:1.7b):** на запрос «Проанализируй загруженное резюме…» агент
возвращал «Требования вакансии / Не указано в вакансии / `<what the posting does not specify>`» со
статусом `success`.

**Причина (цепочка, воспроизведена через API):**
1. В демо тип загружаемого файла по умолчанию — «База знаний»; DOCX сохранялся как `knowledge_base`.
2. Для HR такие файлы не подходят, и `demo/app.js` молча исключал их — в `file_ids` уходил пустой список.
3. `HRAgent._detect_task` без резюме проваливался в `job_requirements` и подставлял **само сообщение
   пользователя** как текст вакансии; текст DOCX до модели не доходил.
4. qwen3:1.7b копировала JSON-шаблон из промпта; сервер не проверял содержательность ответа → `success`.

**Исправление:** запрос про резюме без файла резюме → `422 resume_required` (понятный текст, LLM не
вызывается); короткое сообщение не считается вакансией (`422 job_description_required`), неясный
запрос → `422 hr_task_unclear`; строки-заглушки `<...>` и пустые элементы удаляются, ответ без
содержания → `502 llm_invalid_output`; сводка резюме дополняется фактами, которые локальный парсер
находит в документе дословно (навыки с цитатами, образование, опыт), выдуманные навыки и решения о
найме по-прежнему удаляются; в демо тип файла переключается под выбранного агента и выводится
предупреждение, если файлы не передаются агенту.

```
Linux (локально, Python 3.13):                      152 passed, 3 skipped (Windows-only)
Windows (GitHub Actions, Python 3.12.10):           154 passed, 1 skipped (Unix-only)
Linux (GitHub Actions ubuntu-latest, Python 3.12):  успешно
```

Прогон: https://github.com/Bigfigboy/marketplace-ai-engine/actions/runs/44831313649. Новые тесты —
`tests/test_hr_resume_analysis.py` (10): вымышленный DOCX с Python, FastAPI, SQL, Git и образованием;
исходный сценарий бага (DOCX как «База знаний», `file_ids=[]`) → 422; эхо шаблона qwen3:1.7b; формат
ответа Ollama qwen3 (`<think>` + ```json) через SDK на имитации endpoint; выдуманные навыки и «рекомендую
нанять» удаляются; mock-режим. На старом коде 5 из них падают. Браузерная проверка демо (Playwright):
выбор HR переключает тип файла на «Резюме», при файле «База знаний» — предупреждение и ошибка 422,
при «Резюме» — `file_ids` содержит файл, в ответе Python, FastAPI, SQL, Git и вуз. Реальная модель
qwen3:1.7b в среде разработки не запускалась (реестр моделей Ollama недоступен из песочницы).

## Windows 11: ошибка загрузки PDF/DOCX (итерация 4)

**Симптом (ручное тестирование, Windows 11, Python 3.12):** любой PDF, даже на 1.5 КБ, давал
`document_too_complex: The document needs too much memory or cannot be processed.`

**Точная причина** (`app/services/extract_worker.py`, прежняя версия): воркер выполнял `import resource`
до блока обработки ошибок. Модуль `resource` существует только в Unix, поэтому на Windows воркер падал
с `ModuleNotFoundError`, stdout оставался пустым, а родитель (`extraction._extract_isolated`) трактовал
любой нечитаемый ответ как `document_too_complex`. Вторая, скрытая ошибка: воркер писал JSON с
кириллицей в stdout, а на Windows канал кодируется ANSI-кодовой страницей (cp1251/cp1252): казахские
буквы (`Қ`, `ә`, `₸`) роняли воркер (`UnicodeEncodeError`), русский текст приходил в cp1251 и не
декодировался как UTF-8. Обе причины воспроизведены на Linux имитацией Windows (блокировка `resource`
через `sitecustomize`, `PYTHONIOENCODING=cp1251`); новые тесты падают на старом коде (14 из 14) и
проходят на исправленном. Дополнительно найдено: файлы сохранялись `os.open` без `O_BINARY` — на Windows
CRT заменяет `\n` на `\r\n` и портит сохранённый оригинал.

**Исправление:** лимит памяти по платформе — `RLIMIT_AS` на Unix и **Job Object c `ProcessMemoryLimit`** на
Windows (защита не отключена); ASCII-JSON в stdout байтами; JSON-ответ при любом сбое; явное окружение
воркера (`PYTHONPATH`, UTF-8), без консольного окна на Windows; сбой воркера → `500
document_processing_failed` (а не «документ слишком сложный»); `O_BINARY` при записи; импорт FastAPI в
`app/core/errors.py` ленивый (старт воркера ~0.05 с вместо ~0.3 с). Таймаут, лимит памяти, проверки
ZIP-бомб и DoS-тесты (`tests/test_review_regressions.py`) — без изменений и проходят.

```
Linux, Python 3.13:  142 passed, 3 skipped (Windows-only)
Linux, Python 3.11:  142 passed, 3 skipped
PostgreSQL 16:       142 passed, 3 skipped
Windows Server (GitHub Actions windows-latest, Python 3.12.10): 144 passed, 1 skipped (Unix-only)
Linux (GitHub Actions ubuntu-latest, Python 3.12):              успешно
```

Прогон: https://github.com/Bigfigboy/marketplace-ai-engine/actions/runs/44827478440. На Windows
выполнены и прошли Windows-only тесты: Job Object реально вызывает `MemoryError` при попытке выделить
512 MB под лимитом 128 MB; воркер сообщает `memory_limit = job_object`; PDF ~1.5 КБ и DOCX с казахским
текстом загружаются через API (201). Предыдущий прогон выявил одну ошибку в ожидании теста (имитация
«нет `resource`» на настоящей Windows даёт `job_object`, а не `unavailable`) — исправлено в тесте.
Ручная проверка на Windows 11 у команды (PowerShell, из папки `ai-engine`):

```powershell
python -m pytest tests/test_windows_compat.py -q     # ожидается: 16 passed, 1 skipped
python -m pytest -q                                  # весь набор: 144 passed, 1 skipped
```

## Мультипровайдерный LLM-слой (итерация 3)

Все 34 теста в `tests/test_llm_providers.py` используют официальный SDK `openai` с `httpx.MockTransport`,
который имитирует OpenAI-совместимый API каждого провайдера, включая их форматы ошибок лимитов.
Ключи фиктивные, данные вымышленные.

| Требование | Тест(ы) | Результат |
|---|---|---|
| Единый интерфейс, переключение без изменения агентов | `test_agents_work_unchanged_with_free_providers[groq/gemini/openrouter/ollama]` — все три агента через API | ✅ |
| Корректный запрос каждому провайдеру (URL, модель, ключ, `max_tokens`/`max_completion_tokens`, `reasoning_effort`, заголовки OpenRouter, без `models`) | `test_each_provider_sends_openai_compatible_request[*]` (5 провайдеров) | ✅ |
| Конфигурация через env, legacy `LLM_MODE`, нет ключа → 503 с именем переменной | `test_build_provider_from_settings`, `test_llm_model_override_and_custom_ollama_url`, `test_health_reports_provider` | ✅ |
| OpenRouter — только явно выбранные `:free`; роутеры и платные запрещены даже через allowlist | `test_openrouter_only_explicit_free_models` | ✅ |
| Allowlist бесплатных моделей Groq/Gemini | `test_free_providers_reject_models_outside_allowlist` | ✅ |
| Нет автоматического переключения при исчерпании квоты | `test_daily_quota_is_not_retried_and_never_switches_model` (1 запрос, та же модель) | ✅ |
| Лимиты бесплатных тарифов: `Retry-After`, Gemini `retryDelay`, Groq «try again in 7m12.5s», дневные квоты, предохранитель RPM/RPD движка, 413 | `test_short_retry_after_is_honored`, `test_long_retry_after_fails_fast_with_header`, `test_groq_daily_token_limit_message`, `test_gemini_resource_exhausted_retry_delay`, `test_gemini_daily_quota_detected`, `test_engine_side_guard_stops_before_provider_limit`, `test_request_too_large_for_free_tier`, `test_free_quota_error_reaches_client_with_retry_after` | ✅ |
| Платный OpenAI без «бесплатного» предохранителя; стоимость бесплатных = 0 | `test_paid_openai_has_no_engine_side_free_guard`, `test_agents_work_unchanged_with_free_providers` | ✅ |
| JSON-совместимость: чистый JSON, ```json, `<think>` (Qwen), текст вокруг; отказ от `response_format` → JSON по промпту; отказ от `reasoning_effort` | `test_json_output_variants_parse[*]`, `test_strip_reasoning`, `test_json_mode_unsupported_falls_back_to_prompt_mode`, `test_reasoning_effort_dropped_when_unsupported`, `test_reasoning_effort_can_be_disabled` | ✅ |
| Ollama не запущена → понятная ошибка | `test_ollama_not_running` | ✅ |
| Render в mock без ключей | `test_render_mock_mode_needs_no_api_key`; Docker-контейнер с переменными из `render.yaml` (без ключей): `/health` → ok, все три агента → `success`, `mode: mock`, стоимость 0; с `LLM_PROVIDER=groq` без ключа → `degraded`, `llm_problem: llm_not_configured` | ✅ |
| Безопасность, авторизация, изоляция без изменений | все 94 прежних теста (в т.ч. изоляция, лимиты, инъекции, HR) | ✅ |

Новые тесты нашли и помогли исправить два бага: неверный разбор «7m12.5s» из сообщения Groq и запись
ошибок лимита провайдера как `rejected` (такие запуски не учитывались в квоте, хотя запрос к модели
уже был отправлен). Теперь они записываются как `error`.

Условия бесплатных тарифов, ID моделей и возрастные требования сверены по официальной документации на
20.11.2026 через поиск по официальным доменам (прямое чтение страниц console.groq.com и ai.google.dev
было недоступно). Источники и список непроверенного — в `docs/FREE_LLM_PROVIDERS.md`.

## Покрытие требований ТЗ

| Требование ТЗ | Тест(ы) | Результат |
|---|---|---|
| Marketing Agent RU | `test_marketing.py::test_marketing_ru` | ✅ |
| Marketing Agent KK | `test_marketing.py::test_marketing_kk_autodetect` | ✅ |
| Support Agent с документами | `test_support.py::test_support_answers_from_documents_with_sources`, `test_support_mock_mode_extractive_answer` | ✅ |
| Support Agent без информации | `test_support_no_information_escalates_without_llm_call`, `test_support_without_knowledge_base`, `test_support_model_escalation_and_no_invented_terms` | ✅ |
| Support + prompt injection | `test_support_prompt_injection_in_document_is_neutralized`, `test_support_prompt_injection_in_user_message_is_blocked`, `test_support_answer_without_valid_sources_is_rejected`, `test_support_removes_false_handoff_claims`, `test_security.py::test_prompt_guard_patterns` | ✅ |
| HR с вымышленным резюме | `test_hr.py::test_hr_resume_summary_fictional_docx`, `test_hr_resume_pdf_mock_mode`, `test_hr_requirements_check_shows_confirmed_and_missing`, `test_hr_interview_questions_and_job_description` | ✅ |
| Ограничения HR | `test_hr_refuses_ranking_decisions_and_protected_attributes`, `test_hr_one_resume_per_run`, `test_hr_strips_decision_sentences_from_output`, `test_hr_allows_inclusive_wording` | ✅ |
| Загрузка PDF | `test_files.py::test_upload_pdf`, `test_image_only_pdf_requires_ocr` | ✅ |
| Загрузка DOCX | `test_upload_docx_with_table_and_chunks` | ✅ |
| Неверный формат файла | `test_invalid_extension_rejected`, `test_extension_content_mismatch_rejected`, `test_corrupted_and_empty_files` | ✅ |
| Лимит размера 10 MB | `test_default_limit_is_10mb`, `test_file_size_limit` | ✅ |
| Превышение лимита запросов | `test_limits.py::test_daily_request_limit_per_user`, `test_rate_limit_per_minute`, `test_failed_auth_attempts_are_throttled` | ✅ |
| Лимиты расходов и токенов | `test_company_spend_limit`, `test_global_spend_limit`, `test_message_length_and_input_token_limits`, `test_output_token_limit_is_passed_to_provider` | ✅ |
| Отсутствующий API-ключ | `test_llm_errors.py::test_missing_api_key_returns_503_and_logs_run` | ✅ |
| Ошибка LLM API | `test_llm_api_error_through_the_api`, `test_retry_is_bounded`, `test_non_retryable_errors[*]`, `test_timeout_maps_to_504`, `test_deadline_stops_retries` | ✅ |
| Доступ к файлу чужой компании | `test_security.py::test_cross_company_file_access_denied`, `test_support_does_not_retrieve_other_company_documents`, `test_private_files_isolated_between_users_of_same_company` | ✅ |
| Запись логов в agent_runs | `test_runs_and_architecture.py::test_agent_runs_logging` (поля, токены, стоимость; текст запроса не попадает ни в БД, ни в логи), `test_runs_listing_and_stats` | ✅ |
| Миграции | `test_migrations_create_schema` | ✅ |
| Подключение нового агента без изменения API | `test_new_agent_plugs_into_registry_without_api_changes`, `test_registry_refuses_untrusted_modules` | ✅ |
| Аутентификация / JWT / CORS / ошибки | `test_security.py::*` | ✅ |

## Независимое ревью кода (итерация 2)

Ревью проводил отдельный агент без доступа к моим выводам; каждая находка подтверждена воспроизведением.
Подтверждено **отсутствие** утечек между компаниями и обходов аутентификации. Найдено и исправлено:

| # | Серьёзность | Проблема | Исправление | Регрессионный тест (`tests/test_review_regressions.py`) |
|---|---|---|---|---|
| 1 | High | Параллельные запросы обходили дневной лимит и лимиты расходов (17 из 20 запросов при лимите 3) | Атомарная проверка лимитов + запись запуска `running` (блокировка процесса + advisory lock PostgreSQL), резервирование худшей стоимости | `test_parallel_requests_cannot_exceed_daily_limit` (12 параллельных → ровно 3 вызова LLM), `test_parallel_requests_cannot_exceed_spend_limit` |
| 2 | High | Резюме, загруженное как `job_description`, уходило в LLM без маскирования ПДн и обходило правило «одно резюме» | Маскирование всех текстов HR; файл-вакансия, похожий на резюме, отклоняется; не более 1 файла вакансии | `test_resume_disguised_as_job_description_is_rejected`, `test_job_description_text_is_redacted` |
| 3 | High | PDF 35 KB занимал CPU на десятки минут (DoS) | Разбор PDF/DOCX в отдельном процессе с таймаутом и лимитом памяти, остановка после `MAX_EXTRACTED_CHARS` | `test_pdf_bomb_is_bounded` |
| 3b | High | DOCX 0.3 MB → 1.66 GB памяти и 88 с | Лимиты: `document.xml` ≤ 20 MB, распаковка ≤ 100× размера файла; изолированный процесс | `test_docx_bomb_is_rejected` |
| 4 | Medium | Проверка навыков HR принимала выдуманные цитаты («Java» в «JavaScript», первые 40 символов) | Цитата должна целиком быть в резюме и содержать навык как отдельное слово | `test_skill_verification_rejects_invented_evidence`, `test_hr_summary_drops_substring_skills` |
| 5 | Medium | Защита от инъекций и HR-политика не проверяли всю историю и поля `job_title`/`job_description` | Проверяются все реплики истории (любой роли) и все HR-опции | `test_guards_check_full_history_and_hr_options` |
| 6 | Medium | Запросы без `Content-Length` (chunked) обходили лимит размера | Потоковый ASGI-лимит тела запроса, ответ 413 | `test_chunked_bodies_are_limited` |
| 7 | Low | Повреждённый DOCX вызывал HTTP 500 | Любая ошибка разбора ZIP → 422 | `test_malformed_docx_is_422` |
| — | (не подтверждено, по коду) | Гонка при первом входе одного пользователя по JWT → 500 | Повтор при `IntegrityError` | — |
| — | Готовность к OpenAI | Пустой ответ модели (например, рассуждающая модель исчерпала лимит) считался успехом | `502 llm_empty_output` | `test_empty_model_output_is_an_error` |

После исправлений сценарии DoS проверены вручную: PDF-бомба (200 страниц, 35 KB) — обработка остановлена
по лимиту текста за 9.3 с в дочернем процессе, память родительского процесса не выросла (224 MB);
DOCX-бомба (289 KB) отклонена мгновенно (`document_too_complex`).

## Проверка изоляции данных компаний

Матрица (ключи admin и member компании B против файлов базы знаний, резюме и запуска компании A):
`GET /files/{id}`, `DELETE /files/{id}`, запуск агента с чужим `file_id`, `GET /runs/{id}` → **404 во всех
случаях**; `GET /files` и `GET /runs` компании B не содержат объектов A; support-агент компании B на
вопрос, ответ на который есть только в базе A, → `escalation_required`, 0 источников. Внутри компании:
резюме участника недоступно другому участнику (404), доступно admin (200). Автотесты:
`test_cross_company_file_access_denied`, `test_private_files_isolated_between_users_of_same_company`,
`test_runs_isolated_between_companies_and_users`, `test_support_does_not_retrieve_other_company_documents`,
`test_jwt_from_marketplace` (одинаковый `sub` в разных компаниях — разные пользователи).

## Проверка защиты API-ключей

- В git (все коммиты) нет реальных ключей: найдены только заведомо фиктивные тестовые строки
  (`sk-test-not-a-real-key`, `aie_invalid_key_…`); `.env` не отслеживается, есть в `.gitignore` и `.dockerignore`.
- `OPENAI_API_KEY`, `JWT_SECRET`, `DEMO_API_KEY` — `SecretStr`: не видны в `repr` настроек (проверено).
- Логи: строки вида `sk-…`/`aie_…` и поля `authorization`, `openai_api_key` маскируются (проверено
  форматтером логов); тексты запросов и ответов не логируются (`test_agent_runs_logging`).
- Ключи движка хранятся только как SHA-256; отозванный ключ → 401 (`test_revoked_key_rejected`);
  перебор ключей ограничен 20 неудачами в минуту с IP (`test_failed_auth_attempts_are_throttled`).
- Ключ OpenAI не попадает в образ Docker (передаётся переменной окружения/секретом платформы).

## Готовность к переходу на настоящий OpenAI API

Проверено (без реального ключа): запрос формируется официальным SDK в формате Chat Completions
(`model` из `OPENAI_MODEL`, `max_completion_tokens`, `response_format: json_object` для support/hr,
`temperature` только если задан), промпты support/hr содержат слово «JSON» (требование JSON-режима),
`usage` разбирается, ошибки 401/404/400/429/5xx/таймаут маппятся в понятные коды, ретраи ограничены,
пустой ответ — ошибка, отсутствие ключа — `503 llm_not_configured`.
**Не проверено:** фактические ответы модели, их качество (особенно на казахском), задержки и фактическая
стоимость — это первый шаг после подключения ключа (инструкция в `INTEGRATION_GUIDE.md` §1 и `CLOUD_DEPLOY.md`).

## Найденные и исправленные ошибки в итерации 1

1. Тест статики демо: путь `/demo/../data/...` нормализуется клиентом и получает 401 (защищённый путь) —
   ожидание теста расширено до «401 или 404, содержимое файла не отдаётся».
2. HR mock-режим: заголовок кандидата брался из замаскированной строки, пункты опыта не группировались
   по должностям — исправлено.
3. Демо: чекбокс файла, неподходящего выбранному агенту, отображался отмеченным — исправлено.
4. Безопасность: любой участник компании мог загрузить документ в общую базу знаний (риск «отравления»
   ответов support) — загрузка `knowledge_base` ограничена ролью `admin`, добавлен тест.
5. Dockerfile: убрано доверие `X-Forwarded-*` от любого источника (`--forwarded-allow-ips *`), из-за
   которого можно было обойти лимит неудачных попыток входа по IP; права каталога загрузок — 0700.
6. Линтер (ruff): дубликат стоп-слова, неоднозначное имя переменной, сортировка импортов.

