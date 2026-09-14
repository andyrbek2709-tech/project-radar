#!/usr/bin/env python3
"""Завести и обновить профили проектов пользователя.

    cd backend
    python scripts/seed_projects.py --dry-run     # показать, что изменится
    python scripts/seed_projects.py               # применить
    python scripts/seed_projects.py --audit       # + техревизия репозиториев

Профиль — это то, относительно чего радар оценивает КАЖДУЮ находку
(`build_classification_prompt` и `build_project_match_prompt`). Пустой профиль
не означает «оценивай нейтрально»: модель достраивает недостающее сама. Так
EngHub, написанный на Express и TypeScript, попал в выгрузку 14.09.2026 как
«Python, FastAPI, PostgreSQL» — у проекта стоял `github_repository=None`,
техревизия не запускалась, и `current_stack` уходил в промпт пустым.

Скрипт идемпотентен: несуществующий проект создаётся, существующий
обновляется по полям. Поля из `profile_locked_fields` (правленные руками через
UI) не трогаются без `--force`. Пустое значение в спецификации ниже никогда не
затирает заполненное в базе.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Запуск как скрипта кладёт в sys.path каталог scripts/, а не корень backend/,
# поэтому `python scripts/<name>.py` падал с ModuleNotFoundError: No module named 'app'
# — и локально, и в контейнере (WORKDIR /app, PYTHONPATH не задан).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.data.project_profiles import PROJECTS, apply_spec
from app.models.project import Project
from app.services.profiler import audit_repository, create_project_with_audit, refresh_project_embedding

configure_logging("seed")
log = get_logger("collector")


def main() -> int:
    parser = argparse.ArgumentParser(description="Завести и обновить профили проектов")
    parser.add_argument("--dry-run", action="store_true", help="показать изменения и не сохранять")
    parser.add_argument(
        "--force", action="store_true", help="перезаписать и поля из profile_locked_fields"
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="после обновления провести техревизию репозитория (сеть и вызов LLM)",
    )
    parser.add_argument("--only", help="только эти slug через запятую")
    args = parser.parse_args()

    wanted = {s.strip() for s in args.only.split(",")} if args.only else None
    specs = [p for p in PROJECTS if wanted is None or p["slug"] in wanted]
    if not specs:
        print("Ни один проект не совпал с --only")
        return 1

    created = updated = untouched = 0

    try:
        with session_scope() as session:
            for spec in specs:
                slug = spec["slug"]
                project = session.execute(
                    select(Project).where(Project.slug == slug)
                ).scalar_one_or_none()

                if project is None:
                    if args.dry_run:
                        print(f"  + {slug}: будет создан ({spec['github_repository']})")
                        created += 1
                        continue
                    create_kwargs = dict(spec)
                    repo = create_kwargs.pop("github_repository", None)
                    # Репозиторий передаём в create только при --audit: иначе
                    # create_project_with_audit сразу пойдёт в GitHub и в LLM.
                    project = create_project_with_audit(
                        session,
                        github_repository=repo if args.audit else None,
                        **create_kwargs,
                    )
                    if repo and not args.audit:
                        project.github_repository = repo
                    print(f"  + {slug}: создан")
                    created += 1
                    continue

                changed, locked = apply_spec(project, spec, force=args.force)
                if locked:
                    print(f"  ! {slug}: заблокировано вручную, пропущено — {', '.join(locked)}")
                if not changed:
                    print(f"  = {slug}: без изменений")
                    untouched += 1
                    continue

                print(f"  ~ {slug}: обновлено — {', '.join(changed)}")
                updated += 1

                if args.dry_run:
                    continue

                # Профиль изменился — эмбеддинг по старому тексту больше не описывает проект.
                refresh_project_embedding(session, project)
                if args.audit and project.github_repository:
                    audit_repository(session, project)

            if args.dry_run:
                session.rollback()
                print("\n--dry-run: ничего не сохранено")

    except Exception as exc:  # noqa: BLE001
        print(f"\nОшибка: {exc}")
        return 1

    print(f"\nСоздано: {created}, обновлено: {updated}, без изменений: {untouched}")
    if not args.audit:
        print(
            "Техревизия репозиториев не запускалась. Запусти с --audit "
            "или POST /api/projects/{id}/audit, чтобы подтянуть стек из кода."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
