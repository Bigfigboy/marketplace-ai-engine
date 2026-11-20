# API Documentation

Базовый URL локально: `http://localhost:8000`. Интерактивно: `/docs` (Swagger UI), `/redoc`,
спецификация: `/openapi.json`.

## Аутентификация

Все endpoints, кроме `GET /health`, `/docs`, `/openapi.json`, `/demo/*`, требуют один из заголовков:

| Заголовок | Значение |
|---|---|
| `Authorization: Bearer aie_...` | ключ движка |
| `X-API-Key: aie_...` | ключ движка |
| `Authorization: Bearer <JWT>` | JWT от бэкенда маркетплейса (HS256/384/512, при заданном `JWT_SECRET`) |

JWT claims: `sub` (id пользователя маркетплейса, обязательно), `company_id` (id компании, обязательно),
`exp` (обязательно), `role` = `member` | `admin` (опционально), `company_name` (опционально),
`aud`/`iss` — если заданы `JWT_AUDIENCE`/`JWT_ISSUER`.

`user_id` / `company_id` в теле запроса **не принимаются** (тело с лишними полями → 422).

Роли: `member` видит свои файлы и запуски + общую базу знаний компании; `admin` видит всё в своей компании.

## Формат ошибок

```json
{"error": {"code": "file_not_found", "message": "File not found", "request_id": "3f2a...", "details": {"run_id": "..."}}}
```

| HTTP | code | Когда |
|---|---|---|
| 401 | `auth_required`, `invalid_credentials`, `token_expired` | нет/неверные учётные данные |
| 403 | `knowledge_base_admin_only` | загрузка в базу знаний компании не администратором |
| 404 | `agent_not_found`, `file_not_found`, `run_not_found`, `not_found` | нет объекта **или нет доступа** (чужая компания) |
| 413 | `file_too_large`, `payload_too_large`, `input_token_limit_exceeded` | превышены размеры |
| 415 | `unsupported_file_type` | расширение не PDF/DOCX/TXT |
| 422 | `validation_error`, `message_too_long`, `file_type_mismatch`, `empty_file`, `empty_document`, `corrupted_file`, `encrypted_pdf`, `ocr_required`, `too_many_pages`, `invalid_purpose`, `file_purpose_not_accepted`, `too_many_files`, `resume_required`, `job_description_required`, `one_resume_per_run`, `too_many_job_descriptions`, `job_description_looks_like_resume`, `document_too_complex`, `hr_task_unclear` | некорректный ввод |
| 429 | `rate_limited`, `daily_limit_exceeded`, `spend_limit_exceeded`, `company_spend_limit_exceeded` | лимиты (заголовок `Retry-After`) |
| 500 | `internal_error` | непредвиденная ошибка (детали только в логах) |
| 429 | `llm_rate_limited`, `llm_free_quota_exhausted` | лимит бесплатного тарифа LLM-провайдера (или «предохранитель» движка); `Retry-After`; модель не меняется |
| 413 | `llm_request_too_large` | запрос превышает лимит токенов модели/бесплатного тарифа |
| 503 | `llm_model_not_allowed` | модель не в allowlist бесплатных моделей (OpenRouter — только `:free`) |
| 502 | `llm_unavailable`, `llm_auth_failed`, `llm_model_not_found`, `llm_bad_request`, `llm_quota_exceeded`, `llm_error`, `llm_invalid_output`, `llm_empty_output` | ошибка провайдера ИИ |
| 503 | `llm_not_configured` | нет ключа выбранного провайдера (`OPENAI_API_KEY`, `GROQ_API_KEY`, ...) |
| 504 | `llm_timeout` | таймаут провайдера |

Ошибки запуска агента содержат `details.run_id` — запись в `agent_runs` создаётся и для ошибок.

## Endpoints

### `GET /health` (публичный)

```json
{"status": "ok", "version": "1.0.0", "llm_mode": "groq", "llm_provider": "groq", "llm_billing": "free",
 "llm_configured": true, "llm_problem": null, "model": "openai/gpt-oss-20b", "database": "ok",
 "agents": ["marketing", "support", "hr"]}
```
`status = degraded`, если БД недоступна или выбранный провайдер не может использоваться
(`llm_problem`: `llm_not_configured` — нет ключа, `llm_model_not_allowed` — модель вне allowlist).
`llm_billing`: `paid` | `free` | `local` | `none`.

### `GET /agents`, `GET /agents/{slug}`

Метаданные агента по единому стандарту:

```json
{
  "id": "agent-support-001", "slug": "support", "name": "AI Customer Support Agent",
  "description": "...", "version": "1.0.0", "category": "customer_support",
  "supported_languages": ["ru", "kk", "en"],
  "capabilities": ["knowledge_base_qa", "faq", "complaint_handling", "..."],
  "input_schema": { "...JSON Schema запроса, options = схема опций агента..." },
  "output_schema": { "...JSON Schema поля data в ответе..." },
  "accepted_file_purposes": ["knowledge_base"]
}
```

### `POST /agents/{slug}/run`

Запрос:

```json
{
  "message": "Создай контент-план для кофейни в Алматы",
  "language": "ru",
  "file_ids": [],
  "options": {},
  "history": []
}
```

| Поле | Тип | Описание |
|---|---|---|
| `message` | string, 1–20000 (лимит движка `MAX_MESSAGE_CHARS`, по умолчанию 8000) | запрос |
| `language` | `ru` \| `kk` \| `en` \| `auto` \| null | null/auto → автоопределение |
| `file_ids` | string[] ≤ 10 | файлы из `/files/upload` (доступ проверяется) |
| `options` | object | опции агента (см. ниже и `input_schema`) |
| `history` | `[{role: user\|assistant, content}]` ≤ 10 | предыдущие реплики чата (недоверенные данные) |

Ответ (единый для всех агентов):

```json
{
  "run_id": "8d7c...", "agent": "marketing", "agent_version": "1.0.0",
  "status": "success",
  "response": "## Контент-план ...",
  "language": "ru",
  "sources": [],
  "usage": {"input_tokens": 812, "output_tokens": 640, "total_tokens": 1452,
            "estimated_cost": null, "currency": "USD"},
  "execution_time_ms": 6120,
  "mode": "openai", "model": "gpt-4.1-mini",
  "data": {"task": "content_plan", "platforms": [], "draft": true, "published": false,
           "contains_numbers_to_verify": false},
  "warnings": []
}
```

- `status`: `success` | `escalation_required` (support: нет информации в базе) | `blocked`
  (попытка prompt injection или запрещённый HR-запрос; LLM не вызывается) | ошибки — см. HTTP-коды.
- `mode`: провайдер, сформировавший ответ — `openai` | `groq` | `gemini` | `openrouter` | `ollama`
  (реальный вызов модели); `mock` — офлайн-режим, **не ИИ**; `none` — LLM не вызывался.
- `usage` — фактические токены из ответа провайдера; `estimated_cost` = токены × тарифы из
  конфигурации для платного `openai` (`null`, если тарифы не заданы); для бесплатных/локальных
  провайдеров и mock всегда `0.0`.
- `sources` (support): `[{file_id, filename, chunk_index, score, excerpt}]`.

#### Опции агентов

**marketing**

| option | значения |
|---|---|
| `task` | `auto` (по умолчанию), `audience_analysis`, `marketing_strategy`, `ad_copy`, `content_plan`, `social_posts`, `commercial_offer`, `email_campaign`, `smb_recommendations` |
| `business_name`, `industry`, `city`, `target_audience`, `tone` | строки |
| `platforms` | `["instagram","tiktok","telegram"]` |
| `budget_kzt` | целое, тенге |
| `period_days` | 1–365 |

Файлы: `brief`, `knowledge_base` (≤ 3). `data`: `task`, `platforms`, `draft: true`, `published: false`, `contains_numbers_to_verify`.

**support**

| option | значения |
|---|---|
| `mode` | `auto`, `question`, `complaint`, `faq` |
| `top_k` | 1–10 фрагментов |

Файлы: `knowledge_base` (если `file_ids` пуст — вся база знаний компании). `data`: `intent`,
`answer_status`, `handoff` (`summary`, `category`, `priority`, `missing_information`,
`suggested_next_steps`, `delivered: false`, `delivery_note`), `retrieved_fragments`,
`neutralized_injection_fragments`.

**hr**

| option | значения |
|---|---|
| `task` | `auto`, `resume_summary`, `job_requirements`, `requirements_check`, `interview_questions`, `job_description` |
| `job_title` | строка |
| `job_description` | текст вакансии (альтернатива файлу `job_description`) |
| `question_count` | 3–20 |

Файлы: `resume` (не более одного за запрос), `job_description`. `data`: `task`, `result`
(структура зависит от задачи), `redactions` (что замаскировано перед отправкой в LLM),
`unverified_items` (навыки/требования, не подтверждённые текстом резюме), `decision_support_only: true`.

### `POST /files/upload`

`multipart/form-data`: `file` (PDF/DOCX/TXT, ≤ 10 MB), `purpose` = `knowledge_base` | `resume` |
`job_description` | `brief`.

```json
{"id": "b1c2...", "filename": "kb_coffee_ru.txt", "purpose": "knowledge_base", "file_format": "txt",
 "size_bytes": 812, "char_count": 419, "page_count": null, "chunk_count": 1,
 "created_at": "2026-11-20T19:40:00Z", "warnings": []}
```

Файлы `knowledge_base` (общие для компании и используемые support-агентом) может загружать только `admin`.

Проверки: расширение, реальный формат по содержимому (расширение должно совпадать), размер,
пустые/повреждённые/зашифрованные файлы, PDF без текстового слоя → `ocr_required`,
ZIP-бомбы в DOCX, лимит страниц PDF. PDF/DOCX разбираются в отдельном процессе с лимитом времени
(`EXTRACTION_TIMEOUT_SECONDS`) и памяти (`EXTRACTION_MEMORY_MB`); слишком «тяжёлый» документ → `document_too_complex`.
Лимит размера действует и для запросов без `Content-Length` (chunked). `warnings` может содержать
`instruction_like_text_detected_will_be_ignored`, `decoded_as_cp1251`, `text_truncated`.

### `GET /files`, `GET /files/{id}`, `DELETE /files/{id}`

Список/метаданные доступных файлов. `DELETE` (204) удаляет оригинал, извлечённый текст и фрагменты
базы знаний; удалять может загрузивший пользователь или admin компании.

### `GET /runs?agent=&limit=&offset=`, `GET /runs/{id}`

История запусков (метаданные из `agent_runs`, без текстов запросов и ответов):

```json
{"id": "...", "agent_slug": "support", "agent_version": "1.0.0", "status": "escalation_required",
 "input_tokens": 0, "output_tokens": 0, "estimated_cost": 0.0, "execution_time_ms": 4,
 "error_code": null, "language": "ru", "llm_mode": "none", "model": null,
 "file_count": 0, "source_count": 0, "created_at": "..."}
```

### `GET /stats/agents?days=30`

Статистика по агентам: запуски, по статусам, токены, стоимость, среднее время;
`today_runs_by_you`, `daily_run_limit`. Для `admin` — по компании, для `member` — свои.
