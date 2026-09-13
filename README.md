# Project Radar

Персональный технический радар проектов.

**Найти → проверить → сопоставить с конкретным проектом → оценить реальную пользу → убрать дубли и шум → сохранить перспективное.**

Это не новостной агрегатор. Критерий качества — минимум шума при высокой полезности. Если за сутки ничего значимого нет, отчёт честно пишет «Сегодня значимых находок нет».

---

## Что внутри

| | |
|---|---|
| **Источники v1** | Telegram (Telethon, user session) · собственный GitHub Radar |
| **Дедупликация** | по `github_id`, `full_name`, нормализованному URL, заголовку и семантически через pgvector |
| **Оценка** | 9 независимых метрик + `radar_score`, всегда **относительно конкретного проекта** |
| **Решения** | CRITICAL / RECOMMENDED / REVIEW_LATER / ARCHIVED / REJECTED, всегда с текстовой причиной |
| **REJECTED не навсегда** | триггеры переоценки: major release, рост звёзд, смена лицензии, возобновление активности |
| **Отчёт** | Daily Radar в 21:00, окно — предыдущие 24 часа, Asia/Aqtau |

Подробности: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · схема данных: [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md)

---

## Стек

Python 3.12 · FastAPI · PostgreSQL 17 + pgvector · Redis · Celery (worker + beat) · Telethon · GitHub REST/GraphQL · Groq · OpenAI (опционально) · Next.js 15 · Docker · Railway

---

## Быстрый старт локально

```bash
git clone https://github.com/andyrbek2709-tech/project-radar.git
cd project-radar

cp .env.example .env
#   Минимум для первого запуска: GITHUB_TOKEN и GROQ_API_KEY.
#   Без них система поднимется, но сбор и классификация работать не будут.
#   Telegram можно подключить позже.

docker compose up -d --build
```

Поднимется: Postgres с pgvector, Redis, миграции, api, worker, scheduler, telegram, web.

- UI — http://localhost:3000
- API и Swagger — http://localhost:8000/docs
- Проверка БД и расширения — http://localhost:8000/health/deep

Дальше:

```bash
# 1. Завести проекты (профили — эталон, относительно которого всё оценивается)
docker compose exec api python scripts/seed_projects.py

# 2. Первый прогон радара
curl -u admin:$ADMIN_PASSWORD -X POST http://localhost:8000/api/trigger/collect-github
curl -u admin:$ADMIN_PASSWORD -X POST http://localhost:8000/api/trigger/process-pipeline

# 3. Построить отчёт, не дожидаясь 21:00
curl -u admin:$ADMIN_PASSWORD -X POST "http://localhost:8000/api/trigger/daily-radar?force=true"
```

Тесты (БД и ключи не нужны — проверяется чистая логика):

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

---

## Подключение Telegram

Telegram работает через **user session**, а не Bot API: боты не могут читать чужие каналы.

```bash
cd backend
python scripts/gen_telegram_session.py        # локально, один раз
```

Скрипт выведет `TELEGRAM_SESSION_STRING` — положи её в `.env` и в переменные Railway.

> Эта строка даёт полный доступ к аккаунту. В git она попасть не должна (`.gitignore` уже это закрывает), в логи — тоже (редактор секретов в `app/core/logging.py`).

Каналы по `@username` превращаются в устойчивые численные id:

```bash
python scripts/resolve_telegram_ids.py @channel1 @channel2
# → TELEGRAM_SOURCE_IDS=-1001234567890,-1009876543210
```

Ориентир по нагрузке: **15–25 источников при опросе раз в 15 минут** — безопасно.

---

## Деплой на Railway

### Шаг 1. Базы

Создай проект из этого репозитория, затем добавь два сервиса:

1. **Postgres с pgvector.** Штатный Railway Postgres **не содержит pgvector** — без него не работает ни семантическая дедупликация, ни сравнение находки с профилем проекта.
   `+ New` → `Docker Image` → `pgvector/pgvector:pg17`, переменные `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, том на `/var/lib/postgresql/data`.
   (Альтернатива — готовый pgvector-шаблон из маркетплейса Railway.)
2. **Redis** — `+ New` → `Database` → `Redis`.

### Шаг 2. Пять сервисов из этого репозитория

Каждый — из одного и того же репозитория, **Root Directory оставить `/`**, различается только `Config file path`:

| Сервис | Config file path | Публичный домен |
|---|---|---|
| `api` | `railway.json` | нет¹ |
| `worker` | `railway.worker.json` | нет |
| `scheduler` | `railway.scheduler.json` | нет |
| `telegram` | `railway.telegram.json` | нет |
| `web` | `railway.web.json` | **да** |

¹ Домен для `api` не нужен: `web` ходит к нему по приватной сети. Если хочешь Swagger снаружи — выдай домен, но тогда обязательно `AUTH_ENABLED=true` и непустой `ADMIN_PASSWORD`.

Сервисам без HTTP-порта healthcheck и домен не настраиваются — Railway такие сервисы не убивает.

### Шаг 3. Переменные

> Готовые блоки `KEY=value` под каждый сервис (для Raw Editor), рекомендованные значения с обоснованием, где брать ключи и в каком порядке заполнять — в **[docs/RAILWAY_VARIABLES.md](docs/RAILWAY_VARIABLES.md)**. Ниже — только суть.

Общие для `api`, `worker`, `scheduler`, `telegram`:

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
REDIS_URL    = ${{Redis.REDIS_URL}}

APP_ENV      = production
SECRET_KEY   = <длинная случайная строка>
TIMEZONE     = Asia/Aqtau
RADAR_TIMEZONE = Asia/Aqtau

GITHUB_TOKEN = ghp_...
GROQ_API_KEY = gsk_...
OPENAI_API_KEY = sk-...            # нужен для эмбеддингов

EMBEDDING_PROVIDER = openai
EMBEDDING_MODEL    = text-embedding-3-small
EMBEDDING_DIM      = 1536

ADMIN_USERNAME = admin
ADMIN_PASSWORD = <длинный пароль>
```

Дополнительно для `telegram`:

```
TELEGRAM_API_ID         = ...
TELEGRAM_API_HASH       = ...
TELEGRAM_SESSION_STRING = ...
TELEGRAM_SOURCE_IDS     = -1001234567890,-1009876543210
```

Для `web` (и `PORT = 8000` на `api`, чтобы приватный адрес был предсказуемым):

```
API_URL             = http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000
ADMIN_USERNAME      = admin
ADMIN_PASSWORD      = <тот же пароль>
```

> `ADMIN_*` у `web` — обычные переменные, не `NEXT_PUBLIC_*`: Basic-авторизация добавляется в серверном прокси `web/app/api/proxy`, в браузер креды не попадают.

Для `api` добавь `CORS_ORIGINS = https://<домен web>`.

### Шаг 4. Порядок первого запуска

1. Задеплоить `api` — он сам применит миграции (`alembic upgrade head` в стартовой команде) и создаст расширение `vector`.
2. Проверить `GET https://<api>/health/deep` — должно быть `"pgvector": true`. Если `false`, значит Postgres без pgvector, вернись к шагу 1.
3. Поднять `worker`, `scheduler`, `web`.
4. Завести проекты — через UI (`Projects` → новый проект с указанием `owner/name`) или `scripts/seed_projects.py`.
5. Только после появления проектов поднимать `telegram` и запускать сбор: без профилей pipeline останавливается, оценивать находки не относительно чего.

### Про `railway.json` и декабрь 2026

Railway объявил Config-as-Code устаревшим: `railway.json` / `railway.toml` работают **до 01.12.2026**, дальше — только Infrastructure as Code. В репозитории лежат оба формата: `railway*.json` для сегодняшнего деплоя и `.railway/railway.ts` как готовая замена. Переключение — настройка в дашборде.

---

## Принятые решения

Решения приняты автономно; здесь зафиксировано, что выбрано и почему — чтобы можно было пересмотреть осознанно.

| Развилка | Выбор | Почему |
|---|---|---|
| **Эмбеддинги** | OpenAI `text-embedding-3-small`, 1536 | У Groq эмбеддингов **нет** (проверено на первоисточнике). Локальная multilingual-модель — это +2 GB RAM, а RAM на Railway стоит ~$10/GB в месяц; $0.02 за 1M токенов дешевле. Локальный провайдер реализован и включается одной переменной. Есть третий режим `null` — детерминированная заглушка, чтобы pipeline работал вообще без ключей. |
| **Размерность вектора** | 1536 | И 1024, и 1536 меньше предела индексации pgvector в 2000 — индексируются напрямую. 3072-мерные модели потребовали бы `halfvec`, это лишняя сложность. Размерность зашита в миграцию: менять после первого `upgrade head` нельзя без пересчёта всех векторов. |
| **PostgreSQL** | свой сервис `pgvector/pgvector:pg17` | Штатный Postgres Railway pgvector не содержит. Свой образ = контроль версии и одинаковое окружение локально и в проде. |
| **Конфиг Railway** | оба формата | `railway.json` отключают 01.12.2026. Положить `.railway/railway.ts` сейчас стоит десять минут, возвращаться в ноябре под дедлайн — дороже. |
| **Celery beat** | отдельный сервис + `redbeat` | `worker -B` не масштабируется: две реплики = двойное расписание. Файловое расписание теряется при редеплое (эфемерная ФС), redbeat хранит его в Redis и блокирует дубли. |
| **Telegram** | отдельный долгоживущий процесс | Telethon держит долгую MTProto-сессию. Логин на каждую Celery-таску Telegram расценивает как подозрительную активность: 7 секунд FloodWait превращаются в часы. Цена — +1 сервис (~$5/мес). |
| **GitHub-клиент** | свой на `httpx` | Поведение вокруг лимитов (search 30/мин, потолок 1000 результатов, ETag/304, 202 у `/stats/commit_activity`) — это суть коллектора, а не деталь. Прятать его за чужой абстракцией дороже, чем держать ~300 строк своих. |
| **Модель Groq** | `openai/gpt-oss-20b`, эскалация на `120b` | Строгий JSON-схемой режим у Groq поддерживают **только** `gpt-oss-20b` и `gpt-oss-120b`; у остальных `strict` молча игнорируется. 20b вдвое дешевле ($0.075/$0.30 против $0.15/$0.60); при сломанной схеме тот же вход уходит на 120b. |
| **Deep Analysis** | MANUAL по умолчанию | `OPENAI_DEEP_ANALYSIS_ENABLED=false`. Кандидат ждёт, кнопка **COPY ANALYSIS CONTEXT** собирает готовый промпт, ответ вставляется обратно через API и ложится в ту же таблицу. Ноль трат, разбор остаётся в базе. AUTO включается одной переменной. |
| **Пороги** | строгие | `MIN_RELEVANCE=0.65`, deep `≥0.70`, CRITICAL `≥0.80`. Первые недели отчёты будут короткими и часто пустыми — это признак работающего фильтра. Ослабить проще, чем разгребать ленту; пороги лежат в таблице `settings` и меняются без редеплоя. |
| **Доставка отчёта** | UI + Telegram «Избранное» | Той же user-session, отдельный бот не нужен. Отчёт, за которым надо специально заходить, перестают читать через неделю. |
| **Фронтенд** | Next.js + чистый CSS | Без Tailwind: один CSS-файл с токенами даёт полный контроль над тёмной темой и не зависит от версий PostCSS при сборке. |
| **Авторизация** | HTTP Basic | Пользователь один. Multi-user и OAuth не нужны, но и голым наружу сервис торчать не должен. |
| **Приоритет сборки** | GitHub Radar сквозняком | Не требует Telegram-сессии — запускается сразу после заполнения `.env` и даёт рабочий вертикальный срез всего pipeline. |

---

## Что проверено, а что нет

**Проверено логикой и тестами** (`backend/tests`, 60+ проверок, БД и ключи не нужны):

- нормализация URL, схлопывание одной ссылки из разных каналов в одну находку;
- отсев служебных путей `github.com` (`/trending`, `/features`, `/marketplace`);
- дешёвый фильтр: вакансии, реклама, негативные ключевые слова, archived/stale репы;
- скоринг: звёзды не доминируют, насыщение работает, дубль фичи обнуляет `improvement`;
- движок решений: каждый вердикт имеет непустую причину и упоминает проект;
- триггеры переоценки, включая различение `v1.4.2 → v2.0.0` и `v1.4.2 → v1.5.0`;
- редактирование секретов в логах — по имени ключа, по значению и в свободном тексте.

**Найдено и исправлено вычиткой** (два независимых прохода по всему коду):

- `sync_database_url` не подставлял драйвер для `postgresql://` — ровно того формата, что отдаёт Railway. Упало бы при старте worker, scheduler и `alembic upgrade` с `ModuleNotFoundError: psycopg2`;
- `crontab(minute="*/120")` — поле `minute` это 0–59, beat не поднялся бы при `GITHUB_SCAN_INTERVAL_MINUTES=120`;
- валидатор `relevance` без `mode="before"` — значение `1.02` от модели давало бы `ValidationError` и лишнюю эскалацию на дорогую модель;
- свободный словарь в JSON-схеме репо-аудита — strict-режим требует `additionalProperties: false`, был бы HTTP 400 на каждой техревизии;
- `UNIQUE` с nullable `project_id` — в PostgreSQL `NULL != NULL`, идемпотентность не работала для классификации; заменено двумя частичными индексами;
- ветка «REJECTED не навсегда» была мертва: триггеры ставили статус `ANALYZED`, а pipeline смотрел только на новое сырьё. Добавлен второй проход `reanalyse_pending`;
- `force=True` отдавал бы в Telegram вчерашний отчёт: Core-upsert прошёл мимо identity map;
- поиск репозитория по `full_name` был регистрозависим, ссылки из Telegram не находили запись;
- `compare_digest` на не-ASCII пароле давал 500 вместо 401;
- месяц в статистике стоимости начинался с 1-го числа 19:00 UTC и терял первый день;
- находка помечалась `DECIDED`, даже если все сопоставления упали — и выпадала из обработки навсегда.

**НЕ проверено — в этом окружении не было ни shell, ни Docker, ни БД:**

- `alembic upgrade head` на живой базе;
- `docker compose up` и сборка образов;
- реальные ответы Groq, GitHub и Telegram (код написан по документации, не по прогону);
- сборка Next.js (`npm run build`);
- деплой на Railway;
- формат `.railway/railway.ts` — Infrastructure as Code у Railway новый, перед переключением сверься с документацией.

**Известное отклонение, оставленное сознательно:** миграция `0001` создаёт constraint'ы с
дефолтными именами PostgreSQL (`projects_pkey`), а не по `NAMING_CONVENTION` из моделей
(`pk_projects`). На рантайм это не влияет — код нигде не адресует их по имени, — но первый
`alembic revision --autogenerate` увидит расхождение. Если планируешь активно пользоваться
autogenerate, имена в миграции стоит проставить явно.

Первое, что стоит сделать после клона: `docker compose up -d --build` и `GET /health/deep`. Если там `"pgvector": true` — фундамент стоит.

---

## Структура

```
backend/
  app/
    core/          config · logging (с редактором секретов) · db
    models/        SQLAlchemy: projects · sources · repositories · findings · analysis
    analysis/      embeddings · llm (Groq/OpenAI) · schemas · prompts
    collectors/    github_client · github_radar · telegram_collector · telegram_runner
    services/      normalizer · dedup · scoring · decision_engine · pipeline
                   deep_analysis · profiler · radar_report · reassessment
                   context_prompt · usage
    api/           deps · routes_projects · routes_findings · routes_ops
    tasks/         celery_app (расписание) · jobs
  alembic/         миграции
  scripts/         gen_telegram_session · resolve_telegram_ids · seed_projects
  tests/           normalizer · scoring/decisions · redaction логов
web/
  app/             layout · dashboard · radar · projects · sources · reports · settings
  components/      Nav · FindingCard · CopyContextButton · ScoreBar
  lib/             api · types
docs/
  ARCHITECTURE.md  архитектура, pipeline, решения
  DATA_MODEL.md    схема данных — главный артефакт
```

---

## Безопасность

- Секреты только в environment variables. В коде и в таблице `settings` их нет — попытка записать ключ с «секретным» именем через API отклоняется.
- В логах секреты вырезаются по имени ключа, по значению из окружения и по шаблонам в свободном тексте, включая вложенные структуры и тексты исключений.
- `.gitignore` закрывает `.env`, `*.session` и производные.
- `TELEGRAM_SESSION_STRING` = полный доступ к аккаунту. Обращаться как с паролем.
