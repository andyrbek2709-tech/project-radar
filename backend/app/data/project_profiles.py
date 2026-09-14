"""Эталонные профили проектов пользователя.

Профиль — это то, относительно чего радар оценивает КАЖДУЮ находку:
`build_classification_prompt` решает, какому проекту находка адресована, а
`build_project_match_prompt` считает project_fit. Пустое поле не означает
«оценивай нейтрально»: модель достраивает недостающее сама. Так EngHub,
написанный на Express и TypeScript, попал в выгрузку 14.09.2026 как
«Python, FastAPI, PostgreSQL» — у проекта стоял github_repository=None,
техревизия не запускалась, и current_stack уходил в промпт пустым.

Модуль лежит в пакете приложения, а не в scripts/, потому что его читают
двое: scripts/seed_projects.py и миграция, применяющая профили на деплое.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # только для аннотации: миграция импортирует этот модуль,
    from app.models.project import Project  # и тянуть в неё ORM-модели незачем

# negative_keywords объединяются ПО ВСЕМ активным проектам и применяются
# в cheap_filter как один глобальный список подстрок (pipeline._negative_keywords
# → normalizer.cheap_filter). Поэтому здесь только то, что не нужно НИКОМУ.
# Исключения конкретного проекта пишутся в things_not_needed — они попадают
# в промпт отдельно по каждому проекту и чужие находки не режут.
# Отсюда убраны "crypto" (подстрока ловит легитимную библиотеку cryptography),
# "trading" (убивал бы весь Trading Lab) и "game" (слишком широкое).
PORTFOLIO_NEGATIVE = ["nft", "memecoin", "meme coin", "casino", "gambling", "shitcoin"]

# Поля, которые скрипт вправе обновлять у существующего проекта.
UPDATABLE = (
    "name",
    "description",
    "business_purpose",
    "existing_architecture",
    "current_stack",
    "integrations",
    "current_problems",
    "planned_features",
    "technology_interests",
    "search_keywords",
    "negative_keywords",
    "things_not_needed",
    "priority_areas",
    "github_repository",
)

PROJECTS: list[dict[str, Any]] = [
    {
        "slug": "enghub",
        "name": "EngHub",
        "github_repository": "andyrbek2709-tech/ai-institut",
        "description": (
            "Внутренняя инженерная платформа проектного института: проекты, задачи, "
            "рабочая документация, поиск по нормативам, совещания с протоколами и расчёты "
            "для десяти инженерных отделов."
        ),
        "business_purpose": (
            "Перевести выпуск рабочей документации института в одну систему: задачи по отделам, "
            "версии документов, проверка по нормативам, протоколы совещаний, спецификации."
        ),
        "existing_architecture": (
            "Монорепозиторий. API — Express 4 на TypeScript и Node 22 (services/api-server), "
            "фронтенд — React + Vite (enghub-main). Данные — Supabase PostgreSQL с pgvector, RLS "
            "и Storage. Рядом Python-микросервисы: cad-engine (CadQuery, ezdxf, ifcopenshell) и "
            "process-engine. Деплой — Railway, единственная прод-платформа. "
            "OCR: Tesseract.js локально на 200 dpi, при неудаче OpenAI Vision с ограничением "
            "15 страниц и kill-switch. Поиск по нормативам — гибридный: pgvector плюс "
            "полнотекстовый, RPC search_normative и реранкер на Node. Эмбеддинги — OpenAI "
            "text-embedding-3-small, корпус уже проиндексирован. LLM: OpenAI, GLM/Z.ai и Groq."
        ),
        "current_stack": {
            "backend": ["Express 4", "TypeScript", "Node 22"],
            "frontend": ["React", "Vite", "TypeScript"],
            "database": ["Supabase PostgreSQL", "pgvector", "RLS"],
            "ai": ["OpenAI", "GLM/Z.ai", "Groq", "Whisper", "text-embedding-3-small"],
            "parsing": ["Tesseract.js", "OpenAI Vision OCR", "ExcelJS", "docx"],
            "cad": ["CadQuery", "ezdxf", "IfcOpenShell", "dxf-parser", "libredwg-web"],
            "infra": ["Railway", "Redis Streams", "Supabase Edge Functions"],
        },
        "integrations": ["Supabase", "Railway", "Telegram", "SMTP (info@nipicer.kz)", "Wazzup/Bitrix24 — нет"],
        "current_problems": [
            "вызовы трёх LLM-провайдеров разбросаны по коду, единого роутера и учёта расходов нет",
            "трассировка извлечения собирается, но не отдаётся пользователю: ответ по нормативам "
            "приходит без ссылки на документ и пункт",
            "нет staging-окружения и регулярных бэкапов базы",
            "GPL-3.0 библиотеки DWG/DXF стоят в основных пакетах вместо изолированного воркера",
            "часть кодовых юнитов не задеплоена и вводит в заблуждение: document-parser, "
            "agsk-ingestion, agsk-retrieval, mcp-server",
        ],
        "planned_features": [
            "AI-разбор протоколов совещаний в задачи",
            "автоуведомления о просрочках по email и Telegram",
            "автогенерация спецификаций",
            "S-кривая план/факт для ГИПа",
            "PDF-отчёт готовности для заказчика",
            "кабинет бухгалтерии и дашборд канцелярии",
            "справочник должностей",
            "staging, бэкапы и дисциплина миграций",
            "метрики реального использования",
        ],
        "priority_areas": [
            "RAG по нормативным документам",
            "OCR и извлечение таблиц из сканов",
            "трассировка источников в ответах модели",
            "наблюдаемость и стоимость LLM",
            "CAD: DWG, DXF, IFC",
            "надёжность продакшена",
        ],
        "technology_interests": [
            "гибридный поиск", "pgvector", "structured output",
            "OpenAI-совместимые провайдеры", "IFC", "provenance",
        ],
        "search_keywords": [
            "rag", "hybrid search", "pgvector", "document ai", "table extraction", "ocr",
            "pdf parsing", "llm gateway", "llm observability", "citation", "provenance",
            "ifc", "dxf", "dwg",
        ],
        "things_not_needed": [
            "Vercel и любые платформы деплоя, кроме Railway",
            "замена модели эмбеддингов: корпус нормативов уже проиндексирован "
            "на text-embedding-3-small",
            "библиотеки под C#, C, Swift и только под Windows — стек TypeScript и Python",
            "обучение и дообучение моделей, CUDA-ядра: инференс только по API",
            "NestJS и Elasticsearch — их в проекте нет",
            "подборки ссылок и awesome-списки вместо кода",
        ],
        "negative_keywords": PORTFOLIO_NEGATIVE,
    },
    {
        "slug": "vformate",
        "name": "VFormate — зарплата и расходы",
        "github_repository": "andyrbek2709-tech/Sas_vformate",
        "description": (
            "Внутреннее приложение рекламного агентства: расчёт зарплаты по правилам Казахстана "
            "и регулярные расходы агентства."
        ),
        "business_purpose": (
            "Считать зарплату по фактическому производственному календарю — официальную часть "
            "и наличную доплату параллельно во всех сценариях — и вести регулярные платежи."
        ),
        "existing_architecture": (
            "Монорепозиторий pnpm + Turborepo. apps/api — NestJS 10, Prisma 5, PostgreSQL 16; "
            "apps/web — Next.js 15 App Router и Tailwind. Расчётное ядро — чистые функции без "
            "Prisma (modules/payroll/calc), покрыто 38 тестами Jest. Миграции только аддитивные, "
            "применяются на старте. Деплой Railway: сервисы api и web. Клиентский WhatsApp-бот "
            "через Открытые линии Bitrix24 и Groq выключен флагом в коде."
        ),
        "current_stack": {
            "backend": ["NestJS 10", "Prisma 5", "PostgreSQL 16", "TypeScript"],
            "frontend": ["Next.js 15", "React", "Tailwind CSS"],
            "ai": ["Groq (выключен)"],
            "infra": ["Railway", "Turborepo", "pnpm"],
            "tests": ["Jest"],
        },
        "integrations": ["Railway", "Bitrix24 Открытые линии", "Wazzup/WhatsApp", "Telegram"],
        "current_problems": [
            "нет выгрузок в Excel и PDF",
            "нет роли бухгалтера с отдельным входом без наличной части",
            "нет staging-окружения и бэкапов базы",
            "прогрессивный ИПН 15% на годовой доход не реализован",
            "ставки налогов и взносов стоят неподтверждёнными",
        ],
        "planned_features": [
            "Telegram-бот: напоминания о расходах и голосовые команды",
            "выгрузки Excel и PDF",
            "роль бухгалтера",
            "удержание за отпуск, отгулянный авансом",
        ],
        "priority_areas": [
            "расчёт по правилам ТК РК",
            "печатные формы и выгрузки",
            "Telegram-уведомления",
            "надёжность миграций без staging",
        ],
        "technology_interests": ["Prisma", "идемпотентные миграции", "экспорт xlsx и pdf"],
        "search_keywords": [
            "payroll", "excel export", "pdf report", "telegram bot", "nestjs", "prisma",
            "database migrations", "production calendar",
        ],
        "things_not_needed": [
            "OCR и парсинг PDF — документов на вход нет",
            "RAG, эмбеддинги и векторный поиск",
            "тяжёлые AI-фреймворки: единственный LLM-контур выключен владельцем",
            "CAD и инженерные расчёты",
        ],
        "negative_keywords": PORTFOLIO_NEGATIVE,
    },
    {
        "slug": "trackparts",
        "name": "TrackParts / Fleet Control",
        "github_repository": "andyrbek2709-tech/track-parts",
        "description": (
            "Система управления автопарком: техника, ремонты, дефектные ведомости, путевые листы, "
            "ГСМ, ввод через Telegram и телематика Wialon."
        ),
        "business_purpose": (
            "Собрать учёт автопарка в одну систему и снять ручной ввод: документы с телефона "
            "распознаются и попадают в базу структурированными."
        ),
        "existing_architecture": (
            "Next.js 16 App Router, TypeScript, Tailwind, shadcn/ui. PostgreSQL в продакшене "
            "с файловым fallback для локального и аварийного режима. Ввод через Telegram Bot API. "
            "Распознавание: PDF рендерится в PNG локально, включая JBIG2, дальше Groq Vision "
            "извлекает структурированный JSON — путевые листы до 5 страниц, ведомости до "
            "50 страниц и 500 строк. Голос — Groq whisper-large-v3-turbo. OpenAI — резерв "
            "для структурированного извлечения. Целевая интеграция — Wialon API."
        ),
        "current_stack": {
            "frontend": ["Next.js 16", "React", "TypeScript", "Tailwind CSS", "shadcn/ui"],
            "backend": ["Next.js API routes", "PostgreSQL"],
            "ai": ["Groq Vision", "Groq Whisper", "OpenAI (резерв)"],
            "parsing": ["PDF → PNG рендер", "JBIG2", "structured JSON extraction"],
            "integrations": ["Telegram Bot API", "Wialon API"],
        },
        "integrations": ["Telegram", "Wialon", "PostgreSQL"],
        "current_problems": [
            "распознавание ведомостей упирается в объём страниц и качество сканов",
            "два пути хранения: PostgreSQL в проде и файловый fallback",
        ],
        "planned_features": [
            "полная интеграция Wialon: пробег, маршруты, GPS",
            "единый поиск по технике, водителям и ремонтам",
        ],
        "priority_areas": [
            "OCR и извлечение таблиц из сканов",
            "структурированное извлечение из документов",
            "ввод через Telegram",
            "телематика и GPS",
        ],
        "technology_interests": [
            "vision-модели для документов", "structured output", "распознавание речи",
        ],
        "search_keywords": [
            "ocr", "table extraction", "pdf to json", "structured extraction", "vision llm",
            "document parsing", "telegram bot", "fleet management", "gps telematics", "whisper",
        ],
        "things_not_needed": [
            "перенос стека на Python — приложение на Next.js и TypeScript",
            "графовые базы знаний",
            "обучение и дообучение моделей",
            "CAD и инженерные нормативы",
        ],
        "negative_keywords": PORTFOLIO_NEGATIVE,
    },
    {
        "slug": "retail-gost",
        "name": "ГОСТ Магазин (Rosta)",
        "github_repository": "andyrbek2709-tech/gost-magazin-rosta",
        "description": (
            "Закрытый сервис управления двумя торговыми точками через публичный API Rosta: смены, "
            "чеки, продажи, остатки и документы, плюс Telegram-бот с голосом и разбором фотографий."
        ),
        "business_purpose": (
            "Управлять торговыми точками с телефона: спросить голосом, получить отчёт, "
            "подтвердить изменение — с обязательным подтверждением и журналом каждой правки."
        ),
        "existing_architecture": (
            "FastAPI на Python, интеграция с публичным API Rosta, python-telegram-bot. Любое "
            "изменение подтверждается в Telegram, постоянный журнал в rosta_action_audit.jsonl. "
            "Groq — свободные запросы, распознавание голоса whisper-large-v3-turbo и анализ "
            "фотографий; OpenAI Responses API — опционально для фото и PDF. Мобильная веб-панель, "
            "HTML-отчёт и OpenAPI для ChatGPT Actions. Ключи только в переменных Railway."
        ),
        "current_stack": {
            "backend": ["FastAPI", "Python"],
            "ai": ["Groq Vision", "Groq Whisper", "OpenAI Responses API (опционально)"],
            "integrations": ["Rosta Public API", "Telegram Bot API", "ChatGPT Actions"],
            "parsing": ["FPDF (выгрузка)"],
            "infra": ["Railway"],
        },
        "integrations": ["Rosta API", "Telegram", "Railway", "ChatGPT Actions"],
        "current_problems": [
            "PDF только выгружается через FPDF, парсинга входящих PDF нет",
            "журнал изменений лежит в JSONL на диске, а не в базе",
        ],
        "planned_features": [
            "расширение разрешённых методов Rosta",
            "многошаговые уточнения в диалоге",
        ],
        "priority_areas": [
            "голосовые команды и распознавание речи",
            "анализ фотографий товара и ценников",
            "надёжное подтверждение изменений и аудит",
            "отчётность по наличному обороту",
        ],
        "technology_interests": ["vision-модели", "speech-to-text", "OpenAPI для внешних агентов"],
        "search_keywords": [
            "telegram bot", "speech to text", "vision llm", "receipt recognition",
            "retail analytics", "openapi actions", "audit log", "fastapi",
        ],
        "things_not_needed": [
            "RAG и векторный поиск",
            "CAD и инженерные нормативы",
            "обучение моделей",
            "тяжёлые мультиагентные фреймворки",
        ],
        "negative_keywords": PORTFOLIO_NEGATIVE,
    },
    {
        "slug": "lohotron",
        "name": "Trading Lab",
        "github_repository": "andyrbek2709-tech/lohotron",
        "description": (
            "Paper-trading лаборатория: реальные публичные котировки Bybit, виртуальные сделки "
            "BTC/USDT и ETH/USDT, сравнение стратегий по фиксированным правилам и решений Groq."
        ),
        "business_purpose": (
            "Проверять торговые гипотезы на виртуальных кошельках без биржевых ключей "
            "и без единой реальной заявки."
        ),
        "existing_architecture": (
            "Python 3.12 и FastAPI, SQLite на постоянном томе, Redis и Celery — worker и beat. "
            "Четыре независимых направления-воркера с healthcheck: спот, фьючерсы 1x, фьючерсы 3x "
            "и пробой 5 минут. Groq (openai/gpt-oss-20b) — почасовой анализ свечей. Комиссии, "
            "проскальзывание и funding моделируются по опубликованным ставкам Bybit. Панель "
            "на русском, разделы /research. Деплой Railway."
        ),
        "current_stack": {
            "backend": ["FastAPI", "Python 3.12", "Celery", "Redis"],
            "database": ["SQLite на постоянном томе"],
            "ai": ["Groq"],
            "integrations": ["Bybit public API"],
            "infra": ["Railway"],
        },
        "integrations": ["Bybit", "Railway"],
        "current_problems": [
            "36 из 36 заранее заданных вариантов убыточны на проверочном 2025 году",
            "модель маржи приближённая: риск-тиры, ADL и страховой фонд Bybit не воспроизводятся",
            "REST-опрос примерно раз в 10 секунд, время реакции не гарантируется",
        ],
        "planned_features": [
            "расширение форвард-протокола",
            "точность учёта funding и ликвидаций",
        ],
        "priority_areas": [
            "бэктест и форвард-протокол",
            "моделирование комиссий, funding и ликвидаций",
            "надёжность воркеров и хранения временных рядов",
        ],
        "technology_interests": ["симуляторы биржи", "временные ряды", "Celery"],
        "search_keywords": [
            "backtesting", "paper trading", "exchange simulator", "funding rate",
            "timeseries", "celery worker", "sqlite", "market data",
        ],
        "things_not_needed": [
            "реальные биржевые ключи и живые заявки",
            "OCR, парсинг документов, CAD",
            "автоматическое включение новых правил без ручного решения",
            "высокочастотная торговля",
        ],
        "negative_keywords": PORTFOLIO_NEGATIVE,
    },
    {
        "slug": "zharnama",
        "name": "Zharnama / AI Promo Platform",
        "github_repository": "andyrbek2709-tech/zharnama",
        "description": (
            "Платформа оценки и выполнения прикладных проектов: витрина, Telegram-бриф, "
            "mini-CRM лидов, контент-workflow и Site Studio для сборки динамических сайтов."
        ),
        "business_purpose": (
            "Провести путь «спрос → идея → контент → публикация → лид → проект» в одной системе "
            "и показывать публично только подтверждённый опыт."
        ),
        "existing_architecture": (
            "Next.js 15 и Cloudflare D1 через Drizzle ORM. Telegram-интейк на русском и казахском, "
            "почта. Семь отдельных сервисов на OpenAI: image, creative-director, visual-critic, "
            "site-assistant, lead-intelligence, telegram-concierge и response — с маршрутизацией "
            "по режимам. Higgsfield Cloud для изображений и видео, HikerAPI для разбора Instagram, "
            "DataForSEO опционально. Режимы mock-first: платные вызовы только по явному "
            "подтверждению. Site Studio: registry секций, схемы Zod, renderer, composer, "
            "неизменяемая история версий и безопасный revert."
        ),
        "current_stack": {
            "backend": ["Next.js route handlers", "TypeScript"],
            "frontend": ["Next.js 15", "React", "TypeScript"],
            "database": ["Cloudflare D1", "Drizzle ORM"],
            "ai": ["OpenAI", "Higgsfield Cloud"],
            "integrations": ["Telegram Bot API", "HikerAPI", "DataForSEO", "SMTP"],
            "infra": ["Cloudflare Workers"],
        },
        "integrations": ["Telegram", "Higgsfield", "HikerAPI", "DataForSEO"],
        "current_problems": [
            "семь AI-сервисов без единой трассировки промптов и расходов",
            "нет RAG: дедупликация контента идёт текстовым сравнением",
        ],
        "planned_features": [
            "развитие Site Studio: секции, motion, preview",
            "расширение Competitor Intelligence",
        ],
        "priority_areas": [
            "наблюдаемость и стоимость LLM",
            "генерация и ревью креативов",
            "мультиязычный интейк на русском и казахском",
            "контент-workflow и mini-CRM",
        ],
        "technology_interests": [
            "prompt management", "structured output", "генерация изображений и видео",
            "edge-базы данных",
        ],
        "search_keywords": [
            "llm observability", "prompt management", "structured output", "image generation",
            "video generation", "content workflow", "crm", "telegram bot", "cloudflare d1",
            "drizzle orm",
        ],
        "things_not_needed": [
            "CAD и инженерные нормативы",
            "OCR чертежей и парсинг технической документации",
            "расчёты по ТК РК",
        ],
        "negative_keywords": PORTFOLIO_NEGATIVE,
    },
    {
        "slug": "project-radar",
        "name": "Project Radar",
        "github_repository": "andyrbek2709-tech/project-radar",
        "description": (
            "Персональный техрадар: сбор находок, дедупликация, оценка относительно профиля "
            "каждого проекта и решение CRITICAL / RECOMMENDED / REVIEW_LATER / REJECTED."
        ),
        "business_purpose": (
            "Показывать только то, что относится к конкретному проекту с его стеком и "
            "ограничениями, и объяснять, почему находка получила такую оценку."
        ),
        "existing_architecture": (
            "Python 3.12 и FastAPI, PostgreSQL 17 с pgvector на 768 измерений, Redis и Celery — "
            "worker и beat. Фронтенд Next.js 15. Groq — классификация и project-match, OpenAI — "
            "опциональный глубокий разбор. Промпты версионированы через PROMPT_VERSION, каждый "
            "вызов пишется в FindingAnalysis с моделью, токенами и стоимостью. Профиль проекта "
            "собирается техревизией репозитория и вручную, поля правленные руками защищены "
            "profile_locked_fields. Деплой Railway."
        ),
        "current_stack": {
            "backend": ["FastAPI", "Python 3.12", "SQLAlchemy", "Alembic", "Celery", "Redis"],
            "database": ["PostgreSQL 17", "pgvector"],
            "frontend": ["Next.js 15", "React", "TypeScript"],
            "ai": ["Groq", "OpenAI"],
            "infra": ["Railway", "Docker"],
        },
        "integrations": ["GitHub API", "Telegram", "Railway"],
        "current_problems": [
            "negative_keywords объединяются по всем проектам и работают как глобальный фильтр: "
            "исключение одного проекта режет находки остальным",
            "техревизия читала только корень репозитория и не видела стек монорепозиториев",
            "в выгрузку попадают репозитории без лицензии и с единичными форками без пометки",
        ],
        "planned_features": [
            "пометка зрелости и лицензии в карточке находки",
            "фильтр по языку относительно стека проекта",
        ],
        "priority_areas": [
            "качество профилирования проектов",
            "дедупликация находок",
            "скоринг и объяснимость решений",
            "стоимость LLM-вызовов",
        ],
        "technology_interests": ["pgvector", "дедупликация", "версионирование промптов"],
        "search_keywords": [
            "github trending", "deduplication", "embeddings", "pgvector", "llm scoring",
            "prompt versioning", "celery", "repository analysis",
        ],
        "things_not_needed": [
            "OCR и парсинг PDF — на вход приходят только README и метаданные репозиториев",
            "CAD и инженерные нормативы",
            "торговые стратегии",
        ],
        "negative_keywords": PORTFOLIO_NEGATIVE,
    },
]


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def apply_spec(project: Project, spec: dict[str, Any], *, force: bool) -> tuple[list[str], list[str]]:
    """Наложить спецификацию на существующий проект.

    Возвращает (что изменено, что пропущено как заблокированное). Пустое значение
    в спецификации не затирает заполненное в базе — сид не должен обеднять профиль,
    доведённый руками.
    """
    locked_fields = set(project.profile_locked_fields or [])
    changed: list[str] = []
    locked: list[str] = []

    for field in UPDATABLE:
        if field not in spec:
            continue
        new_value = spec[field]
        if _is_empty(new_value):
            continue
        if field in locked_fields and not force:
            if getattr(project, field) != new_value:
                locked.append(field)
            continue
        if getattr(project, field) == new_value:
            continue
        setattr(project, field, new_value)
        changed.append(field)

    return changed, locked
