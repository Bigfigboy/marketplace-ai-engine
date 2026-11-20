# AI Engine — API Contract (для Backend 2)

Контракт описывает **только существующие** endpoints (`app/api/routes.py`, версия движка `1.0.0`).
Поведение, на которое опирается Backend 2, закреплено автотестами `tests/test_backend2_contract.py`.
Полная спецификация в формате OpenAPI: `GET /openapi.json`, интерактивно — `/docs`.

## 1. Общие правила

| Правило | Значение |
|---|---|
| Базовый URL | задаётся Backend 2 конфигурацией, например `ENGINE_URL=https://<engine-host>` (префикса версии нет) |
| Формат | `application/json; charset=utf-8`; загрузка файлов — `multipart/form-data` |
| Идентификаторы | строки UUID (`run_id`, `file_id`); собственные id движка, не id Supabase |
| Время | ISO 8601 в UTC (`created_at`) |
| Трассировка | каждый ответ содержит заголовок `X-Request-ID`; в ошибках он же — `error.request_id` |
| Идентичность | только из учётных данных (см. §2). Поля `user_id`/`company_id` в теле запроса запрещены → `422 validation_error` |
| Языки | `ru`, `kk` (казахский, ISO 639-1), `en`, `auto`/не передавать. **`kz` недопустим** → 422 |
| Идемпотентность | нет. Повтор `POST /agents/{slug}/run` создаёт новый запуск и расходует квоту — не повторяйте автоматически, кроме 429/503/504 с учётом `Retry-After` |
| Таймауты | запуск агента может длиться до `REQUEST_TIMEOUT_SECONDS` (по умолчанию 120 с); на Render Free добавляется «пробуждение» ~1 мин. Таймаут HTTP-клиента Backend 2 — **не меньше 150–180 с** |

## 2. Авторизация

Все endpoints, кроме публичных (`GET /health`, `/docs`, `/redoc`, `/openapi.json`, `/demo/*`, `/`),
требуют один из заголовков:

| Способ | Заголовок | Для кого |
|---|---|---|
| **Engine token (рекомендуется для Backend 2)** | `Authorization: Bearer <JWT>` | Backend 2 выпускает короткоживущий JWT на каждого пользователя бизнеса |
| API-ключ движка | `Authorization: Bearer aie_...` или `X-API-Key: aie_...` | сервисные проверки, демо; создаётся только CLI на сервере движка |

### 2.1 Engine token (JWT), который выпускает Backend 2

Проверка включается переменной движка `JWT_SECRET`. Подпись: HMAC (`JWT_ALGORITHM` = `HS256`, `HS384` или
`HS512`). Если заданы `JWT_AUDIENCE` / `JWT_ISSUER`, движок требует совпадения `aud` / `iss`.

| Claim | Обязателен | Значение |
|---|---|---|
| `sub` | да | id пользователя в Supabase (`auth.users.id`), ≤ 128 символов |
| `company_id` | да | id тенанта (бизнеса) в маркетплейсе, ≤ 128 символов — **согласовать**, см. SUPABASE_INTEGRATION.md |
| `exp` | да | срок жизни; рекомендуемо 5–10 минут; допуск расхождения часов 30 с |
| `role` | нет | роль **внутри компании в движке**: `admin` или `member`; любое другое значение (`business`, `developer`, `authenticated`) → `member` |
| `company_name` | нет | название бизнеса; используется при первом появлении компании |
| `aud`, `iss` | если настроены | например `ai-engine` и `marketplace-backend` |
| `iat` | нет | рекомендуется |

При первом токене с новым `company_id` / `sub` движок сам создаёт компанию и пользователя
(по внешним id); роль обновляется из каждого токена — маркетплейс источник истины.

**Сырой access token Supabase Auth не принимается**: в нём нет `company_id`, а подпись/аудитория
другие (`401 invalid_credentials`, тест `test_raw_supabase_access_token_is_rejected`).

### 2.2 Права ролей движка

| Действие | `member` | `admin` (администратор **своей компании**) |
|---|---|---|
| `GET /agents`, `POST /agents/{slug}/run` | ✅ | ✅ |
| Загрузка `resume`, `job_description`, `brief` | ✅ (файлы видны только загрузившему) | ✅ |
| Загрузка `knowledge_base` (общая база знаний компании для support) | ❌ `403 knowledge_base_admin_only` | ✅ |
| Просмотр файлов | свои + база знаний компании | все файлы компании |
| `DELETE /files/{id}` | свои | любые файлы компании |
| `GET /runs`, `GET /runs/{id}`, `GET /stats/agents` | свои запуски | все запуски компании |

**Платформенного (кросс-тенантного) администратора в движке нет**: ни один endpoint не даёт доступа к
данным другой компании. Создание/отзыв API-ключей — только CLI на сервере (`python -m app.cli ...`).

### 2.3 Ошибки авторизации

`401 auth_required` (нет заголовка), `401 invalid_credentials` (подпись, `aud`/`iss`, нет `exp`/`sub`/`company_id`),
`401 token_expired`. После 20 неудачных попыток в минуту с одного IP — `429 rate_limited`.

## 3. Формат ошибок

```json
{"error": {"code": "resume_required", "message": "No resume was received by the HR assistant. ...",
           "request_id": "3f2a9c1d7e5b4a60", "details": {"run_id": "8d7c5f0e-..."}}}
```

- `code` — стабильный машинный код (ветвитесь по нему, не по тексту); `message` — на английском.
- `details.run_id` есть у ошибок `POST /agents/{slug}/run`: запись в журнале запусков создаётся и для ошибок.
- `details.issues` — у `validation_error` (без эха пользовательского ввода).
- 429 и ошибки лимитов LLM-провайдера содержат заголовок `Retry-After` (секунды).

| HTTP | `code` |
|---|---|
| 401 | `auth_required`, `invalid_credentials`, `token_expired` |
| 403 | `knowledge_base_admin_only` |
| 404 | `agent_not_found`, `file_not_found`, `run_not_found`, `not_found` — также при попытке доступа к чужим данным |
| 405 | `method_not_allowed` |
| 413 | `file_too_large`, `payload_too_large`, `input_token_limit_exceeded`, `llm_request_too_large` |
| 415 | `unsupported_file_type` |
| 422 | `validation_error`, `message_too_long`, `unsupported_language`, `too_many_files`, `file_purpose_not_accepted`, `invalid_purpose`, `empty_file`, `empty_document`, `corrupted_file`, `encrypted_pdf`, `ocr_required`, `too_many_pages`, `file_type_mismatch`, `document_too_complex`, `resume_required`, `job_description_required`, `hr_task_unclear`, `one_resume_per_run`, `too_many_job_descriptions`, `job_description_looks_like_resume` |
| 429 | `rate_limited`, `daily_limit_exceeded`, `spend_limit_exceeded`, `company_spend_limit_exceeded`, `llm_rate_limited`, `llm_free_quota_exhausted` |
| 500 | `internal_error`, `document_processing_failed` |
| 502 | `llm_unavailable`, `llm_auth_failed`, `llm_model_not_found`, `llm_bad_request`, `llm_quota_exceeded`, `llm_error`, `llm_invalid_output`, `llm_empty_output` |
| 503 | `llm_not_configured`, `llm_model_not_allowed` |
| 504 | `llm_timeout` |

Рекомендация Backend 2: 4xx — показать пользователю (кроме 401 → обновить engine token), 429/503/504 —
повторить после `Retry-After`, 5xx/502 — показать «AI временно недоступен», сохранить `run_id`/`request_id`.

## 4. Endpoints

### `GET /health` — публичный

```json
{"status": "ok", "version": "1.0.0", "llm_mode": "mock", "llm_provider": "mock", "llm_billing": "none",
 "llm_configured": true, "llm_problem": null, "model": "mock", "database": "ok",
 "agents": ["marketing", "support", "hr"]}
```
`status: "degraded"` — БД недоступна или выбранный LLM-провайдер не настроен (`llm_problem`).
`llm_provider`: `mock | openai | groq | gemini | openrouter | ollama`; `llm_billing`: `paid | free | local | none`.

### `GET /agents` и `GET /agents/{slug}`

Метаданные агентов движка (сейчас `marketing`, `support`, `hr`) в едином формате:

```json
{"id": "agent-hr-001", "slug": "hr", "name": "AI HR Assistant", "description": "...", "version": "1.0.0",
 "category": "hr", "supported_languages": ["ru", "kk", "en"],
 "capabilities": ["resume_summary", "..."], "input_schema": {"...": "JSON Schema"},
 "output_schema": {"...": "JSON Schema поля data"}, "accepted_file_purposes": ["resume", "job_description"]}
```
`GET /agents` → `{"agents": [ ... ]}`; неизвестный slug → `404 agent_not_found`.

### `POST /agents/{slug}/run`

Запрос (лишние поля запрещены):

```json
{"message": "Создай контент-план для кофейни в Алматы", "language": "ru", "file_ids": [],
 "options": {}, "history": []}
```

| Поле | Тип / ограничения |
|---|---|
| `message` | строка 1…`MAX_MESSAGE_CHARS` (по умолчанию 8000) |
| `language` | `ru` \| `kk` \| `en` \| `auto` \| отсутствует (автоопределение) |
| `file_ids` | ≤ 10 id файлов из `POST /files/upload`, доступных этому пользователю; тип файла должен входить в `accepted_file_purposes` агента |
| `options` | опции агента (JSON Schema — `input_schema.properties.options`), см. API_DOCUMENTATION.md |
| `history` | ≤ 10 элементов `{"role": "user"\|"assistant", "content": "..."}` (≤ 4000 символов); клиентские данные, проверяются как недоверенные |

Ответ `200` (одинаковый для всех агентов):

```json
{"run_id": "5d6de9cc-1699-4f71-84c5-54c076379ef6", "agent": "support", "agent_version": "1.0.0",
 "status": "success", "response": "Доставка стоит 1 000 тенге.", "language": "ru",
 "sources": [{"file_id": "b1c2...", "filename": "kb.txt", "chunk_index": 0, "score": 1.42,
              "excerpt": "Стоимость доставки составляет 1 000 тенге..."}],
 "usage": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500, "estimated_cost": 0.0,
           "currency": "USD"},
 "execution_time_ms": 2310, "mode": "groq", "model": "openai/gpt-oss-20b",
 "data": {"intent": "question", "answer_status": "answered", "handoff": null, "...": "..."},
 "warnings": []}
```

- `status`: `success` | `escalation_required` (support: ответа нет в базе знаний, `data.handoff` — черновик
  обращения, **не отправлен**) | `blocked` (попытка prompt injection или запрещённый HR-запрос; LLM не вызывался).
- `mode`: какой провайдер ответил (`openai | groq | gemini | openrouter | ollama`), `mock` — офлайн-режим,
  **не ИИ**, `none` — LLM не вызывался.
- `usage.estimated_cost`: только для платного `openai` (по тарифам из конфигурации, иначе `null`); для
  бесплатных/локальных провайдеров и mock — `0.0`.
- `response` — Markdown от модели: рендерить с экранированием HTML.

### `POST /files/upload`

`multipart/form-data`: `file` (PDF/DOCX/TXT, ≤ 10 MB), `purpose` = `knowledge_base` | `resume` |
`job_description` | `brief`. Ответ `201`:

```json
{"id": "b1c2d3e4-...", "filename": "kb_coffee_ru.txt", "purpose": "knowledge_base", "file_format": "txt",
 "size_bytes": 812, "char_count": 419, "page_count": null, "chunk_count": 1,
 "created_at": "2026-11-20T19:40:00Z", "warnings": []}
```
Ошибки файлов — см. §3 (413/415/422, `403 knowledge_base_admin_only`).

### `GET /files?purpose=`, `GET /files/{file_id}`, `DELETE /files/{file_id}`

`GET /files` → `{"files": [FileInfo, ...]}` (без `warnings`); `GET /files/{id}` → `FileInfo`;
`DELETE` → `204` (удаляет оригинал, извлечённый текст и фрагменты базы знаний). Чужой файл → 404.

### `GET /runs?agent=&limit=&offset=` и `GET /runs/{run_id}`

Метаданные запусков (тексты запросов и ответов движок **не хранит**):

```json
{"id": "5d6de9cc-...", "agent_slug": "support", "agent_version": "1.0.0", "status": "success",
 "input_tokens": 1200, "output_tokens": 300, "estimated_cost": 0.0, "execution_time_ms": 2310,
 "error_code": null, "language": "ru", "llm_mode": "groq", "model": "openai/gpt-oss-20b",
 "file_count": 1, "source_count": 1, "created_at": "2026-11-20T22:43:49Z"}
```
`status` в журнале: `running`, `success`, `escalation_required`, `blocked`, `rejected` (отклонён до
выполнения: валидация/лимит — не расходует квоту), `error`. `GET /runs` →
`{"runs": [...], "total": N, "limit": 20, "offset": 0}` (`limit` 1–100).

### `GET /stats/agents?days=30`

Агрегаты по агентам за период (свои — для `member`, по компании — для `admin`):
`{"scope": "user", "period_days": 30, "agents": [{"agent_slug", "runs", "success", "escalation_required",
"blocked", "errors", "input_tokens", "output_tokens", "estimated_cost", "avg_execution_time_ms"}],
"today_runs_by_you": 3, "daily_run_limit": 10}`.

## 5. Файлы и `file_ids`

1. Файл передаётся движку байтами через `POST /files/upload` **от имени того пользователя/компании**, кто
   будет его использовать (engine token этого пользователя). Движок хранит свою копию и возвращает `id`.
2. Этот `id` передаётся в `file_ids` при запуске агента. Движок проверяет доступ и тип файла.
3. Соответствие файлов Supabase Storage ↔ `file_id` движка хранит Backend 2 (см. SUPABASE_INTEGRATION.md §5).

| Агент | `accepted_file_purposes` | Особенности |
|---|---|---|
| `marketing` | `brief`, `knowledge_base` | ≤ 3 файлов, необязательны |
| `support` | `knowledge_base` | без `file_ids` ищет по всей базе знаний компании; загрузка KB — только `admin` |
| `hr` | `resume`, `job_description` | ≤ 1 резюме и ≤ 1 вакансии; запрос о резюме без файла → `422 resume_required` |

## 6. Лимиты (по умолчанию, настраиваются переменными движка)

`RATE_LIMIT_PER_MINUTE=30` (на пользователя, в памяти процесса) · `DAILY_RUN_LIMIT_PER_USER=10` при
`DEMO_MODE=true` (на пользователя движка, сутки по `Asia/Almaty`) · `DAILY_SPEND_LIMIT` /
`COMPANY_DAILY_SPEND_LIMIT` (только для платного провайдера) · `MAX_FILE_SIZE_MB=10` · тело JSON ≤ 1 MB ·
лимиты бесплатных LLM-провайдеров (`LLM_REQUESTS_PER_MINUTE/PER_DAY`).

## 7. Пример: вызов из Backend 2 (Python, серверная сторона)

```python
import time, httpx, jwt  # PyJWT

def engine_token(supabase_user_id: str, business_id: str, engine_role: str, business_name: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": supabase_user_id, "company_id": business_id, "role": engine_role,
                       "company_name": business_name, "aud": "ai-engine", "iss": "marketplace-backend",
                       "iat": now, "exp": now + 300},
                      ENGINE_JWT_SECRET, algorithm="HS256")   # секрет только из переменных окружения

def run_agent(token: str, slug: str, message: str, file_ids: list[str] | None = None) -> dict:
    with httpx.Client(base_url=ENGINE_URL, timeout=180) as client:
        r = client.post(f"/agents/{slug}/run", headers={"Authorization": f"Bearer {token}"},
                        json={"message": message, "file_ids": file_ids or []})
    body = r.json()
    if r.status_code != 200:
        err = body["error"]   # err["code"], err.get("details", {}).get("run_id"), r.headers.get("Retry-After")
        raise EngineError(r.status_code, err)
    return body               # body["run_id"], body["status"], body["usage"], ...
```

curl (значения — заглушки):

```bash
curl -s "$ENGINE_URL/agents/marketing/run" -H "Authorization: Bearer $ENGINE_TOKEN" \
  -H "Content-Type: application/json" -d '{"message":"Создай контент-план для кофейни в Алматы","language":"ru"}'
curl -s "$ENGINE_URL/files/upload" -H "Authorization: Bearer $ENGINE_TOKEN" \
  -F "file=@resume_fictional.docx" -F "purpose=resume"
```
