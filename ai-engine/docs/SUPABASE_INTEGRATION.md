# Интеграция AI Engine с Supabase (Backend 2)

Документ описывает, **как Backend 2 подключает существующий AI Engine** к Supabase Auth, каталогу и своей
таблице `agent_runs`. AI Engine не создаёт и не изменяет таблицы Supabase; всё, что ниже помечено
«предложение», — решение Backend 2 и предмет согласования.

## 1. Модель доверия

```
Браузер ──(Supabase session)──► Backend 2 ──(engine token, 5 мин)──► AI Engine
   │                                │
   └── Supabase Auth                ├─ проверяет сессию Supabase (сервером)
                                    ├─ читает profiles (роль, бизнес), connect_requests (доступ к агенту)
                                    └─ подписывает engine token секретом ENGINE_JWT_SECRET
```

Правила:

1. **Источник истины об идентичности — Supabase**, проверяет её **Backend 2**. Движок доверяет только
   подписи engine token.
2. Сырой access token Supabase движку **не передаётся и не принимается** (нет `company_id`; `role` в нём —
   роль Postgres `authenticated`; аудитория другая). Проверено тестом `test_raw_supabase_access_token_is_rejected`.
   Кроме того, движок проверяет только HMAC-подписи (`HS256/384/512`), а проекты Supabase могут подписывать
   токены асимметричными ключами (JWKS) — см. Project Settings → JWT Keys вашего проекта.
3. `ENGINE_JWT_SECRET` (в движке — переменная `JWT_SECRET`) — **отдельный** случайный секрет ≥ 32 байт, не
   секрет Supabase. Хранится только в переменных окружения сервера Backend 2 и сервера движка; в браузер,
   репозиторий и документацию не попадает. Владелец секрета может выдать любую `company_id`/роль — это
   полное доверие, поэтому выпуск токенов только на сервере.
4. Движок **не знает о подписках**: проверку, что бизнес подключил агента (`connect_requests` одобрен),
   агент опубликован и пользователь имеет право его вызывать, Backend 2 выполняет **до** выпуска токена.

## 2. Сопоставление идентичности и ролей

| Engine token claim | Источник в Supabase (предложение) | Комментарий |
|---|---|---|
| `sub` | `auth.users.id` (= `profiles.id`) | движок хранит его как `users.external_id` |
| `company_id` | id **бизнеса-тенанта**: если «1 пользователь = 1 бизнес» — `profiles.id` владельца; если есть сущность организации — её id | **Согласовать.** Все данные движка (файлы, база знаний, запуски, лимиты) изолированы по этому значению |
| `role` | см. таблицу ниже | роль **внутри компании в движке**, не платформенная |
| `company_name` | название бизнеса из профиля | используется при первом появлении компании |
| `aud` / `iss` | константы, например `ai-engine` / `marketplace-backend` | должны совпадать с `JWT_AUDIENCE` / `JWT_ISSUER` движка |

| Роль в маркетплейсе (Supabase) | Роль в engine token | Почему |
|---|---|---|
| `business` (владелец бизнеса) | `admin` | может загружать базу знаний компании для support-агента, видит все запуски компании |
| сотрудник бизнеса (если появится) | `member` | только свои файлы и запуски |
| `developer` | `member` **своего** тестового тенанта (например `company_id = "dev-" + user_id`) или без доступа | движок не исполняет код сторонних разработчиков; доступ нужен только для пробных вызовов демо-агентов |
| `admin` платформы | не нужен для вызовов агентов | в движке нет кросс-тенантного администратора; модерация — в Backend 2 |

Любое значение `role`, кроме `admin`/`member`, движок трактует как `member` (тест `test_role_mapping`).
Один и тот же `sub` с разными `company_id` — разные пользователи движка с изолированными данными
(тест `test_tenant_isolation_by_company_claim`).

## 3. Выпуск engine token (пример для Backend 2)

```python
import os, time, jwt   # PyJWT; аналог есть для Node.js (jsonwebtoken / jose)

ENGINE_JWT_SECRET = os.environ["ENGINE_JWT_SECRET"]   # тот же, что JWT_SECRET в движке

def engine_token_for(profile: dict, business: dict) -> str:
    now = int(time.time())
    role = "admin" if profile["role"] == "business" else "member"
    claims = {"sub": profile["id"], "company_id": business["id"], "role": role,
              "company_name": business["name"], "aud": "ai-engine", "iss": "marketplace-backend",
              "iat": now, "exp": now + 300}
    return jwt.encode(claims, ENGINE_JWT_SECRET, algorithm="HS256")
```

Последовательность для одного вызова агента:

1. Получить пользователя из сессии Supabase на сервере (например, `supabase.auth.getUser(access_token)`).
2. Прочитать `profiles` (роль, бизнес) **сервисным ключом на сервере** или через RLS от имени пользователя.
3. Проверить право: опубликованный агент каталога, связанный с engine slug (§6), и одобренная
   `connect_requests` для этого бизнеса.
4. Выпустить engine token (§3), вызвать `POST {ENGINE_URL}/agents/{slug}/run` (API_CONTRACT.md).
5. Записать результат в свою `agent_runs` (§4) и вернуть ответ фронтенду.

Вариант «браузер → движок напрямую с engine token» возможен (CORS: домен фронтенда в `CORS_ORIGINS` движка),
но тогда токен виден в браузере; рекомендуемый вариант — проксирование через Backend 2.

## 4. `agent_runs`: две таблицы, одна запись на вызов

В движке есть своя таблица `agent_runs` (технический журнал), у Backend 2 — своя (бизнес-учёт). Чтобы не
было конфликтов и дублирования:

| | Движок (`agent_runs` движка) | Backend 2 (`agent_runs` Supabase) |
|---|---|---|
| Назначение | технический журнал: статус, токены, стоимость, время, `error_code`, провайдер | история для UI, аналитика каталога, биллинг/подписки |
| Ключ | `id` = `run_id` из ответа движка | собственный `id` + **`engine_run_id` (предложение: `UNIQUE`)** |
| Кто пишет | только движок | только Backend 2 |
| Тексты запросов/ответов | не хранит (политика приватности) | решение Backend 2 (для HR — персональные данные кандидатов; лучше не хранить) |

Правила согласования (предложение):

1. **Одна запись Backend 2 на один вызов движка.** После ответа движка Backend 2 вставляет строку с
   `engine_run_id = response.run_id` (или `error.details.run_id` для ошибок). Уникальный индекс по
   `engine_run_id` + `INSERT ... ON CONFLICT (engine_run_id) DO NOTHING` исключает дубли при повторе обработки.
2. **Без двойной записи с одной стороны.** Движок в Supabase не пишет; Backend 2 не пишет в БД движка.
3. Поля для копирования из ответа: `agent` (slug), `agent_version`, `status`, `language`, `mode`, `model`,
   `usage.input_tokens/output_tokens/estimated_cost`, `execution_time_ms`; из ошибки — HTTP-статус и
   `error.code`. Источник истины по токенам/стоимости — движок.
4. Если до движка не дошли (сеть, таймаут) — `run_id` нет; Backend 2 пишет свою строку со статусом
   вроде `engine_unreachable` и пустым `engine_run_id` (несколько `NULL` уникальному индексу не мешают).
5. Сверка: `GET /runs/{run_id}` с токеном `admin` нужной компании возвращает статус из журнала движка.
   Глобального (кросс-тенантного) списка запусков у движка нет — сверять по сохранённым `engine_run_id`.
6. Пользователь/компания в журнале движка — внутренние id движка; связь с Supabase — через `sub` /
   `company_id`, которые Backend 2 сам кладёт в токен.

## 5. Файлы: Supabase Storage ↔ движок

Supabase Storage остаётся хранилищем Backend 2. Движку нужны байты файла:

1. Backend 2 скачивает файл из Storage (сервером) и загружает его в движок `POST /files/upload` с engine
   token **того бизнеса/пользователя**, который будет его использовать, и нужным `purpose`
   (`resume`, `job_description`, `knowledge_base` — только с ролью `admin`, `brief`).
2. Возвращённый `file_id` движка Backend 2 сохраняет у себя (предложение: колонка с id движка рядом с путём
   в Storage) и передаёт в `file_ids` при запуске агента.
3. Удаление в Storage → `DELETE /files/{file_id}` в движке (иначе копия останется в движке).
4. На Render Free диск движка не сохраняется: после перезапуска файлы и база знаний пропадают, запуск с
   таким `file_id` даст `404 file_not_found` — Backend 2 должен повторно загрузить файл и повторить запрос.

## 6. Каталог агентов

- Движок исполняет только **свои** агенты: `marketing`, `support`, `hr` (`GET /agents`). Карточки каталога
  Backend 2 для них ссылаются на engine slug (предложение: поле вроде `engine_slug` в таблице `agents`).
- Сторонние агенты разработчиков **движок не исполняет** (загрузка чужого кода на сервер намеренно
  не поддерживается). Для них Backend 2 не должен вызывать движок; способ их исполнения — отдельное решение.
- Метаданные (`name`, `description`, `capabilities`, `supported_languages`, `input_schema`) можно брать из
  `GET /agents/{slug}` при публикации карточки, но каталог в Supabase — источник истины для витрины.

## 7. Где размещать БД движка

Движок использует свои таблицы `companies`, `users`, `api_keys`, `files`, `document_chunks`, `agent_runs`,
`alembic_version`. **Не размещайте их в схеме `public` Supabase**: имя `agent_runs` совпадёт с таблицей
Backend 2, а схема `public` доступна через Data API Supabase.

Варианты:

1. **Отдельная БД** (SQLite на сервере движка для демо или отдельный Postgres) — проще всего.
2. **Та же БД Supabase, отдельная схема** `ai_engine`, не добавленная в exposed schemas Data API:

   ```sql
   create schema if not exists ai_engine;   -- выполняет владелец БД один раз
   ```
   ```bash
   DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/postgres?options=-csearch_path%3Dai_engine
   ```
   Проверено на PostgreSQL 16: все таблицы движка создаются в `ai_engine`, `public.agent_runs` не
   затрагивается. Используйте прямое подключение или session-пулер (параметр `options` должен доходить до
   Postgres); совместимость с transaction-пулером не проверялась.

## 8. Что передавать в Supabase (итого)

| Данные | Откуда | Куда (предложение) |
|---|---|---|
| `run_id`, статус, токены, стоимость, время, провайдер/модель, код ошибки | ответ движка | `agent_runs` Backend 2 |
| Источники ответа support (`sources`) | ответ движка | по желанию: история диалога в UI |
| `file_id` движка | ответ `POST /files/upload` | рядом с объектом Storage |
| Соответствие карточки каталога и engine slug | `GET /agents` | `agents` Backend 2 |
| Тексты запросов/ответов | — | не обязательно; для HR лучше не хранить (ПДн) |
