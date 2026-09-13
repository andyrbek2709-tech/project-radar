# Project Radar — архитектура

Персональный технический радар: найти → проверить → сопоставить с конкретным проектом → оценить реальную пользу → убрать дубли и шум → сохранить перспективное.

Это **не** новостной агрегатор. Критерий качества — минимум шума при высокой полезности. Если за сутки ничего нет, отчёт честно пишет «Сегодня значимых находок нет».

---

## 1. Сервисы

| сервис | процесс | назначение |
|---|---|---|
| `api` | `uvicorn app.main:app` | FastAPI: REST для фронта, ручные триггеры, статистика |
| `worker` | `celery -A app.tasks.celery_app worker` | весь pipeline |
| `scheduler` | `celery -A app.tasks.celery_app beat` | расписание (отдельный процесс, не `worker -B`) |
| `telegram` | `python -m app.collectors.telegram.runner` | **отдельный долгоживущий asyncio-процесс** |
| `web` | Next.js standalone | UI |
| `postgres` | pgvector/pgvector:pg17 | БД + векторы |
| `redis` | redis:7 | брокер Celery + кэш GitHub |

**Почему Telethon отдельным процессом, а не в Celery-таске.** Telethon — асинхронный клиент с долгоживущей MTProto-сессией. Поднимать его внутри синхронной Celery-таски означает логиниться на каждый прогон; Telegram считает это подозрительной активностью и раздаёт FloodWait по нарастающей. Отдельный процесс держит одно соединение, читает по таймеру и складывает сырьё в `raw_items`, после чего ставит задачу в Celery. Celery в этой схеме ничего про Telegram не знает.

**Почему beat отдельным сервисом.** `worker -B` нельзя масштабировать: два инстанса = двойное расписание. Плюс beat пишет `celerybeat-schedule` на диск, а на Railway файловая система эфемерная — используем `redbeat` (расписание в Redis), либо, если не хочется лишней зависимости, `PersistentScheduler` с путём на Railway Volume. Решение вынесено в `DECISIONS_RADAR.html`.

---

## 2. Pipeline

```
RAW COLLECTOR
  ├─ telegram runner  → raw_items (kind=telegram_message)
  └─ github radar     → repositories + repository_snapshots + raw_items (kind=github_repo)
        ↓
NORMALIZATION      raw → Finding-кандидат: title, url, normalized_url, content_text
        ↓
DEDUPLICATION      finding_keys (github_id → full_name → normalized_url → url → title_hash)
                   → pgvector семантика (cosine < 0.08)
                   → дубль: +seen_count, +found_via, +confidence; выход из pipeline
        ↓
CHEAP FILTER       без LLM: negative_keywords, длина, язык, чёрный список доменов,
                   archived/disabled репы, pushed_at старше N месяцев, stars < порога
                   → filtered_out (в отчёт не попадает)
        ↓
GROQ ANALYSIS      structured JSON (strict schema): relevance, projects, categories,
                   novelty, potential_value, recommend_deep_analysis, summary
                   → relevance < MIN_RELEVANCE_SCORE → REJECTED(reason=low_relevance)
        ↓
PROJECT MATCHING   строки finding_project_matches для каждого названного проекта
        ↓
SIMILARITY         embedding находки ↔ projects.embedding, project_features.embedding,
                   прошлые findings, прошлые decisions (принятые и отклонённые)
                   → max_similarity_to_features, nearest_feature_id
        ↓
DEEP ANALYSIS      только кандидаты: radar_score ≥ MIN_DEEP_ANALYSIS_SCORE
                   ∧ recommend_deep_analysis ∧ лимит MAX_DEEP_ANALYSES_PER_DAY
                   режимы: AUTO (OpenAI) | MANUAL (кнопка COPY ANALYSIS CONTEXT)
        ↓
DECISION ENGINE    CRITICAL / RECOMMENDED / REVIEW_LATER / ARCHIVED / REJECTED
                   + обязательный reason + reassessment_triggers
        ↓
DATABASE
        ↓
DAILY RADAR        21:00 Asia/Aqtau, окно — предыдущие 24 часа
```

Каждая стадия — отдельная Celery-таска, состояние в `findings.pipeline_stage`. Упавшая стадия не теряет работу предыдущих.

---

## 3. Источники: плагинный интерфейс

```python
class Source(Protocol):
    kind: str
    async def collect(self, ctx: CollectContext) -> AsyncIterator[RawItem]: ...
    async def health(self) -> SourceHealth: ...
```

Реализации v1: `TelegramSource`, `GitHubSearchSource`, `GitHubWatchSource`.
Зарезервировано: `RssSource`, `HackerNewsSource`, `RedditSource`, `HabrSource`, `DocsSource`.
Регистрация — в `app/collectors/registry.py`, по `kind` из таблицы `sources`. Добавление источника не трогает pipeline ниже нормализации.

### Telegram (Telethon 1.45, user session)

- `StringSession` из env `TELEGRAM_SESSION_STRING`; генерируется локально скриптом `scripts/gen_telegram_session.py` и **никогда не логируется**.
- Чтение: `client.iter_messages(entity, min_id=last_item_id, reverse=True)` — курсор в `source_cursors.last_item_id`.
- `flood_sleep_threshold = 60`; `FloodWaitError` с `e.seconds > 300` → пауза источника и запись в `collector_runs`, без агрессивных ретраев.
- Извлекаем без скачивания медиа: текст, дату, автора, `reply_to_msg_id`, URL из `MessageEntityUrl` / `MessageEntityTextUrl`, признак форварда, тип медиа.
- Источники в `TELEGRAM_SOURCE_IDS` — численные `-100…` id (устойчивее `@username`).

### GitHub Radar

Собственный поиск, не только то, что всплыло в Telegram:

| стратегия | запрос |
|---|---|
| новые репы по теме | `created:>{d-7} stars:>50 topic:rag` |
| быстрый рост | своими снапшотами: `stars_delta / stars_old` за 14 дней |
| новые релизы | отслеживаемые репы → `/releases/latest` |
| по профилю проекта | `search_keywords` проекта → `q` |
| высокая активность | `pushed:>{d-2} stars:>200 language:python` |
| интересные форки | `is_fork` + расхождение с parent |

Жёсткие факты, под которые спроектирован коллектор:
- **Search: 30 запросов/минуту**, core PAT — 5000/час.
- **Search отдаёт максимум 1000 результатов** на запрос. Поэтому широкие запросы режутся по датам и звёздам на узкие окна, а не пагинируются за 1000.
- **304 Not Modified не тратит лимит** — ETag хранится в `repositories.etag` / `source_cursors.etag`, используется везде.
- **`/stargazers` с 30.06.2026 закрыт** для не-коллабераторов → `starred_at` недоступен. Рост звёзд считаем **только своими снапшотами**, отсюда обязательность `repository_snapshots`.
- Официального trending API нет и не появилось.
- Массовый добор метаданных — **GraphQL** (50 реп одним запросом ≈ 1 point вместо 50 REST-вызовов).
- `/stats/commit_activity` может отдать `202` (считается) — ретрай через 2–3 секунды, не считать ошибкой.
- `contributors_count` — `?per_page=1&anon=1` и разбор `Link: rel="last"`.

---

## 4. AI-слои

### Groq — дешёвый массовый слой

Строгий JSON через `response_format={"type":"json_schema", ..., "strict": True}`.
**Strict-режим поддерживают только `openai/gpt-oss-20b` и `openai/gpt-oss-120b`** — у остальных моделей `strict` молча игнорируется. Поэтому дефолт `GROQ_MODEL=openai/gpt-oss-20b` ($0.075/$0.30 за 1M), эскалация на `120b` ($0.15/$0.60) для сложных случаев.

Ответ валидируется pydantic-моделью; при `invalid_json` — один ретрай с `{"type":"json_object"}`, затем запись `status=schema_error` и находка уходит в REVIEW_LATER, а не молча теряется.

```json
{"relevance":0.91,"projects":["EngHub"],"categories":["RAG","Document AI"],
 "novelty":7,"potential_value":8,"recommend_deep_analysis":true,
 "summary":"…","noise":false}
```

Groq **не принимает финального решения о внедрении**. Его задача — отсеять шум и назвать кандидатов.

Параметр называется `max_completion_tokens` (не `max_tokens`). Rate limits читаются из заголовков `x-ratelimit-remaining-*`, при 429 — `retry-after`.
Batch API даёт −50% к цене при окне 24ч — подключаем для ночного добора, когда латентность не важна.

### Embeddings

**У Groq embeddings-эндпоинта нет** (проверено: на console.groq.com страницы Embeddings не существует; упоминания на сторонних сайтах не подтверждаются первоисточником). Варианты:

| провайдер | модель | dim | цена |
|---|---|---|---|
| локально | `BAAI/bge-m3` | 1024 | $0, ~2.2 GB RAM |
| локально | `intfloat/multilingual-e5-large` | 1024 | $0, ~2.2 GB RAM |
| OpenAI | `text-embedding-3-small` | 1536 | $0.02 / 1M |

Абстракция `EmbeddingProvider` (`app/analysis/embeddings/`) переключается через `EMBEDDING_PROVIDER`. Выбор дефолта — вопрос в доске решений: локальная модель бесплатна, но добавляет ~2 GB RAM на Railway (это реальные деньги при $10/GB), OpenAI стоит копейки и не ест память.

### Deep Analysis

`OPENAI_DEEP_ANALYSIS_ENABLED=false` по умолчанию. Два режима, оба поддержаны архитектурно:

- **AUTO** — `OpenAIDeepAnalyzer` вызывает модель, результат в `finding_analyses(analysis_type='deep_analysis')`.
- **MANUAL** — кнопка `COPY ANALYSIS CONTEXT` собирает готовый промпт; ответ из ChatGPT вставляется обратно через `POST /findings/{id}/manual-analysis` и ложится в ту же таблицу с `provider='manual'`.

Оба режима пишут одну и ту же структуру, поэтому UI и decision engine не различают их источник.

---

## 5. Decision Engine

Детерминированные правила поверх скоров — не LLM. LLM даёт материал, решение принимает код, и его можно объяснить строкой.

```
if risk_score > 0.8 or license_risk:            REJECTED   (license_risk / high_risk)
elif max_similarity_to_features > 0.85:         REJECTED   (duplicates_existing)
elif improvement_score < 0.25:                  REJECTED   (no_real_advantage)
elif activity_score < 0.15:                     REJECTED   (project_inactive)
elif radar_score >= 0.80 and confidence >= 0.7: CRITICAL
elif radar_score >= 0.60:                       RECOMMENDED
elif radar_score >= 0.40:                       REVIEW_LATER (+7d / +30d по priority)
else:                                           ARCHIVED
```

`reason` пишется всегда и человеческим языком: «дублирует существующий механизм парсинга (pdfplumber, similarity 0.91)», «не даёт заметного преимущества относительно текущего подхода», «добавляет инфраструктуру: требуется отдельный сервис», «последний commit 14 месяцев назад».

При REJECTED автоматически ставятся `reassessment_triggers`:
`major_release`, `stars_surge` (×2 от baseline), `architecture_change`, `new_feature`, `license_change`, `activity_resumed`. Ежедневная таска сверяет свежие снапшоты с baseline.

### Формулировки

Запрещено: «это интересный проект».
Обязательный шаблон: **что делает → что у нас сейчас → что предлагает нового → преимущество относительно текущего подхода → цена внедрения → рекомендация.** Оценка всегда относительно конкретного проекта, поэтому и хранится в `finding_project_matches`, а не в `findings`.

---

## 6. Расписание

| задача | когда | примечание |
|---|---|---|
| `collect_telegram` | каждые `TELEGRAM_SCAN_INTERVAL_MINUTES` (деф. 15) | в telegram-процессе, не в Celery |
| `collect_github` | каждые `GITHUB_SCAN_INTERVAL_MINUTES` (деф. 120) | 30 req/min на search — окна разнесены |
| `snapshot_repositories` | 03:00 | один снапшот в сутки |
| `process_pending` | каждые 10 мин | нормализация → dedup → фильтр → Groq |
| `run_deep_analyses` | каждый час | с учётом `MAX_DEEP_ANALYSES_PER_DAY` |
| `check_reassessments` | 04:00 | после снапшотов |
| `promote_review_queue` | 08:00 | `review_at <= today` → обратно в радар |
| `build_daily_radar` | `RADAR_DAILY_TIME` (деф. 21:00) | окно — предыдущие 24ч, Asia/Aqtau |

Никакого бесконечного polling там, где хватает расписания.

---

## 7. Web UI

Next.js (App Router) + Tailwind, тёмная тема, без компонентных фреймворков «из коробки».
Разделы: **Dashboard**, **Projects**, **Radar** (Critical / Recommended / Review Later / Archive / Rejected), **Sources** (Telegram / GitHub), **Settings**.

Карточка находки: название · URL · repository · источник (`found_via` бейджами) · дата обнаружения · для какого проекта · краткое описание · почему релевантно · что уже есть у нас · что предлагает нового · advantages · disadvantages · integration complexity · risk · score с раскрывающимся `score_breakdown` · AI recommendation · status.

**COPY ANALYSIS CONTEXT** — одна кнопка, один клик, готовый промпт для ChatGPT/Codex со всем контекстом: репозиторий, почему Radar его выбрал, текущий стек проекта, что уже реализовано, прямой вопрос «даёт ли это реальное улучшение и стоит ли внедрять».

---

## 8. Контроль стоимости

Каждый внешний вызов пишет строку в `ai_usage_events`. Дашборд показывает: Groq requests/tokens, OpenAI requests/tokens, GitHub API requests, обработанные Telegram-сообщения, число deep analyses, daily и monthly estimated cost.

`MAX_DEEP_ANALYSES_PER_DAY` конфигурируем, но **жёстко не ограничиваем изначально** — функциональность ради экономии не режем. Экономия достигается архитектурой, а не отключением фич:

- дедупликация до LLM — не платим за повторы;
- cheap filter без LLM срезает основную массу;
- `finding_analyses.input_digest` — один и тот же вход не оплачивается дважды;
- ETag/304 на GitHub — бесплатные проверки;
- GraphQL-батчи вместо N REST-вызовов;
- Groq Batch API (−50%) для не срочного;
- `max_completion_tokens` ограничен, README усекается до N символов перед отправкой.

---

## 9. Деплой на Railway

Шесть сервисов из одного репозитория, у каждого свой Root Directory / Dockerfile:
`api`, `worker`, `scheduler`, `telegram`, `web`, плюс `Postgres(pgvector)` и `Redis`.

- **Railway Postgres не содержит pgvector** — поднимаем отдельным сервисом из образа `pgvector/pgvector:pg17` (или из готового pgvector-шаблона Railway).
- Переменные между сервисами — референсами: `${{Postgres.DATABASE_URL}}`, `${{Redis.REDIS_URL}}`, приватная сеть `*.railway.internal`.
- Воркерам HTTP-порт не нужен: healthcheck и домен не настраиваем, Railway такие сервисы не убивает.
- **`railway.json` / `railway.toml` объявлены deprecated с отключением 01.12.2026**, замена — `.railway/railway.ts` (Infrastructure as Code). В репозитории кладём **оба**: `railway.json` работает сегодня, `.railway/railway.ts` — целевой формат. Это пункт в доске решений.
- Frontend — `output: 'standalone'`, Dockerfile, опционально serverless-режим (засыпание после 10 мин) для экономии.

Подробности и точные шаги — в `README.md` и `docs/DEPLOY.md`.

---

## 10. Логирование

`structlog`, JSON в stdout, поля: `service`, `collector`, `source_id`, `finding_id`, `stage`, `duration_ms`, `trace_id`.
Логгеры: `collector`, `telegram`, `github`, `groq`, `openai`, `scheduler`, `errors`, `decisions`.

**Секреты в логи не попадают никогда.** В `app/core/logging.py` стоит процессор-редактор, который вырезает по маске ключи `*_KEY`, `*_TOKEN`, `*_HASH`, `*SESSION*`, `*PASSWORD*`, `DATABASE_URL`, `REDIS_URL` из любого структурированного поля, включая вложенные словари и тексты исключений.

---

## 11. Что осознанно НЕ делаем в v1

- Не тянем Habr/Reddit/HN/RSS — интерфейс `Source` готов, реализации отложены.
- Не строим свой rerank поверх эмбеддингов — хватает косинуса + правил.
- Не делаем multi-user: один пользователь, basic-auth (`ADMIN_USERNAME`/`ADMIN_PASSWORD`).
- Не храним медиа из Telegram — только метаданные.
- Не используем `halfvec` — держим размерность ≤ 2000 и индексируем напрямую.
