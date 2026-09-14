# Разбор выгрузки Project Radar от 14.09.2026

48 находок, статусы RECOMMENDED (6) и REVIEW_LATER (42). Проверено по коду
репозиториев, а не по описаниям в выгрузке.

## Главный вывод

**Радар оценивал EngHub по чужому профилю.** В каждой из 48 карточек раздел
«Что у нас уже есть» описывает EngHub как *«Python, FastAPI, PostgreSQL,
построчный разбор документов, полуавтоматическое извлечение таблиц»*.

Фактически EngHub — это:

| Что | Факт | Файл |
|---|---|---|
| API | Express 4.18 + TypeScript 5.3, Node 22 | `services/api-server/package.json` |
| OCR | Tesseract.js 7.0 локально → OpenAI Vision как фолбэк, kill-switch `system_flags.ocr_kill` | `services/api-server/src/services/textExtract.ts:79-153` |
| Поиск по нормативам | гибридный: pgvector + полнотекстовый, RPC `search_normative` | `routes/agsk.ts`, `routes/normative-search.ts`, `supabase/functions/search-normative` |
| Эмбеддинги | OpenAI `text-embedding-3-small`, 1536, корпус уже сэмбеднут (менять нельзя) | `services/agsk-ingestion/src/processors/embedder.ts` |
| LLM | OpenAI + GLM/Z.ai + Groq, **единого роутера нет** | `services/llm.ts`, `utils/aiProvider.ts`, `cadLlmService.ts`, `docReviewLlm.ts` |
| Python | только `cad-engine` (CadQuery, ezdxf, ifcopenshell) и `process-engine` | `services/cad-engine/` |

Тот самый «построчный разбор документов на FastAPI», по которому радар мерил
project_fit, — это `services/document-parser`. **Он мёртвый:**

- ни одной ссылки из `api-server` и фронтенда: нет URL, нет env-переменной,
  нет Railway-конфига (проверено grep'ом по всему репо, кроме `node_modules`);
- `STATE.md:4270` сам числит его среди «4 недеплоящихся кодовых юнита»
  вместе с `agsk-ingestion`, `agsk-retrieval`, `mcp-server`.

## Первопричина — в конфиге радара, не в находках

`backend/scripts/seed_projects.py:32-39`:

```python
{
    "slug": "enghub",
    "description": "Инженерная документация и работа с нормативами",
    "github_repository": None,  # ← "andyrbek2709-tech/enghub"
    "priority_areas": ["RAG", "Document AI", "парсинг PDF", "поиск по нормативам"],
}
```

Три дефекта:

1. **`github_repository: None`** → техревизия репозитория никогда не
   запускалась. `profiler.py:169` при пустом поле пишет
   `"github_repository не указан у проекта"` и выходит. Поле `current_stack`
   остаётся пустым, и в промпт `build_project_match_prompt`
   (`app/analysis/prompts.py:158-226`) стек уходит незаполненным — модель
   достраивает его сама. Отсюда и взялся FastAPI.
2. **В комментарии указан не тот репозиторий** — `andyrbek2709-tech/enghub`.
   По `CLAUDE.md` это standalone-репо, помеченное «НЕ использовать». Рабочий —
   `andyrbek2709-tech/ai-institut`.
3. **У остальных проектов профиль пустой.** `vformate` и `trackparts` заведены
   с `description: ""`, `priority_areas: []`, `search_keywords: []`.
   `lohotron`, `zharnama`, `gost-magazin-rosta` не заведены вовсе. Поэтому все
   48 находок ушли в EngHub — конкурентов у него в скоринге просто нет.

Правка профиля меняет качество всей последующей выдачи. Разбирать сами
находки до неё — работа по неверной вводной.

## Качество выборки

- 12 из 48 — без лицензии или `NOASSERTION`;
- 24 — меньше 300 звёзд, 20 — меньше 15 форков;
- ~10 из них — свежие одиночные репозитории с 0-2 форками
  (`uajy-academic-rag-chatbot`, `pan-campus-agent`, `MultiModeGraphRag`,
  `deepseek-v4-flash-vision-rag`, `Review-RAG`, `StudyPilot`, `win-ocr`).

По правилу 15 `CLAUDE.md` такие репозитории нельзя клонировать и ставить до
проверки источника. Радару стоит отсекать их фильтром, а не отдавать в
выгрузку наравне с `docling` (66k звёзд) и `pydantic`.

Отдельно мимо кассы — находки из чужих экосистем: OCR на C# (#9), на чистом C
(#11), под Swift/macOS (#31), обёртка над Windows Snipping Tool (#41),
CUDA-ядра внимания (#28), awesome-list (#46).

## Что из 48 действительно применимо

**Стоит смотреть:**

| # | Находка | К чему | Комментарий |
|---|---|---|---|
| 4 | `docling` (MIT, 66k★) | EngHub | единственная зрелая замена связке Tesseract.js + Vision; но Python — потребует сервиса, а не библиотеки |
| 12 / 26 | `litellm` / `any-llm` | EngHub | закрывают реальную дыру — разбросанные вызовы трёх провайдеров |
| 35 | `opik` (Apache-2.0, 22k★) | EngHub, zharnama | трассировка и стоимость LLM-вызовов |
| 40 | `ifc-lite` (MPL-2.0) | EngHub `cad-engine` | клиентский парсер IFC, сейчас всё через `ifcopenshell` на сервере |
| 27 | `maskgate` — **как идея, не как код** | EngHub, track-parts | маскировка ПДн до отправки в LLM |

**Не нужно:** `pydantic` (#3) — уже стоит в `cad-engine` и `process-engine`;
`transformers` (#8), `nucliadb` (#7), `embetter` (#23) — свой pgvector-стек
работает и корпус залочен на `text-embedding-3-small`; `VideoTranscriptAPI`
(#37) — Whisper уже подключён; `nestjs/elasticsearch` (#36) — NestJS в EngHub
нет вообще (он в Sas_vformate, где поиск не нужен).

`GraphRAG-SDK` (#1, топ выдачи) решает задачу, которой у EngHub нет в
поставленном виде: provenance уже собирается (`document-parser/src/models/
traceability.py`, `source_document_id` + `source_quote` в миграции
`2026-08-03`), просто не отдаётся в ответе. Это правка на день, а не переезд
на графовую БД.

## Что покрывает радар у остальных проектов

| Проект | Стек | Темы, по которым радар молчит |
|---|---|---|
| Sas_vformate | NestJS + Prisma + PG, Next.js 15 | Groq-бот на паузе; выгрузки Excel/PDF из плана |
| track-parts | Next.js 16, PG, Groq Vision + Whisper | OCR путевых листов — прямая целевая тема радара |
| lohotron | FastAPI + SQLite + Celery, Groq | торговые сигналы, бэктест |
| gost-magazin-rosta | FastAPI + Telegram, Groq Vision/STT | CRM, распознавание фото товара |
| zharnama | Next.js 15 + Cloudflare D1, OpenAI ×7 + Higgsfield | генерация креативов, наблюдаемость промптов |

track-parts по теме «OCR и извлечение таблиц» ближе к находкам радара, чем
EngHub, — но радар его не видит.

## Решения — в `DECISIONS_RADAR_FINDINGS.html`
