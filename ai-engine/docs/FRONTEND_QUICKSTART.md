# AI Engine — памятка для frontend-разработчика

Полная версия: [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md), все поля и коды ошибок:
[API_DOCUMENTATION.md](API_DOCUMENTATION.md), живая спецификация: `GET /openapi.json`, Swagger: `/docs`.

## 1. Как ходить в API

- Базовый URL: адрес стенда (например `https://<имя>.onrender.com`) или `http://localhost:8000`.
- Заголовок: `Authorization: Bearer <token>`. В продакшене `<token>` — короткоживущий JWT, который
  выдаёт **бэкенд маркетплейса** (ключи `aie_...` в браузере не хранить; для демо — допустимо).
- Не передавайте `user_id`/`company_id` в теле — сервер определяет их по токену (лишние поля → 422).
- Для прямых вызовов из браузера домен фронтенда должен быть в `CORS_ORIGINS` движка.

## 2. Пять вызовов, которых достаточно

| Задача | Вызов |
|---|---|
| Каталог агентов (карточки) | `GET /agents` → `name`, `description`, `capabilities`, `supported_languages`, `accepted_file_purposes` |
| Отправить сообщение агенту | `POST /agents/{slug}/run` с `{"message", "language"?, "file_ids"?, "options"?, "history"?}` |
| Загрузить файл | `POST /files/upload` (multipart: `file`, `purpose`) → `id` |
| Удалить файл | `DELETE /files/{id}` → 204 |
| История/статистика | `GET /runs`, `GET /stats/agents` |

Slugs: `marketing`, `support`, `hr`. `purpose`: `knowledge_base` (только admin, для support),
`resume` (hr, одно на запрос), `job_description` (hr), `brief` (marketing).

```ts
const res = await fetch(`${API}/agents/support/run`, {
  method: "POST",
  headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  body: JSON.stringify({ message: "Сколько стоит доставка?", history }),  // language: авто
});
const body = await res.json();
if (!res.ok) showError(body.error.code, body.error.message);   // + body.error.details?.run_id
else render(body);
```

## 3. Что показывать из ответа

```json
{"run_id": "...", "agent": "support", "status": "success | escalation_required | blocked",
 "response": "Markdown", "language": "ru|kk|en",
 "sources": [{"filename": "...", "excerpt": "...", "chunk_index": 0, "score": 1.2, "file_id": "..."}],
 "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost": null, "currency": "USD"},
 "execution_time_ms": 0, "mode": "openai | groq | gemini | openrouter | ollama | mock | none", "model": "...", "data": {}, "warnings": []}
```

- `response` — Markdown от модели: **рендерить с экранированием HTML** (без `dangerouslySetInnerHTML`
  без санитайзера; ссылки — с `rel="noopener noreferrer"`).
- `status`:
  - `success` — обычный ответ;
  - `escalation_required` — в базе знаний нет ответа; покажите `data.handoff` (черновик обращения),
    он **не отправлен** менеджеру (`delivered: false`) — передачу в CRM делает маркетплейс;
  - `blocked` — запрос отклонён правилами (попытка взлома промпта или запрещённый HR-запрос);
    `response` содержит объяснение для пользователя.
- `mode` — какой провайдер ответил (`groq`, `gemini`, `openrouter`, `ollama`, `openai`) — показывайте
  вместе с `model`. `mode: "mock"` — тестовый режим без ИИ, обязательно показывайте плашку «демо-ответ, не ИИ».
- `sources` — для support показывайте как «Источники».
- `usage.estimated_cost` может быть `null` (тарифы не настроены) — показывайте «н/д».

## 4. Ошибки, которые стоит обработать в UI

| HTTP / code | Что показать |
|---|---|
| 401 | «Сессия истекла» → обновить токен |
| 422 `ocr_required` | «PDF — скан, нужен текстовый PDF/DOCX» |
| 422 `file_type_mismatch` / 415 | «Поддерживаются PDF, DOCX, TXT» |
| 413 `file_too_large` | «Файл больше 10 MB» |
| 429 (+ `Retry-After`) | «Лимит запросов, попробуйте позже» (`llm_free_quota_exhausted` — исчерпан бесплатный лимит AI на сегодня) |
| 502/503/504 `llm_*` | «AI временно недоступен, повторите» |

## 5. Чат и история

Сервер не хранит переписку. Для контекста передавайте `history` — до 10 последних реплик
`{role: "user"|"assistant", content}` (каждая ≤ 4000 символов). Ориентир по UI — `ai-engine/demo/app.js`.
