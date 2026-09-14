"""Автопрофилирование: техревизия репозитория → Project Profile.

Create Project автоматически формирует первичный search profile. Пользователь
потом правит вручную только то, что система не определила — отредактированные
поля попадают в profile_locked_fields и повторным аудитом не затираются.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.embeddings import get_embedding_provider
from app.analysis.llm import GroqClient, get_analysis_client, LLMError, LLMSchemaError
from app.analysis.prompts import PROMPT_VERSION, REPO_AUDIT_SYSTEM, build_repo_audit_prompt
from app.analysis.schemas import RepoAuditResult
from app.collectors.github_client import GitHubClient, GitHubError
from app.core.config import settings
from app.core.logging import get_logger
from app.models.project import Project, ProjectFeature, ProjectRepoAudit
from app.services import usage

log = get_logger("collector")

# Файлы, по которым восстанавливается стек. Порядок = приоритет при лимите бюджета.
AUDIT_FILES = [
    "README.md",
    "readme.md",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "alembic.ini",
    "next.config.js",
    "next.config.mjs",
    "go.mod",
    "Cargo.toml",
    ".env.example",
]

# Каталоги, наличие которых само по себе говорит об архитектуре.
AUDIT_DIRS = ["app", "src", "backend", "frontend", "api", "services", "web", "migrations"]

# Монорепозиторий прячет настоящий стек на уровень ниже корня. У ai-institut в
# корневом package.json одна строка про exceljs, а Express, Supabase и OCR лежат
# в services/api-server; у Sas_vformate то же самое в apps/api. Аудит, читавший
# только корень, видел заглушку и отдавал в промпт пустой current_stack — модель
# дописывала стек сама, и так EngHub на Express попал в радар как FastAPI.
WORKSPACE_DIRS = ("services", "apps", "packages", "backend", "frontend", "web", "api", "bot", "bots")

# Внутри воркспейса читаем только манифесты: README подпакета стек не уточняет,
# а запрос к GitHub тратит.
WORKSPACE_MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "Cargo.toml")

# Потолок на вложенные манифесты: у ai-institut в services/ десяток сервисов,
# и без ограничения один аудит съедает заметную долю часового лимита GitHub API.
MAX_WORKSPACE_FILES = 12

# Детекторы стека по зависимостям — работают даже без LLM.
STACK_SIGNATURES: dict[str, tuple[str, ...]] = {
    "fastapi": ("backend", "FastAPI"),
    "django": ("backend", "Django"),
    "flask": ("backend", "Flask"),
    "celery": ("backend", "Celery"),
    "sqlalchemy": ("backend", "SQLAlchemy"),
    "alembic": ("backend", "Alembic"),
    "pydantic": ("backend", "Pydantic"),
    "psycopg": ("database", "PostgreSQL"),
    "asyncpg": ("database", "PostgreSQL"),
    "pgvector": ("database", "pgvector"),
    "redis": ("infra", "Redis"),
    "next": ("frontend", "Next.js"),
    "react": ("frontend", "React"),
    "tailwindcss": ("frontend", "Tailwind CSS"),
    "typescript": ("frontend", "TypeScript"),
    "openai": ("ai", "OpenAI"),
    "anthropic": ("ai", "Anthropic"),
    "groq": ("ai", "Groq"),
    "langchain": ("ai", "LangChain"),
    "llama-index": ("ai", "LlamaIndex"),
    "sentence-transformers": ("ai", "sentence-transformers"),
    "pdfplumber": ("parsing", "pdfplumber"),
    "pypdf": ("parsing", "pypdf"),
    "tesseract": ("parsing", "Tesseract OCR"),
    "pytesseract": ("parsing", "Tesseract OCR"),
    "telethon": ("integrations", "Telethon"),
    "httpx": ("backend", "httpx"),
    "docker": ("infra", "Docker"),
    # Node-бэкенды: без них Express-проект определялся как «фронтенд на React».
    "express": ("backend", "Express"),
    "fastify": ("backend", "Fastify"),
    "@nestjs/core": ("backend", "NestJS"),
    "prisma": ("backend", "Prisma"),
    "drizzle-orm": ("backend", "Drizzle ORM"),
    "turbo": ("infra", "Turborepo"),
    "vite": ("frontend", "Vite"),
    "wrangler": ("infra", "Cloudflare Workers"),
    "@supabase/supabase-js": ("database", "Supabase"),
    "aiosqlite": ("database", "SQLite"),
    # Парсинг и OCR на стороне Node — раньше ловился только питоновский Tesseract.
    "tesseract.js": ("parsing", "Tesseract.js"),
    "pymupdf": ("parsing", "PyMuPDF"),
    "python-docx": ("parsing", "python-docx"),
    "openpyxl": ("parsing", "openpyxl"),
    "exceljs": ("parsing", "ExcelJS"),
    "fpdf": ("parsing", "FPDF"),
    # CAD — отдельная область, в общие «parsing»/«backend» её сваливать нельзя.
    "cadquery": ("cad", "CadQuery"),
    "ezdxf": ("cad", "ezdxf"),
    "ifcopenshell": ("cad", "IfcOpenShell"),
    "dxf-parser": ("cad", "dxf-parser"),
    "python-telegram-bot": ("integrations", "python-telegram-bot"),
    "aiogram": ("integrations", "aiogram"),
    "telegraf": ("integrations", "Telegraf"),
}


def _signature_pattern(signature: str) -> str:
    """\\b вокруг «@nestjs/core» не сработает: рядом с @ и / нет границы слова,
    и пакет никогда не находился. Границу ставим только там, где она осмысленна."""
    left = r"\b" if signature[:1].isalnum() else ""
    right = r"\b" if signature[-1:].isalnum() else ""
    return left + re.escape(signature) + right


def _fetch_text(client: GitHubClient, full_name: str, path: str) -> str | None:
    """Содержимое одного файла репозитория. None — если файла нет или он бинарный."""
    try:
        resp = client._request("GET", f"/repos/{full_name}/contents/{path}", allow_404=True)
    except GitHubError:
        return None
    if resp.status != 200 or not isinstance(resp.data, dict):
        return None
    content = resp.data.get("content")
    if resp.data.get("encoding") != "base64" or not content:
        return None
    try:
        return base64.b64decode(content).decode("utf-8", errors="replace")[:12000]
    except Exception:  # noqa: BLE001
        return None


def _fetch_workspace_manifests(
    client: GitHubClient, full_name: str, root_entries: dict[str, Any]
) -> dict[str, str]:
    """Манифесты пакетов монорепозитория: services/api-server/package.json и такое же.

    Ровно один уровень вложенности. Глубже начинается перебор всего дерева, а
    стек в двухуровневых воркспейсах (pnpm, turbo, services/*) виден уже здесь.
    """
    found: dict[str, str] = {}
    for workspace in WORKSPACE_DIRS:
        if len(found) >= MAX_WORKSPACE_FILES:
            break
        entry = root_entries.get(workspace)
        if not entry or entry.get("type") != "dir":
            continue
        try:
            listing = client._request(
                "GET", f"/repos/{full_name}/contents/{workspace}", allow_404=True
            )
        except GitHubError:
            continue
        if listing.status != 200 or not isinstance(listing.data, list):
            continue

        for child in listing.data:
            if len(found) >= MAX_WORKSPACE_FILES:
                break
            if not isinstance(child, dict) or child.get("type") != "dir":
                continue
            for manifest in WORKSPACE_MANIFESTS:
                path = f"{workspace}/{child['name']}/{manifest}"
                text = _fetch_text(client, full_name, path)
                if text is not None:
                    found[path] = text
                    break  # один манифест на пакет — язык пакета уже ясен
    if found:
        log.info("audit_workspace_manifests", repo=full_name, count=len(found))
    return found


def fetch_repo_files(client: GitHubClient, full_name: str) -> tuple[dict[str, str], str | None]:
    """Забрать содержимое ключевых файлов и список каталогов верхнего уровня."""
    files: dict[str, str] = {}
    commit_sha: str | None = None

    try:
        root = client._request("GET", f"/repos/{full_name}/contents/", allow_404=True)
    except GitHubError as exc:
        log.warning("audit_contents_failed", repo=full_name, error=str(exc)[:200])
        return files, None

    if root.status != 200 or not isinstance(root.data, list):
        return files, None

    present = {entry["name"]: entry for entry in root.data if isinstance(entry, dict)}

    dirs = [name for name, e in present.items() if e.get("type") == "dir" and name in AUDIT_DIRS]
    if dirs:
        files["__structure__"] = "Каталоги верхнего уровня: " + ", ".join(sorted(dirs))

    for filename in AUDIT_FILES:
        if filename not in present:
            continue
        text = _fetch_text(client, full_name, filename)
        if text is not None:
            files[filename] = text

    for path, text in _fetch_workspace_manifests(client, full_name, present).items():
        files[path] = text

    try:
        commits = client._request(
            "GET", f"/repos/{full_name}/commits", params={"per_page": 1}, allow_404=True
        )
        if commits.status == 200 and isinstance(commits.data, list) and commits.data:
            commit_sha = commits.data[0].get("sha")
    except GitHubError:
        pass

    return files, commit_sha


def detect_stack_heuristically(files: dict[str, str]) -> dict[str, list[str]]:
    """Детект по зависимостям без единого вызова LLM.

    Работает как база: LLM потом дополняет, но не заменяет — то, что видно
    в requirements.txt, гадать не нужно.
    """
    # Сравниваем по имени файла, а не по полному пути: манифест пакета приходит
    # как "services/api-server/package.json" и в прежний набор строк не попадал.
    manifests = {
        "requirements.txt", "pyproject.toml", "package.json",
        "docker-compose.yml", "docker-compose.yaml", "Dockerfile",
        "go.mod", "Cargo.toml",
    }
    blob = "\n".join(
        content.lower()
        for name, content in files.items()
        if name.rsplit("/", 1)[-1] in manifests
    )
    stack: dict[str, list[str]] = {}
    for signature, (area, label) in STACK_SIGNATURES.items():
        if re.search(_signature_pattern(signature), blob):
            stack.setdefault(area, [])
            if label not in stack[area]:
                stack[area].append(label)
    return stack


def audit_repository(
    session: Session, project: Project, *, groq: GroqClient | None = None
) -> ProjectRepoAudit:
    """Полная техревизия. Результат сохраняется целиком — можно перестроить
    профиль, не ходя в GitHub повторно."""
    audit = ProjectRepoAudit(
        project_id=project.id,
        repo_full_name=project.github_repository or "",
        status="pending",
    )
    session.add(audit)
    session.flush()

    if not project.github_repository:
        audit.status = "failed"
        audit.error = "github_repository не указан у проекта"
        return audit

    if not settings.GITHUB_TOKEN:
        audit.status = "failed"
        audit.error = "GITHUB_TOKEN не задан — приватный репозиторий не прочитать"
        return audit

    try:
        with GitHubClient() as client:
            files, commit_sha = fetch_repo_files(client, project.github_repository)
    except Exception as exc:  # noqa: BLE001
        audit.status = "failed"
        audit.error = str(exc)[:500]
        log.warning("audit_failed", project=project.slug, error=str(exc)[:200])
        return audit

    if not files:
        audit.status = "failed"
        audit.error = "не удалось прочитать содержимое репозитория (приватный или пустой)"
        return audit

    audit.commit_sha = commit_sha
    audit.files_collected = {k: v[:4000] for k, v in files.items()}

    heuristic_stack = detect_stack_heuristically(files)
    audit.detected_stack = heuristic_stack

    groq = groq or get_analysis_client()
    if not groq.enabled:
        audit.status = "ok"
        audit.error = "Groq выключен — профиль построен только по эвристике"
        apply_audit(session, project, audit, result=None, heuristic_stack=heuristic_stack)
        return audit

    prompt = build_repo_audit_prompt(repo_full_name=project.github_repository, files=files)
    try:
        result = groq.complete_structured(
            system=REPO_AUDIT_SYSTEM,
            user=prompt,
            schema_model=RepoAuditResult,
            schema_name="repo_audit",
            model=settings.GROQ_ESCALATION_MODEL,  # ревизия стоит того, чтобы взять модель покрупнее
        )
    except (LLMError, LLMSchemaError) as exc:
        audit.status = "failed"
        audit.error = str(exc)[:500]
        apply_audit(session, project, audit, result=None, heuristic_stack=heuristic_stack)
        return audit

    parsed: RepoAuditResult = result.parsed
    audit.detected_features = [f for f in parsed.existing_features]
    audit.llm_model = result.model
    audit.status = "ok"

    usage.record(
        session,
        provider="groq",
        operation="repo_audit",
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=result.cost_usd,
    )

    apply_audit(session, project, audit, result=parsed, heuristic_stack=heuristic_stack)
    return audit


def apply_audit(
    session: Session,
    project: Project,
    audit: ProjectRepoAudit,
    *,
    result: RepoAuditResult | None,
    heuristic_stack: dict[str, list[str]],
) -> None:
    """Заполнить профиль. Поля, отредактированные человеком, не трогаем."""
    locked = set(project.profile_locked_fields or [])

    def setter(field_name: str, value: Any) -> None:
        if field_name in locked or not value:
            return
        setattr(project, field_name, value)

    # Эвристика и LLM объединяются: файлы надёжнее, модель шире.
    merged_stack: dict[str, list[str]] = {k: list(v) for k, v in heuristic_stack.items()}
    if result:
        for area, items in (result.current_stack or {}).items():
            merged_stack.setdefault(area, [])
            for item in items:
                if item not in merged_stack[area]:
                    merged_stack[area].append(item)
    setter("current_stack", merged_stack)

    if result:
        setter("description", result.description)
        setter("business_purpose", result.business_purpose)
        setter("existing_architecture", result.existing_architecture)
        setter("integrations", result.integrations)
        setter("technology_interests", result.technology_interests)
        setter("search_keywords", result.search_keywords)
        setter("negative_keywords", result.negative_keywords)
        setter("current_problems", result.current_problems)
        _sync_features(session, project, result.existing_features)

    if not project.search_keywords:
        # Без ключевых слов GitHub Radar не знает, что искать для этого проекта.
        project.search_keywords = _fallback_keywords(merged_stack)

    project.profile_source = "auto_audit" if not locked else "hybrid"
    refresh_project_embedding(session, project)


def _sync_features(
    session: Session, project: Project, features: list[dict[str, str]]
) -> None:
    """Добавляем только новое: удалять руками заведённые фичи нельзя."""
    if not features:
        return

    existing = {
        f.name.strip().lower()
        for f in session.execute(
            select(ProjectFeature).where(ProjectFeature.project_id == project.id)
        ).scalars().all()
    }

    provider = get_embedding_provider()
    fresh: list[ProjectFeature] = []
    for item in features:
        name = (item.get("name") or "").strip()
        if not name or name.lower() in existing:
            continue
        fresh.append(
            ProjectFeature(
                project_id=project.id,
                name=name[:300],
                description=(item.get("description") or "")[:2000],
                category=(item.get("category") or "")[:64] or None,
                source="auto_audit",
                evidence={"audit": True},
            )
        )
        existing.add(name.lower())

    if not fresh:
        return

    try:
        vectors = provider.embed([f"{f.name}. {f.description or ''}" for f in fresh])
        for feature, vector in zip(fresh, vectors):
            feature.embedding = vector
    except Exception as exc:  # noqa: BLE001
        log.warning("feature_embedding_failed", project=project.slug, error=str(exc)[:200])

    session.add_all(fresh)
    session.flush()


def _fallback_keywords(stack: dict[str, list[str]]) -> list[str]:
    keywords: list[str] = []
    for items in stack.values():
        for item in items:
            token = item.lower().replace(" ", "-")
            if token not in keywords:
                keywords.append(token)
    return keywords[:15]


def build_profile_text(project: Project) -> str:
    """Сводный текст профиля — из него считается эмбеддинг для сравнения с находками."""
    parts = [project.name, project.description or "", project.business_purpose or ""]
    stack = project.current_stack or {}
    if stack:
        parts.append(
            "Стек: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in stack.items() if v)
        )
    for label, values in (
        ("Проблемы", project.current_problems),
        ("Планы", project.planned_features),
        ("Приоритеты", project.priority_areas),
        ("Интересы", project.technology_interests),
        ("Ключевые слова", project.search_keywords),
    ):
        if values:
            parts.append(f"{label}: {', '.join(values)}")
    if project.existing_architecture:
        parts.append(f"Архитектура: {project.existing_architecture}")
    return "\n".join(p for p in parts if p).strip()


def refresh_project_embedding(session: Session, project: Project) -> None:
    text = build_profile_text(project)
    project.profile_text = text
    try:
        project.embedding = get_embedding_provider().embed([text])[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("project_embedding_failed", project=project.slug, error=str(exc)[:200])


def create_project_with_audit(
    session: Session, *, slug: str, name: str, github_repository: str | None = None, **fields: Any
) -> Project:
    """Create Project = создать + сразу провести техревизию, если указан репозиторий."""
    project = Project(
        slug=slug,
        name=name,
        github_repository=github_repository,
        profile_source="manual",
        **{k: v for k, v in fields.items() if v is not None},
    )
    session.add(project)
    session.flush()

    if github_repository:
        audit_repository(session, project)
    else:
        refresh_project_embedding(session, project)

    log.info(
        "project_created",
        slug=slug,
        with_audit=bool(github_repository),
        keywords=len(project.search_keywords or []),
    )
    return project


__all__ = [
    "audit_repository",
    "apply_audit",
    "create_project_with_audit",
    "refresh_project_embedding",
    "build_profile_text",
    "detect_stack_heuristically",
    "fetch_repo_files",
]
