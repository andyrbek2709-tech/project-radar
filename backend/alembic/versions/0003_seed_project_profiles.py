"""Профили всех семи проектов пользователя.

Профиль проекта — вход для оценки каждой находки. У enghub стоял
github_repository=None, техревизия не запускалась, current_stack уходил
в промпт пустым — и модель дописывала стек сама: в выгрузке 14.09.2026
Express-проект описан как «Python, FastAPI, PostgreSQL» во всех 48 карточках.
У vformate и trackparts профиль был пустой, а lohotron, zharnama и
project-radar в базе отсутствовали, поэтому все находки уходили в enghub —
других кандидатов классификация просто не видела.

Данные применяются миграцией, а не только скриптом, потому что деплой
выполняет `alembic upgrade head`, и иначе исправленный профиль не доехал бы
до прода без ручного запуска.

Поля, отмеченные пользователем в profile_locked_fields, не трогаются:
правки через интерфейс важнее этой спецификации.

Эмбеддинг проекта здесь не пересчитывается — для этого нужен сетевой вызов,
недопустимый в миграции. На поведение радара это не влияет: Project.embedding
нигде в скоринге и дедупликации не читается (используются Finding.embedding и
ProjectFeature.embedding). Освежить профильный вектор можно потом —
`python scripts/seed_projects.py --audit` или POST /api/projects/{id}/audit.

Revision ID: 0003_seed_project_profiles
Revises: 0002_idempotency_ok
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.data.project_profiles import PROJECTS, UPDATABLE

revision = "0003_seed_project_profiles"
down_revision = "0002_idempotency_ok"
branch_labels = None
depends_on = None

_TEXT_ARRAY = postgresql.ARRAY(sa.Text())

# Лёгкое описание таблицы вместо ORM-модели: миграция не должна зависеть от
# того, как модель выглядит в будущем.
projects = sa.table(
    "projects",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("slug", sa.Text()),
    sa.column("name", sa.Text()),
    sa.column("description", sa.Text()),
    sa.column("business_purpose", sa.Text()),
    sa.column("existing_architecture", sa.Text()),
    sa.column("current_stack", postgresql.JSONB()),
    sa.column("integrations", _TEXT_ARRAY),
    sa.column("current_problems", _TEXT_ARRAY),
    sa.column("planned_features", _TEXT_ARRAY),
    sa.column("technology_interests", _TEXT_ARRAY),
    sa.column("search_keywords", _TEXT_ARRAY),
    sa.column("negative_keywords", _TEXT_ARRAY),
    sa.column("things_not_needed", _TEXT_ARRAY),
    sa.column("priority_areas", _TEXT_ARRAY),
    sa.column("github_repository", sa.Text()),
    sa.column("profile_locked_fields", _TEXT_ARRAY),
    sa.column("profile_source", sa.Text()),
    sa.column("profile_text", sa.Text()),
    sa.column("is_active", sa.Boolean()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _profile_text(spec: dict) -> str:
    """Повторяет profiler.build_profile_text, но по словарю и без обращения к сети."""
    parts = [spec["name"], spec.get("description") or "", spec.get("business_purpose") or ""]
    stack = spec.get("current_stack") or {}
    if stack:
        parts.append("Стек: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in stack.items() if v))
    for label, key in (
        ("Проблемы", "current_problems"),
        ("Планы", "planned_features"),
        ("Приоритеты", "priority_areas"),
        ("Интересы", "technology_interests"),
        ("Ключевые слова", "search_keywords"),
    ):
        if spec.get(key):
            parts.append(f"{label}: {', '.join(spec[key])}")
    if spec.get("existing_architecture"):
        parts.append(f"Архитектура: {spec['existing_architecture']}")
    return "\n".join(p for p in parts if p).strip()


def upgrade() -> None:
    bind = op.get_bind()

    existing = {
        row.slug: set(row.profile_locked_fields or [])
        for row in bind.execute(sa.select(projects.c.slug, projects.c.profile_locked_fields))
    }

    for spec in PROJECTS:
        slug = spec["slug"]
        values = {
            field: spec[field]
            for field in UPDATABLE
            if field in spec and spec[field] not in (None, "", [], {})
        }
        values["profile_text"] = _profile_text(spec)

        if slug not in existing:
            bind.execute(
                projects.insert().values(
                    id=uuid.uuid4(),
                    slug=slug,
                    profile_source="manual",
                    is_active=True,
                    profile_locked_fields=[],
                    **values,
                )
            )
            continue

        locked = existing[slug]
        allowed = {k: v for k, v in values.items() if k not in locked}
        if not allowed:
            continue
        bind.execute(
            projects.update()
            .where(projects.c.slug == slug)
            .values(updated_at=sa.func.now(), **allowed)
        )


def downgrade() -> None:
    """Откат не восстанавливает прежние профили.

    Прежнее состояние — пустые и неверные профили, из-за которых радар
    оценивал проекты по выдуманному стеку; возвращать его нечем и незачем.
    Созданные проекты не удаляем: к ним уже могут быть привязаны находки.
    """
    pass
