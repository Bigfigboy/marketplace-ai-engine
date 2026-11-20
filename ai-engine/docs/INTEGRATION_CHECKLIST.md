# Чек-лист совместного тестирования AI Engine ↔ Backend 2

Отмечайте пункты по ходу совместной сессии. Колонка «Автотест» — где поведение уже проверено в движке
(`ai-engine/tests/`); на совместной сессии проверяется то же самое, но через код Backend 2.
Все данные в тестах — вымышленные. Реальные ключи и секреты в чат и документы не вставлять.

## 0. Согласования (до начала)

- [ ] Тенант: какой id Supabase передаётся как `company_id` (AI_ENGINE_HANDOFF.md §6, п.1)
- [ ] Сопоставление ролей `business/developer/admin` → `admin/member` (SUPABASE_INTEGRATION.md §2)
- [ ] `JWT_AUDIENCE`, `JWT_ISSUER`, срок жизни токена (рекомендуется 300 с)
- [ ] Секрет `ENGINE_JWT_SECRET` (≥ 32 случайных байт) передан защищённым каналом; не совпадает с секретом Supabase
- [ ] Схема вызова: через Backend 2 (рекомендуется) / напрямую из браузера (тогда `CORS_ORIGINS`)
- [ ] Структура `agent_runs` Backend 2 с уникальным `engine_run_id`
- [ ] Связь карточек каталога с engine slug (`marketing`, `support`, `hr`)
- [ ] Размещение БД движка: отдельная БД или схема `ai_engine` (не `public`)
- [ ] LLM-провайдер на стенде: `mock` / Groq / Gemini / OpenRouter / локальный Ollama
- [ ] Лимиты на время тестов: `DAILY_RUN_LIMIT_PER_USER`, `RATE_LIMIT_PER_MINUTE`

## 1. Конфигурация стенда

| # | Проверка | Ожидание | Автотест |
|---|---|---|---|
| 1.1 | `GET {ENGINE_URL}/health` | `200`, `status: ok`, `agents` содержит `marketing`, `support`, `hr`, видно `llm_provider` | `test_health_is_public_for_monitoring` |
| 1.2 | В движке заданы `JWT_SECRET`, `JWT_AUDIENCE`, `JWT_ISSUER` | токен Backend 2 принимается (п. 2.1) | `test_engine_token_from_backend2_end_to_end` |
| 1.3 | Таймаут HTTP-клиента Backend 2 | ≥ 150 с; первый запрос к «спящему» Render проходит | — |
| 1.4 | Секрет отсутствует во фронтенде и в git обеих сторон | поиск по репозиториям ничего не находит | — |

## 2. Авторизация

| # | Проверка | Ожидание | Автотест |
|---|---|---|---|
| 2.1 | Вызов `GET /agents` с engine token владельца бизнеса | `200`, три агента | `test_engine_token_from_backend2_end_to_end` |
| 2.2 | Вызов с **сырым** токеном Supabase | `401 invalid_credentials` | `test_raw_supabase_access_token_is_rejected` |
| 2.3 | Просроченный токен | `401 token_expired`; Backend 2 перевыпускает токен | `test_invalid_engine_tokens` |
| 2.4 | Неверные `aud` / `iss` / подпись / нет `exp` / нет `company_id` | `401 invalid_credentials` | `test_invalid_engine_tokens` |
| 2.5 | Без заголовка | `401 auth_required` | `test_auth_required_and_invalid_keys` |
| 2.6 | `user_id`/`company_id` в теле запроса | `422 validation_error` | `test_error_contract_for_backend2` |
| 2.7 | Роль `business` → `admin`, остальные → `member` | `admin` загружает базу знаний (`201`), `member` — `403 knowledge_base_admin_only` | `test_role_mapping` |
| 2.8 | Пользователь без одобренной `connect_requests` | Backend 2 **не вызывает** движок (проверка на стороне Backend 2) | — |

## 3. Изоляция компаний

| # | Проверка | Ожидание | Автотест |
|---|---|---|---|
| 3.1 | Бизнес B запрашивает `GET /files/{id}` / `DELETE` файла бизнеса A | `404 file_not_found` | `test_tenant_isolation_by_company_claim` |
| 3.2 | Бизнес B передаёт `file_id` бизнеса A в `file_ids` | `404 file_not_found` | `test_cross_company_file_access_denied` |
| 3.3 | Бизнес B запрашивает `GET /runs/{run_id}` бизнеса A; `GET /runs` | `404`; `total: 0` | `test_tenant_isolation_by_company_claim` |
| 3.4 | Support бизнеса B спрашивает то, что есть только в базе A | `escalation_required`, `sources: []` | `test_tenant_isolation_by_company_claim` |
| 3.5 | Один пользователь Supabase в двух бизнесах | данные не смешиваются | `test_tenant_isolation_by_company_claim` |
| 3.6 | `member` не видит резюме другого `member` той же компании | `404` | `test_private_files_isolated_between_users_of_same_company` |

## 4. Агенты

| # | Проверка | Ожидание | Автотест |
|---|---|---|---|
| 4.1 | `marketing`: «Создай контент-план для кофейни в Алматы», `language: ru` | `200`, `status: success`, все поля ответа по контракту | `test_marketing_ru` |
| 4.2 | `marketing` на казахском, `language: kk` | `language: kk` | `test_marketing_kk_autodetect`, `test_language_codes` |
| 4.3 | `language: kz` | `422 validation_error` (правильно — `kk`) | `test_language_codes` |
| 4.4 | `support`: загрузить KB (`admin`) → «Сколько стоит доставка?» | `success`, `sources[0].file_id` = загруженный файл | `test_engine_token_from_backend2_end_to_end` |
| 4.5 | `support`: вопрос вне базы | `escalation_required`, `data.handoff.delivered: false` | `test_support_no_information_escalates_without_llm_call` |
| 4.6 | `hr`: загрузить вымышленное резюме (`purpose=resume`) → «Проанализируй загруженное резюме…» | `success`, навыки/опыт/образование | `test_resume_analysis_extracts_known_skills_and_education` |
| 4.7 | `hr`: тот же запрос без резюме | `422 resume_required` | `test_resume_not_received_is_a_clear_error_not_a_template` |
| 4.8 | `hr`: «Ранжируй кандидатов» | `status: blocked` | `test_hr_refuses_ranking_decisions_and_protected_attributes` |
| 4.9 | Попытка prompt injection в сообщении | `status: blocked` | `test_support_prompt_injection_in_user_message_is_blocked` |
| 4.10 | `mode` в ответе соответствует провайдеру стенда | `mock` на Render по умолчанию | `test_render_mock_mode_needs_no_api_key` |

## 5. Файлы

| # | Проверка | Ожидание | Автотест |
|---|---|---|---|
| 5.1 | PDF и DOCX из Supabase Storage → `POST /files/upload` | `201`, `id` сохранён у Backend 2 | `test_upload_pdf`, `test_upload_docx_with_table_and_chunks` |
| 5.2 | Файл > 10 MB | `413 file_too_large` | `test_default_limit_is_10mb` |
| 5.3 | `.exe` / подмена расширения | `415 unsupported_file_type` / `422 file_type_mismatch` | `test_invalid_extension_rejected`, `test_extension_content_mismatch_rejected` |
| 5.4 | Скан PDF без текста | `422 ocr_required` | `test_image_only_pdf_requires_ocr` |
| 5.5 | Удаление в Storage → `DELETE /files/{id}` | `204`, повторный `GET` → `404` | `test_delete_file_removes_blobs_and_chunks` |
| 5.6 | После перезапуска Render старый `file_id` | `404 file_not_found` → Backend 2 загружает заново | — |
| 5.7 | Резюме, загруженное как `knowledge_base`, передано HR | `422 file_purpose_not_accepted` | `test_resume_with_wrong_file_type_is_rejected` |

## 6. Ошибки и лимиты

| # | Проверка | Ожидание | Автотест |
|---|---|---|---|
| 6.1 | Любая ошибка | JSON `{"error": {"code", "message", "request_id"}}`, заголовок `X-Request-ID` | `test_error_contract_for_backend2` |
| 6.2 | Ошибка запуска агента | `error.details.run_id` есть, запись видна в `GET /runs/{run_id}` | `test_error_contract_for_backend2` |
| 6.3 | Превышен дневной лимит | `429 daily_limit_exceeded` + `Retry-After` | `test_daily_request_limit_per_user` |
| 6.4 | Превышен лимит в минуту | `429 rate_limited` + `Retry-After` | `test_rate_limit_per_minute` |
| 6.5 | LLM-провайдер не настроен на стенде | `503 llm_not_configured`, `/health` → `degraded` | `test_health_reports_provider` |
| 6.6 | Бесплатная квота провайдера исчерпана | `429 llm_free_quota_exhausted` + `Retry-After`, без смены модели | `test_free_quota_error_reaches_client_with_retry_after` |
| 6.7 | Backend 2 не повторяет `POST /agents/{slug}/run` автоматически при 4xx/5xx | один `run_id` на одно действие пользователя | — |

## 7. Журнал запусков

| # | Проверка | Ожидание |
|---|---|---|
| 7.1 | Успешный запуск | одна строка в `agent_runs` Supabase с `engine_run_id` = `run_id` |
| 7.2 | Ошибка движка (например 422) | одна строка с `engine_run_id` = `error.details.run_id` и кодом ошибки |
| 7.3 | Повторная обработка того же ответа | дубликат не создаётся (уникальный индекс) |
| 7.4 | Сверка | `GET /runs/{run_id}` (токен `admin` компании) совпадает по статусу и токенам |
| 7.5 | Тексты HR-запросов/ответов | не сохраняются в Supabase (если так согласовано) |
| 7.6 | БД движка | не в схеме `public` Supabase; `public.agent_runs` Backend 2 не затронута |

## 8. Завершение

- [ ] Все пункты 1–7 пройдены на общем стенде; расхождения записаны с `request_id` и `run_id`
- [ ] Секрет перевыпущен, если он где-либо засветился во время тестов
- [ ] Демо-лимиты возвращены к значениям для показа (`DAILY_RUN_LIMIT_PER_USER=10`)
- [ ] Автотесты движка зелёные в GitHub Actions (Linux и Windows)
