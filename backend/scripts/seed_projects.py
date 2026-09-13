#!/usr/bin/env python3
"""Завести проекты пользователя и, если указан репозиторий, сразу собрать профиль.

    cd backend
    python scripts/seed_projects.py

Репозитории правятся здесь. Приватные можно оставить с github_repository=None —
профиль тогда заполняется вручную через UI или PATCH /api/projects/{id}.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.models.project import Project
from app.services.profiler import create_project_with_audit

configure_logging("seed")
log = get_logger("collector")

PROJECTS: list[dict] = [
    {
        "slug": "enghub",
        "name": "EngHub",
        "description": "Инженерная документация и работа с нормативами",
        "github_repository": None,  # ← "andyrbek2709-tech/enghub"
        "priority_areas": ["RAG", "Document AI", "парсинг PDF", "поиск по нормативам"],
        "search_keywords": ["rag", "document parsing", "pdf extraction", "table extraction", "ocr"],
        "negative_keywords": ["crypto", "trading bot", "game", "nft"],
    },
    {
        "slug": "vformate",
        "name": "VFORMATE",
        "description": "",
        "github_repository": None,
        "priority_areas": [],
        "search_keywords": [],
        "negative_keywords": ["crypto", "nft"],
    },
    {
        "slug": "trackparts",
        "name": "TrackParts",
        "description": "",
        "github_repository": None,
        "priority_areas": [],
        "search_keywords": [],
        "negative_keywords": ["crypto", "nft"],
    },
    {
        "slug": "retail-gost",
        "name": "Retail / ГОСТ",
        "description": "Расчётчики и проверки по СН РК и ГОСТ",
        "github_repository": None,
        "priority_areas": ["нормативные расчёты", "валидация данных"],
        "search_keywords": ["rules engine", "formula parser", "spreadsheet engine"],
        "negative_keywords": ["crypto", "nft", "game"],
    },
]


def main() -> int:
    created = skipped = 0
    with session_scope() as session:
        for spec in PROJECTS:
            exists = session.execute(
                select(Project).where(Project.slug == spec["slug"])
            ).scalar_one_or_none()
            if exists is not None:
                print(f"  = {spec['slug']}: уже есть, пропускаю")
                skipped += 1
                continue

            project = create_project_with_audit(session, **spec)
            mark = "с техревизией" if spec.get("github_repository") else "без репозитория"
            print(f"  + {project.slug}: создан ({mark})")
            created += 1

    print(f"\nСоздано: {created}, пропущено: {skipped}")
    if any(p["github_repository"] is None for p in PROJECTS):
        print(
            "\nУ части проектов не указан github_repository — автопрофилирование "
            "для них не запускалось.\nПропиши репозитории в scripts/seed_projects.py "
            "или через PATCH /api/projects/{id}, затем POST /api/projects/{id}/audit."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
