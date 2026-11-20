# Test Report

Дата: 20.11.2026. Все результаты ниже — фактический вывод команд в среде разработки.

## Важно: что проверено, а что нет

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
82 passed in 10.04s
```

### Тот же набор на PostgreSQL 16 (локальный сервер)

```
$ TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/ai_engine_test python -m pytest
81 passed in 18.55s   (прогон до добавления теста test_knowledge_base_upload_is_admin_only)
```

### Другие версии Python

```
Python 3.11.18 (uv venv):            82 passed in 16.09s
Python 3.12.16 (внутри Docker-образа): 82 passed in 13.56s
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
  процесс работает от пользователя `engine` (uid 10001).
- `docker compose` с профилем `postgres` **не запускался**.

### Ручная проверка демо-интерфейса (Playwright, Chromium, mock-режим)

Сценарий: ввод ключа → загрузка базы знаний → вопрос support (ответ с источником) → вопрос вне
базы (`escalation_required`, черновик обращения «не отправлен») → загрузка вымышленного резюме →
сводка HR → пост для marketing на казахском. Ошибок в консоли браузера нет; ширина 390 px — без
горизонтальной прокрутки. Скрипт `examples/curl_examples.sh` пройден целиком против запущенного сервера.

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

## Найденные и исправленные ошибки в ходе разработки

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

