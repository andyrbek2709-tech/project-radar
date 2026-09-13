# Railway — переменные окружения

Практический гайд: что вставить в **Variables → Raw Editor** каждого сервиса.
Блоки ниже готовы к вставке целиком; секреты — плейсхолдерами `<...>`.
Значения по умолчанию совпадают с `backend/app/core/config.py`, но здесь они
проставлены явно, чтобы состояние прода было видно в дашборде, а не в коде.

Имена сервисов, на которые ссылаются `${{...}}`: `Postgres`, `Redis`, `api`,
`worker`, `scheduler`, `telegram`, `web`. Если назвал иначе — поправь ссылки.

---

## 0. Что берётся из Railway-референсов (руками не вводить)

| Переменная | Значение | Где |
|---|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | api, worker, scheduler, telegram |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | api, worker, scheduler |
| `API_URL` | `http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000` | web |
| `CORS_ORIGINS` | `https://${{web.RAILWAY_PUBLIC_DOMAIN}}` | api |

Штатный Railway Redis отдаёт `REDIS_URL` сам. **Postgres — нет**: pgvector
ставится как Docker-образ `pgvector/pgvector:pg17`, и `DATABASE_URL` на нём
надо объявить один раз (блок ниже). После этого `${{Postgres.DATABASE_URL}}`
работает во всех сервисах. Драйвер (`+asyncpg` / `+psycopg`) код подставляет
сам — в URL его писать не нужно.

Приватная сеть Railway: `api` слушает `$PORT`. Чтобы адрес для `web` был
предсказуемым, на `api` задаётся `PORT=8000` — это в блоке api.

---

## 1. Postgres (Docker Image `pgvector/pgvector:pg17`)

Volume → `/var/lib/postgresql/data`. Публичный домен не нужен.

```env
POSTGRES_USER=radar
POSTGRES_PASSWORD=<сгенерировать, см. §4>
POSTGRES_DB=radar
PGDATA=/var/lib/postgresql/data/pgdata
DATABASE_URL=postgresql://${{POSTGRES_USER}}:${{POSTGRES_PASSWORD}}@${{RAILWAY_PRIVATE_DOMAIN}}:5432/${{POSTGRES_DB}}
```

`PGDATA` в подкаталог — иначе Postgres отказывается инициализироваться в
непустом корне тома (Railway кладёт туда `lost+found`).

---

## 2. Блоки по сервисам

### `api` — FastAPI, миграции при старте

```env
# --- infra (референсы) ---
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
PORT=8000

# --- app ---
APP_NAME=Project Radar
APP_ENV=production
SECRET_KEY=<сгенерировать, см. §4>
TIMEZONE=Asia/Aqtau
LOG_LEVEL=INFO
LOG_JSON=true

# --- auth / cors (только api) ---
AUTH_ENABLED=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<сгенерировать, см. §4 — тот же пароль в web>
CORS_ORIGINS=https://${{web.RAILWAY_PUBLIC_DOMAIN}}

# --- github ---
GITHUB_TOKEN=<см. §4>
GITHUB_USERNAME=andyrbek2709-tech
GITHUB_SEARCH_ENABLED=true
GITHUB_MIN_STARS=40
GITHUB_MAX_REPO_AGE_DAYS=30
GITHUB_STALE_PUSH_DAYS=270
GITHUB_README_MAX_CHARS=12000

# --- groq: strict JSON schema работает ТОЛЬКО на gpt-oss-20b / gpt-oss-120b ---
GROQ_API_KEY=<см. §4>
GROQ_ENABLED=true
GROQ_MODEL=openai/gpt-oss-20b
GROQ_ESCALATION_MODEL=openai/gpt-oss-120b
GROQ_MAX_COMPLETION_TOKENS=1200
GROQ_TIMEOUT_SECONDS=60

# --- openai: эмбеддинги всегда, deep analysis — выключен ---
OPENAI_API_KEY=<см. §4>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1
OPENAI_DEEP_ANALYSIS_ENABLED=false

# --- embeddings: DIM фиксируется миграцией, после первого старта не менять ---
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
EMBEDDING_BATCH_SIZE=64

# --- radar / пороги (меняются на лету через PUT /api/settings/{key}) ---
RADAR_ENABLED=true
RADAR_DAILY_TIME=21:00
RADAR_TIMEZONE=Asia/Aqtau
TELEGRAM_SCAN_INTERVAL_MINUTES=15
GITHUB_SCAN_INTERVAL_MINUTES=120
PIPELINE_INTERVAL_MINUTES=10
PIPELINE_BATCH_SIZE=120
MAX_DEEP_ANALYSES_PER_DAY=20
MIN_RELEVANCE_SCORE=0.65
MIN_DEEP_ANALYSIS_SCORE=0.70
CRITICAL_SCORE=0.80
RECOMMENDED_SCORE=0.60
REVIEW_LATER_SCORE=0.40
DEDUP_COSINE_THRESHOLD=0.08
DUPLICATE_FEATURE_THRESHOLD=0.85

# --- telegram: api только показывает статус в /api/ops/status ---
TELEGRAM_ENABLED=true
TELEGRAM_SESSION_STRING=<см. §4>
```

### `worker` — Celery, весь pipeline и доставка отчёта

Отчёт в «Избранное» шлёт **worker**, не telegram-сервис: ему нужны те же
Telegram-креды. Это короткое соединение раз в сутки той же сессией — Telegram
такое допускает; рискованны только частые *перелогины*, а их здесь нет.

```env
# --- infra (референсы) ---
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}

# --- app ---
APP_NAME=Project Radar
APP_ENV=production
SECRET_KEY=<тот же, что в api>
TIMEZONE=Asia/Aqtau
LOG_LEVEL=INFO
LOG_JSON=true

# --- github ---
GITHUB_TOKEN=<см. §4>
GITHUB_USERNAME=andyrbek2709-tech
GITHUB_SEARCH_ENABLED=true
GITHUB_MIN_STARS=40
GITHUB_MAX_REPO_AGE_DAYS=30
GITHUB_STALE_PUSH_DAYS=270
GITHUB_README_MAX_CHARS=12000

# --- groq ---
GROQ_API_KEY=<см. §4>
GROQ_ENABLED=true
GROQ_MODEL=openai/gpt-oss-20b
GROQ_ESCALATION_MODEL=openai/gpt-oss-120b
GROQ_MAX_COMPLETION_TOKENS=1200
GROQ_TIMEOUT_SECONDS=60

# --- openai ---
OPENAI_API_KEY=<см. §4>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1
OPENAI_DEEP_ANALYSIS_ENABLED=false

# --- embeddings ---
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
EMBEDDING_BATCH_SIZE=64

# --- radar / пороги ---
RADAR_ENABLED=true
RADAR_DAILY_TIME=21:00
RADAR_TIMEZONE=Asia/Aqtau
PIPELINE_BATCH_SIZE=120
MAX_DEEP_ANALYSES_PER_DAY=20
MIN_RELEVANCE_SCORE=0.65
MIN_DEEP_ANALYSIS_SCORE=0.70
CRITICAL_SCORE=0.80
RECOMMENDED_SCORE=0.60
REVIEW_LATER_SCORE=0.40
DEDUP_COSINE_THRESHOLD=0.08
DUPLICATE_FEATURE_THRESHOLD=0.85

# --- telegram: для доставки Daily Radar в «Избранное» ---
TELEGRAM_DELIVER_REPORT=true
TELEGRAM_API_ID=<см. §4>
TELEGRAM_API_HASH=<см. §4>
TELEGRAM_SESSION_STRING=<см. §4>
TELEGRAM_FLOOD_SLEEP_THRESHOLD=60
```

### `scheduler` — Celery beat на RedBeat

Только расписание. Ни ключей, ни порогов ему не нужно. `numReplicas` = 1.

```env
REDIS_URL=${{Redis.REDIS_URL}}
DATABASE_URL=${{Postgres.DATABASE_URL}}

APP_NAME=Project Radar
APP_ENV=production
SECRET_KEY=<тот же, что в api>
TIMEZONE=Asia/Aqtau
LOG_LEVEL=INFO
LOG_JSON=true

RADAR_ENABLED=true
RADAR_DAILY_TIME=21:00
RADAR_TIMEZONE=Asia/Aqtau
GITHUB_SCAN_INTERVAL_MINUTES=120
PIPELINE_INTERVAL_MINUTES=10
```

### `telegram` — долгоживущий сборщик

Одно живое MTProto-соединение. `numReplicas` = 1. Redis не нужен.

```env
DATABASE_URL=${{Postgres.DATABASE_URL}}

APP_NAME=Project Radar
APP_ENV=production
SECRET_KEY=<тот же, что в api>
TIMEZONE=Asia/Aqtau
LOG_LEVEL=INFO
LOG_JSON=true

TELEGRAM_ENABLED=true
TELEGRAM_API_ID=<см. §4>
TELEGRAM_API_HASH=<см. §4>
TELEGRAM_SESSION_STRING=<см. §4>
TELEGRAM_SOURCE_IDS=<см. §5>
TELEGRAM_SCAN_INTERVAL_MINUTES=15
TELEGRAM_HISTORY_LIMIT=50
TELEGRAM_FLOOD_SLEEP_THRESHOLD=60
```

### `web` — Next.js, единственный публичный сервис

`ADMIN_*` здесь — обычные переменные, **не** `NEXT_PUBLIC_*`: Basic-auth
добавляет серверный прокси `web/app/api/proxy`, в браузер креды не уходят.
`NEXT_PUBLIC_API_URL` не нужен — фронт ходит только через `/api/proxy`.

```env
API_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<тот же, что в api>
NODE_ENV=production
```

Если прокси отдаёт 502 «Backend недоступен», а `api` жив — приватная сеть
Railway требует IPv6: замени в `railway.json` `--host 0.0.0.0` на `--host ::`.

---

## 3. Почему такие числа

| Переменная | Значение | Одной строкой |
|---|---|---|
| `GROQ_MODEL` | `openai/gpt-oss-20b` | Дешёвый массовый слой; из моделей Groq strict JSON schema поддерживают только gpt-oss-20b/120b. |
| `GROQ_ESCALATION_MODEL` | `openai/gpt-oss-120b` | Пограничные случаи — та же strict-схема, точнее, дороже; таких запросов единицы. |
| `GROQ_MAX_COMPLETION_TOKENS` | `1200` | gpt-oss тратит токены на reasoning; ниже — обрезанный JSON и ретраи. |
| `EMBEDDING_PROVIDER/MODEL` | `openai` / `text-embedding-3-small` | $0.02 за 1M токенов, ноль RAM на Railway; `local` требует ~2 GB. |
| `EMBEDDING_DIM` | `1536` | Размерность text-embedding-3-small; вшита в миграцию — менять только с пересчётом векторов. |
| `TELEGRAM_SCAN_INTERVAL_MINUTES` | `15` | Отчёт раз в сутки — чаще читать смысла нет, а FloodWait на аккаунт растёт. |
| `GITHUB_SCAN_INTERVAL_MINUTES` | `120` | Search API — 30 запросов/мин; 12 прогонов в сутки закрывают окно в 30 дней с запасом. |
| `PIPELINE_INTERVAL_MINUTES` | `10` | Быстрее, чем копятся находки; Groq-лимиты при таком темпе не упираются. |
| `PIPELINE_BATCH_SIZE` | `120` | Укладывается в `task_soft_time_limit` 25 мин даже при медленном Groq. |
| `MIN_RELEVANCE_SCORE` | `0.65` | Строгий старт: пустой отчёт первые недели — признак работающего фильтра. |
| `MIN_DEEP_ANALYSIS_SCORE` | `0.70` | Выше relevance: deep analysis дорогой, туда идут только уверенные находки. |
| `MAX_DEEP_ANALYSES_PER_DAY` | `20` | Потолок расхода OpenAI; реально в день доходит 3–5. |
| `CRITICAL / RECOMMENDED / REVIEW_LATER` | `0.80 / 0.60 / 0.40` | Три корзины отчёта; ниже 0.40 — не показывать. |
| `DEDUP_COSINE_THRESHOLD` | `0.08` | Это *расстояние*: ловит перепосты, не склеивает соседние темы. |
| `DUPLICATE_FEATURE_THRESHOLD` | `0.85` | Похожесть на уже существующую фичу проекта → дубль, не рекомендация. |
| `GITHUB_MIN_STARS` | `40` | Дешёвый фильтр до Groq: ниже — шум из форков и учебных реп. |
| `GITHUB_MAX_REPO_AGE_DAYS` | `30` | Окно «новых» репозиториев для search. |
| `GITHUB_STALE_PUSH_DAYS` | `270` | Нет пушей 9 месяцев → заброшено, не рекомендуем. |
| `OPENAI_DEEP_ANALYSIS_ENABLED` | `false` | Ручной режим через COPY ANALYSIS CONTEXT; включать, когда пороги устаканились. |
| `TIMEZONE / RADAR_TIMEZONE` | `Asia/Aqtau` | Локальное время отчёта; UTC+5 без перехода на летнее. |
| `RADAR_DAILY_TIME` | `21:00` | Вечер: день собран, читать на следующее утро. |

Все пороги radar-блока можно менять без редеплоя: `PUT /api/settings/{key}`.

---

## 4. Что получить самому

### `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`
https://my.telegram.org → **API development tools** → создать приложение
(название любое, платформа Other). `api_id` — число, `api_hash` — 32 hex-символа.
Это USER-сессия, не Bot API: бот не видит каналы, на которые не подписан.

### `TELEGRAM_SESSION_STRING`
Скрипт уже в репозитории: `backend/scripts/gen_telegram_session.py`.
Запускать **локально и один раз** — на сервере интерактивный логин невозможен,
а повторные логины наказываются нарастающим FloodWait.

```powershell
cd backend
pip install "telethon>=1.38"
$env:TELEGRAM_API_ID="<api_id>"; $env:TELEGRAM_API_HASH="<api_hash>"
python scripts/gen_telegram_session.py
```

Спросит телефон, код из Telegram, пароль 2FA (если включён). Напечатает
`TELEGRAM_SESSION_STRING=...` — строку целиком в Railway. Строка = полный
доступ к аккаунту: не коммитить, не пересылать, в логи не попадает (редактор
секретов вырезает всё с `SESSION` в имени).

### `GITHUB_TOKEN`
https://github.com/settings/tokens

- **Fine-grained** (рекомендуется): Repository access → *Public repositories*;
  permissions не нужны — Search и чтение публичных реп работают без них.
  Если техревизия должна читать твои **приватные** репозитории проектов —
  Repository access → *Only select repositories* → Contents: Read, Metadata: Read.
- **Classic**: scope `public_repo`; для приватных — `repo`.

Без токена лимит 60 запросов/час и сбор встанет; с токеном — 5000/час
и Search 30/мин.

### `GROQ_API_KEY`
https://console.groq.com → **API Keys** → Create. Free tier хватает на старт;
`openai/gpt-oss-20b` и `gpt-oss-120b` доступны на нём.

### `OPENAI_API_KEY`
https://platform.openai.com/api-keys — нужен **даже при выключенном deep
analysis**: на нём эмбеддинги. Для project-ключа хватает permission
*Model capabilities*. Без него — `EMBEDDING_PROVIDER=null`: pipeline работает,
но семантическая дедупликация бесполезна.

### `SECRET_KEY`, `ADMIN_PASSWORD`, `POSTGRES_PASSWORD`
Любая длинная случайная строка. Сгенерировать:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(24))"   # ADMIN_PASSWORD
python -c "import secrets; print(secrets.token_hex(24))"       # POSTGRES_PASSWORD (без спецсимволов — безопасно для URL)
```

Без Python: `openssl rand -base64 48`. Для `POSTGRES_PASSWORD` избегай
`@ : / ? # %` — они ломают `DATABASE_URL`.

---

## 5. Формат `TELEGRAM_SOURCE_IDS`

Через запятую, без пробелов вокруг (пробелы обрезаются, но не полагайся).
Надёжнее численные `-100…` id: username канал может сменить, id — нет.

```env
TELEGRAM_SOURCE_IDS=-1001234567890,-1009876543210,@ai_newz
```

- `-1001234567890` — канал или супергруппа (префикс `-100` обязателен);
- `@ai_newz` — публичный username, резолвится при первом проходе;
- приватные каналы — **только** численным id.

Получить id по username (нужна уже готовая сессия в `.env`):

```powershell
cd backend
python scripts/resolve_telegram_ids.py @ai_newz @seeallochnaya
# → TELEGRAM_SOURCE_IDS=-1001234567890,-1009876543210
```

Список читается один раз при старте `telegram`-сервиса и заводится в таблицу
`sources`; дальше источниками управляют через UI / `/api/sources`, а
переменную можно не трогать.

---

## 6. Порядок при деплое

**До первого запуска** (иначе api не поднимется или поднимется бесполезным):

1. `Postgres` (блок §1) и `Redis` — дождаться, что оба зелёные.
2. `api`: весь блок. Минимум секретов — `SECRET_KEY`, `ADMIN_PASSWORD`,
   `GITHUB_TOKEN`, `GROQ_API_KEY`, `OPENAI_API_KEY`.
   `TELEGRAM_SESSION_STRING` можно оставить пустым.
3. Deploy `api` → `GET /health/deep` через приватку или временный домен →
   `"pgvector": true`. Если `false` — Postgres не pgvector, назад к §1.
4. `web`: блок целиком, выдать публичный домен. `CORS_ORIGINS` на `api`
   подхватится сам через референс.
5. `worker` и `scheduler` — блоки целиком (Telegram-креды у worker можно
   пока пустыми: доставка отчёта просто залогирует warning).
6. Завести проекты через UI (`Projects` → `owner/name`) — без профилей
   pipeline останавливается, оценивать не относительно чего.

**Можно потом:**

7. Telegram: получить `api_id/api_hash`, сгенерировать сессию (§4),
   собрать `TELEGRAM_SOURCE_IDS` (§5) → заполнить в `telegram` и `worker`,
   добавить `TELEGRAM_SESSION_STRING` в `api` (для статуса) → Deploy `telegram`.
8. Подкрутить пороги через `PUT /api/settings/{key}` после первых 2–3 отчётов.
9. `OPENAI_DEEP_ANALYSIS_ENABLED=true` на `worker` — когда ручной режим через
   COPY ANALYSIS CONTEXT перестал устраивать.

Общее правило: секреты одинаковы во всех сервисах, где встречаются
(`SECRET_KEY`, `ADMIN_PASSWORD`, ключи API). Если надоест дублировать —
Railway **Shared Variables** на уровне проекта и `${{shared.GROQ_API_KEY}}`
в сервисах; поведение то же.
