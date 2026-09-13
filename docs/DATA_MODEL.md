# Project Radar — схема данных

> Главный артефакт проекта. Всё остальное — обвязка вокруг этих таблиц.
> PostgreSQL 16/17 + pgvector 0.8+. Миграции — Alembic.

## 0. Принципы

1. **Сырое отделено от нормализованного.** `raw_items` — то, что реально пришло из источника, неизменяемое. `findings` — одна сущность на одну реальную находку, независимо от того, сколько раз и откуда она всплыла.
2. **История метрик — отдельная таблица снапшотов.** Без неё нельзя отличить репу «2200 звёзд и стоит три года» от «150 → 870 → 2200 за две недели».
3. **Оценка всегда относительно проекта.** Ни одна оценка не живёт на находке в одиночку — она живёт в `finding_project_matches` (находка × проект).
4. **Решение всегда с причиной.** `decisions.reason` — NOT NULL. REJECTED без текстовой причины запрещён на уровне схемы.
5. **REJECTED не навсегда.** `reassessment_triggers` фиксирует, при каком событии находку вернуть на стол.
6. **Векторы живут рядом с сущностью,** а не в общей таблице эмбеддингов: так дешевле джойнить и проще индексировать.

## 1. Карта таблиц

```
                       ┌──────────────┐
                       │   projects   │◄──────┐
                       └──────┬───────┘       │
             ┌────────────────┼──────────┐    │
             ▼                ▼          ▼    │
   project_features  project_keywords  project_repo_audits
                                                │
  ┌─────────────┐    ┌─────────────┐            │
  │   sources   │───►│ source_cursors            │
  └──────┬──────┘    └─────────────┘            │
         ▼                                      │
  ┌─────────────┐   ┌──────────────────┐        │
  │  raw_items  │   │  repositories    │        │
  └──────┬──────┘   └────────┬─────────┘        │
         │                   ▼                  │
         │          repository_snapshots        │
         │                   │                  │
         ▼                   ▼                  │
  ┌────────────────────────────────┐            │
  │           findings             │            │
  └───┬──────────┬─────────┬───────┘            │
      │          │         │                    │
      ▼          ▼         ▼                    │
finding_keys  finding_  finding_project_matches─┘
              sources          │
                               ▼
                    ┌──────────────────┐
                    │ finding_analyses │  (groq / deep)
                    └──────────────────┘
                               │
                    ┌──────────┴───────────┐
                    ▼                      ▼
               decisions          reassessment_triggers
                    │
                    ▼
              review_queue

  daily_reports · ai_usage_events · collector_runs · settings
```

---

## 2. Справочники и проекты

### `projects` — Project Profile

Карточка проекта пользователя. Это **эталон**, относительно которого оценивается всё.

| поле | тип | описание |
|---|---|---|
| id | uuid PK | |
| slug | text UNIQUE NOT NULL | `enghub`, `vformate`, `trackparts` |
| name | text NOT NULL | |
| description | text | |
| business_purpose | text | зачем проект существует для бизнеса |
| current_stack | jsonb NOT NULL default `{}` | `{"backend":["FastAPI","PostgreSQL"],"frontend":["Next.js"],"ai":["OpenAI"],"infra":["Docker","Railway"]}` |
| existing_architecture | text | текстовое описание |
| integrations | text[] | |
| current_problems | text[] | болевые точки — самый сильный сигнал релевантности |
| planned_features | text[] | |
| technology_interests | text[] | |
| search_keywords | text[] | позитивные ключи для GitHub search |
| negative_keywords | text[] | шумовые слова → срезаются на cheap filter |
| things_not_needed | text[] | «нам не нужен k8s», «не нужен Kafka» |
| priority_areas | text[] | куда сейчас смотрим в первую очередь |
| github_repository | text | `owner/name` |
| profile_source | text NOT NULL | `manual` \| `auto_audit` \| `hybrid` |
| profile_locked_fields | text[] | поля, которые правил человек → автопрофилировщик их не трогает |
| is_active | bool NOT NULL default true |
| embedding | vector(EMB_DIM) | эмбеддинг сводного текста профиля |
| profile_text | text | сгенерированный текст, из которого посчитан embedding |
| created_at / updated_at | timestamptz | |

Индексы: `UNIQUE(slug)`, `hnsw (embedding vector_cosine_ops)` where is_active.

### `project_features` — «что у нас уже есть»

Отдельная таблица, потому что сравнение «находка vs существующая фича» — векторное и построчное.

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| project_id | uuid FK→projects ON DELETE CASCADE | |
| name | text NOT NULL | «Парсинг PDF через pdfplumber» |
| description | text | |
| category | text | `rag`, `auth`, `parsing`, `infra`… |
| source | text | `auto_audit` \| `manual` |
| evidence | jsonb | откуда взяли: `{"file":"requirements.txt","match":"pdfplumber==0.11"}` |
| embedding | vector(EMB_DIM) | |
| created_at | timestamptz | |

Индексы: `(project_id)`, `hnsw (embedding vector_cosine_ops)`.

### `project_repo_audits` — техревизия репозитория проекта

Лог автопрофилирования. Хранит сырьё, чтобы можно было перепрогнать профиль без повторного похода в GitHub.

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| project_id | uuid FK→projects | |
| repo_full_name | text NOT NULL | |
| commit_sha | text | на каком коммите ревизовали |
| files_collected | jsonb | `{"README.md": "...", "pyproject.toml": "...", "docker-compose.yml": "..."}` (усечённые) |
| detected_stack | jsonb | результат детектора |
| detected_features | jsonb | список фич до записи в project_features |
| llm_model | text | чем разбирали |
| status | text | `pending` \| `ok` \| `failed` |
| error | text | |
| created_at | timestamptz | |

---

## 3. Источники и сбор

### `sources` — реестр источников (плагинная архитектура)

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| kind | text NOT NULL | `telegram` \| `github_search` \| `github_watch` \| `rss` \| `hn` \| `reddit` (v2+) |
| external_id | text NOT NULL | `-1001234567890` / `repo-search:rag-python` |
| title | text | |
| config | jsonb NOT NULL default `{}` | для github_search — сам query; для telegram — лимиты истории |
| is_active | bool NOT NULL default true | |
| last_run_at | timestamptz | |
| last_error | text | |
| created_at / updated_at | timestamptz | |

`UNIQUE(kind, external_id)`.

### `source_cursors` — где остановились

Отдельно от `sources`, потому что курсор обновляется на каждом прогоне и это горячая запись.

| поле | тип | |
|---|---|---|
| source_id | uuid PK FK→sources ON DELETE CASCADE | |
| last_item_id | text | telegram: `message.id`; github: ISO-дата последнего `created` |
| etag | text | GitHub `If-None-Match` — 304 не тратит лимит |
| last_modified | text | |
| cursor_data | jsonb | произвольное состояние плагина |
| updated_at | timestamptz | |

### `raw_items` — сырьё, неизменяемое

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| source_id | uuid FK→sources | |
| external_id | text NOT NULL | `tg:-1001234:98765` / `gh:repo:123456789` |
| kind | text NOT NULL | `telegram_message` \| `github_repo` |
| payload | jsonb NOT NULL | как пришло |
| content_text | text | извлечённый текст для дешёвого фильтра |
| urls | text[] | все URL |
| github_urls | text[] | подмножество — GitHub-ссылки |
| author | text | |
| published_at | timestamptz | дата в источнике |
| collected_at | timestamptz NOT NULL default now() | |
| processing_status | text NOT NULL default `pending` | `pending` \| `filtered_out` \| `processed` \| `error` |
| filter_reason | text | почему срезали на cheap filter |
| error | text | |

Индексы:
- `UNIQUE(source_id, external_id)` — **главная защита от повторного анализа**
- `(processing_status, collected_at)` — выборка очереди
- `GIN (github_urls)`
- `(published_at DESC)`

### `repositories` — GitHub-репозиторий как сущность

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| github_id | bigint UNIQUE NOT NULL | стабилен при переименовании — ключ дедупликации №1 |
| full_name | text NOT NULL | `owner/name`; UNIQUE, но может меняться |
| owner / name | text NOT NULL | |
| url | text NOT NULL | |
| description | text | |
| language | text | |
| topics | text[] | |
| license_spdx | text | |
| default_branch | text | |
| homepage | text | |
| is_fork | bool | |
| parent_full_name | text | для «интересных форков» |
| archived / disabled | bool | |
| created_at_gh / updated_at_gh / pushed_at_gh | timestamptz | |
| latest_release_tag | text | |
| latest_release_at | timestamptz | |
| readme_text | text | |
| readme_fetched_at | timestamptz | |
| readme_etag | text | |
| contributors_count | int | |
| stars / forks / watchers / open_issues | int | **текущее** значение; история — в снапшотах |
| etag | text | для условных запросов метаданных |
| first_seen_at | timestamptz NOT NULL | |
| last_checked_at | timestamptz | |

Индексы: `UNIQUE(github_id)`, `UNIQUE(full_name)`, `GIN(topics)`, `(pushed_at_gh DESC)`.

### `repository_snapshots` — история метрик ★

Без этой таблицы «быстро растущие репы» детектировать нечем: `/stargazers` c 30.06.2026 закрыт для не-коллабораторов, официального trending API нет.

| поле | тип | |
|---|---|---|
| id | bigserial PK | |
| repository_id | uuid FK→repositories ON DELETE CASCADE | |
| captured_on | date NOT NULL | дата (не timestamp) — один снапшот в сутки |
| stars / forks / watchers / open_issues | int | |
| contributors_count | int | |
| latest_release_tag | text | |
| pushed_at_gh | timestamptz | |
| commits_last_week | int | из `/stats/commit_activity` |
| license_spdx | text | смена лицензии — триггер переоценки |

`UNIQUE(repository_id, captured_on)`, индекс `(repository_id, captured_on DESC)`.

Производные (считаются запросом, не хранятся):
```sql
-- прирост звёзд за 14 дней
SELECT r.full_name,
       s_now.stars,
       s_now.stars - s_old.stars                                   AS stars_delta,
       (s_now.stars - s_old.stars)::float / NULLIF(s_old.stars,0)  AS stars_growth_ratio
FROM repositories r
JOIN LATERAL (SELECT * FROM repository_snapshots
              WHERE repository_id=r.id ORDER BY captured_on DESC LIMIT 1) s_now ON true
JOIN LATERAL (SELECT * FROM repository_snapshots
              WHERE repository_id=r.id AND captured_on <= current_date - 14
              ORDER BY captured_on DESC LIMIT 1) s_old ON true;
```

---

## 4. Находки

### `findings` — одна находка = одна строка

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| kind | text NOT NULL | `repository` \| `article` \| `release` \| `tool` \| `discussion` |
| title | text NOT NULL | |
| url | text | канонический |
| normalized_url | text | схема/www/utm/trailing slash срезаны |
| repository_id | uuid FK→repositories NULL | если находка — репа |
| summary | text | короткое описание (Groq) |
| content_text | text | текст для эмбеддинга |
| embedding | vector(EMB_DIM) | |
| found_via | text[] NOT NULL default `{}` | `{telegram,github_radar}` — денормализация для UI |
| first_seen_at | timestamptz NOT NULL | |
| last_seen_at | timestamptz NOT NULL | |
| seen_count | int NOT NULL default 1 | повторное появление → +confidence |
| status | text NOT NULL default `new` | `new`→`analyzing`→`analyzed`→`decided` |
| pipeline_stage | text | последняя пройденная стадия |
| is_noise | bool NOT NULL default false | |
| created_at / updated_at | timestamptz | |

Индексы: `hnsw (embedding vector_cosine_ops)`, `(status, first_seen_at DESC)`, `UNIQUE(repository_id)` partial where repository_id is not null — одна репа = одна находка.

### `finding_keys` — универсальные ключи дедупликации

Вместо пяти UNIQUE-колонок — одна таблица ключей. Новый тип ключа добавляется без миграции схемы.

| поле | тип | |
|---|---|---|
| id | bigserial PK | |
| finding_id | uuid FK→findings ON DELETE CASCADE | |
| key_type | text NOT NULL | `url` \| `normalized_url` \| `github_full_name` \| `github_id` \| `title_hash` \| `doi` |
| key_value | text NOT NULL | |

`UNIQUE(key_type, key_value)` — попытка вставить существующий ключ = обнаружен дубль.
Индекс `(finding_id)`.

**Порядок дедупликации** (дёшево → дорого):
1. `github_id` → точное совпадение
2. `github_full_name`
3. `normalized_url`
4. `url`
5. `title_hash` (нормализованный заголовок, sha256)
6. семантика: `embedding <=> candidate < DEDUP_COSINE_THRESHOLD` (по умолчанию 0.08) **и** совпадение хотя бы одной категории

### `finding_sources` — откуда пришла (m2m)

| поле | тип | |
|---|---|---|
| finding_id | uuid FK→findings ON DELETE CASCADE | |
| raw_item_id | uuid FK→raw_items ON DELETE CASCADE | |
| source_id | uuid FK→sources | |
| seen_at | timestamptz NOT NULL | |
| dedup_method | text | каким ключом склеили: `github_id` / `semantic` / `new` |
| dedup_score | float | для семантики — косинусная близость |

PK `(finding_id, raw_item_id)`.
Триггер/сервис при вставке: `findings.seen_count += 1`, `last_seen_at = now()`, `found_via` дополняется `sources.kind`.

---

## 5. Анализ и сопоставление с проектом

### `finding_project_matches` — ★ ядро полезности

Именно здесь живут все оценки. Одна находка × один проект = одна строка.

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| finding_id | uuid FK→findings ON DELETE CASCADE | |
| project_id | uuid FK→projects ON DELETE CASCADE | |
| **relevance_score** | float | 0..1 — насколько вообще про нас |
| **novelty_score** | float | 0..1 — новизна относительно того, что мы уже видели |
| **project_fit_score** | float | 0..1 — попадание в стек/проблемы/приоритеты |
| **improvement_score** | float | 0..1 — насколько лучше текущего решения |
| **maturity_score** | float | 0..1 — зрелость (возраст, релизы, лицензия, тесты) |
| **activity_score** | float | 0..1 — живость (pushed_at, коммиты, контрибьюторы) |
| **implementation_cost_score** | float | 0..1, **больше = дороже внедрять** |
| **risk_score** | float | 0..1, **больше = рискованнее** |
| **confidence_score** | float | 0..1 — уверенность системы |
| **radar_score** | float | итог, формула ниже |
| score_breakdown | jsonb | вклад каждого слагаемого — для объяснимости в UI |
| max_similarity_to_features | float | косинус к ближайшей `project_features` |
| nearest_feature_id | uuid FK→project_features | «дублирует вот это» |
| max_similarity_to_past | float | косинус к ближайшей прошлой находке |
| nearest_past_finding_id | uuid FK→findings | |
| categories | text[] | `{RAG, "Document AI"}` |
| why_relevant | text | «EngHub сейчас использует X; это предлагает Y…» |
| what_we_have | text | |
| what_it_offers | text | |
| advantages | text[] | |
| disadvantages | text[] | |
| integration_complexity | text | `low` \| `medium` \| `high` |
| recommend_deep_analysis | bool | |
| created_at / updated_at | timestamptz | |

`UNIQUE(finding_id, project_id)`; индексы `(project_id, radar_score DESC)`, `(radar_score DESC)`.

**Формула radar_score** (веса — в `settings`, не в коде):

```
base = 0.25*relevance + 0.20*project_fit + 0.20*improvement
     + 0.15*novelty   + 0.10*maturity    + 0.10*activity

radar_score = clamp01( base
                     - 0.15*implementation_cost
                     - 0.15*risk )
              * (0.6 + 0.4*confidence)
```

Звёзды в формуле **отсутствуют**: они входят только в `activity_score`/`maturity_score` и с насыщением (`log10`), чтобы 50k-звёздная популярная штука не перебивала точное попадание в задачу.

### `finding_analyses` — результаты LLM, версионируемые

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| finding_id | uuid FK→findings ON DELETE CASCADE | |
| project_id | uuid FK→projects NULL | NULL = общая классификация (Groq-слой) |
| analysis_type | text NOT NULL | `groq_classification` \| `project_match` \| `deep_analysis` \| `manual_chatgpt` |
| provider | text NOT NULL | `groq` \| `openai` \| `manual` |
| model | text NOT NULL | |
| prompt_version | text NOT NULL | `v1` — чтобы отличать результаты разных промптов |
| input_digest | text | sha256 входа — не гонять одно и то же дважды |
| result | jsonb NOT NULL | валидированный pydantic-ответ |
| raw_response | text | на случай разбора багов |
| status | text NOT NULL | `ok` \| `invalid_json` \| `schema_error` \| `api_error` |
| error | text | |
| prompt_tokens / completion_tokens | int | |
| cost_usd | numeric(12,6) | |
| latency_ms | int | |
| created_at | timestamptz | |

Индексы: `(finding_id, analysis_type)`, `UNIQUE(finding_id, project_id, analysis_type, prompt_version, input_digest)` — идемпотентность.

**Deep analysis** кладёт в `result` структуру:
```json
{
  "what_it_does": "...", "what_we_have": "...", "real_advantage": true,
  "replaces": ["pdfplumber"], "complements": ["Qdrant"],
  "implementation_complexity": "medium", "new_services_required": ["Redis Stack"],
  "new_dependencies": ["docling>=2.0"], "maintenance_burden": "low",
  "vendor_lock_in": "none", "security_risks": ["..."], "license_risks": "Apache-2.0, ok",
  "migration_complexity": "low", "expected_benefit": "...",
  "verdict": "RECOMMENDED", "verdict_reason": "..."
}
```

---

## 6. Решения

### `decisions`

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| finding_id | uuid FK→findings ON DELETE CASCADE | |
| project_id | uuid FK→projects NULL | решение почти всегда привязано к проекту |
| status | text NOT NULL | `CRITICAL` \| `RECOMMENDED` \| `REVIEW_LATER` \| `ARCHIVED` \| `REJECTED` |
| **reason** | text NOT NULL | **CHECK (length(trim(reason)) > 0)** |
| reason_code | text | `duplicates_existing` \| `no_real_advantage` \| `adds_infrastructure` \| `project_inactive` \| `stale_commits` \| `license_risk` \| `not_needed` |
| decided_by | text NOT NULL | `engine` \| `user` |
| radar_score_at_decision | float | |
| score_snapshot | jsonb | все метрики на момент решения |
| superseded_by | uuid FK→decisions NULL | цепочка переоценок |
| is_current | bool NOT NULL default true | |
| created_at | timestamptz | |

Индексы: `(finding_id, project_id) where is_current`, `(status, created_at DESC)`.

### `reassessment_triggers` — REJECTED не навсегда

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| finding_id | uuid FK→findings ON DELETE CASCADE | |
| project_id | uuid FK→projects NULL | |
| trigger_type | text NOT NULL | `major_release` \| `stars_surge` \| `architecture_change` \| `new_feature` \| `license_change` \| `activity_resumed` \| `scheduled` |
| condition | jsonb NOT NULL | `{"metric":"stars","op":">=","value":2200}` / `{"semver":"major"}` |
| baseline | jsonb NOT NULL | что было на момент отклонения |
| status | text NOT NULL default `armed` | `armed` \| `fired` \| `disarmed` |
| fired_at | timestamptz | |
| fired_evidence | jsonb | что именно сработало |
| created_at | timestamptz | |

Индекс `(status, trigger_type)`.
Ежедневная задача `check_reassessments` сравнивает свежий `repository_snapshots` с `baseline` → при срабатывании создаёт новое `finding_project_matches` + возвращает находку в `status='new'`.

### `review_queue` — REVIEW LATER

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| finding_id | uuid FK→findings ON DELETE CASCADE | |
| project_id | uuid FK→projects | |
| review_at | date NOT NULL | вернуться 7 дней / месяц / позже |
| priority | text NOT NULL | `high` \| `medium` \| `low` |
| notes | text | |
| resolved_at | timestamptz | |
| created_at | timestamptz | |

`UNIQUE(finding_id, project_id) where resolved_at is null`; индекс `(review_at) where resolved_at is null`.

---

## 7. Отчёты, стоимость, эксплуатация

### `daily_reports`

| поле | тип | |
|---|---|---|
| id | uuid PK | |
| report_date | date UNIQUE NOT NULL | |
| window_start / window_end | timestamptz NOT NULL | окно 24ч, Asia/Aqtau |
| telegram_messages_processed | int | |
| github_candidates | int | |
| after_cheap_filter | int | |
| after_ai_filter | int | |
| deep_analyses_count | int | |
| critical_count / recommended_count / review_later_count | int | |
| rejected_count / duplicates_count | int | |
| estimated_cost_usd | numeric(12,6) | |
| body_markdown | text NOT NULL | готовый отчёт |
| payload | jsonb | структурировано для UI |
| is_empty | bool NOT NULL default false | «Сегодня значимых находок нет» |
| created_at | timestamptz | |

### `ai_usage_events` — контроль стоимости

| поле | тип | |
|---|---|---|
| id | bigserial PK | |
| occurred_at | timestamptz NOT NULL default now() | |
| provider | text NOT NULL | `groq` \| `openai` \| `github` \| `telegram` \| `embeddings` |
| operation | text NOT NULL | `classification` \| `deep_analysis` \| `embed` \| `api_call` |
| model | text | |
| requests | int NOT NULL default 1 | |
| prompt_tokens / completion_tokens | int | |
| cost_usd | numeric(12,6) NOT NULL default 0 | |
| finding_id | uuid FK→findings NULL | |
| meta | jsonb | |

Индексы: `(occurred_at DESC)`, `(provider, occurred_at DESC)`.
Дневная/месячная сводка — обычный `GROUP BY date_trunc(...)`, материализованные вьюхи не нужны на этих объёмах.

### `collector_runs` — лог прогонов

| поле | тип | |
|---|---|---|
| id | bigserial PK | |
| source_id | uuid FK→sources NULL | |
| collector | text NOT NULL | `telegram` \| `github_search` \| `github_refresh` |
| started_at / finished_at | timestamptz | |
| status | text | `running` \| `ok` \| `partial` \| `failed` |
| items_fetched / items_new / items_skipped | int | |
| api_requests | int | |
| rate_limit_remaining | int | |
| error | text | |

### `settings` — рантайм-конфиг (не секреты)

| поле | тип | |
|---|---|---|
| key | text PK | `score.weights`, `dedup.cosine_threshold`, `radar.min_relevance` |
| value | jsonb NOT NULL | |
| updated_at | timestamptz | |

Секреты здесь **не хранятся никогда** — только env.

---

## 8. Векторы: параметры

| параметр | значение | почему |
|---|---|---|
| Тип колонки | `vector(EMB_DIM)` | |
| EMB_DIM | 1024 (BGE-M3 / E5-large) или 1536 (OpenAI 3-small) | обе ≤ 2000 — индексируются напрямую |
| Индекс | HNSW, `m=16`, `ef_construction=64` | объёмы < 100k строк, HNSW даёт лучший recall |
| Opclass | `vector_cosine_ops` | эмбеддинги нормализованы |
| 3072-мерные модели | **не берём** | > 2000 → потребуется `halfvec(3072)` + `halfvec_cosine_ops`, лишняя сложность |

Колонки с векторами: `projects.embedding`, `project_features.embedding`, `findings.embedding`.

Alembic: расширение ставится **первой** миграцией
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```
и в `script.py.mako` добавлен `import pgvector.sqlalchemy`, иначе autogenerate роняет Vector-колонки.

---

## 9. Инварианты, которые схема защищает

1. `UNIQUE(raw_items.source_id, external_id)` — одно и то же сообщение/репа не анализируется дважды.
2. `UNIQUE(finding_keys.key_type, key_value)` — дубль ловится на вставке, а не постфактум.
3. `CHECK(decisions.reason <> '')` — решение без причины невозможно.
4. `UNIQUE(repository_snapshots.repository_id, captured_on)` — один снапшот в сутки, история не раздувается.
5. `UNIQUE(finding_analyses …, input_digest)` — один и тот же вход не оплачивается дважды.
6. `UNIQUE(findings.repository_id)` — репозиторий не может породить две находки.
