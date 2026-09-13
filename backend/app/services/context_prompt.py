"""COPY ANALYSIS CONTEXT — готовый промпт для ChatGPT/Codex одним кликом.

Собирается на бэкенде, а не на фронте: контекст (профиль проекта, существующие
фичи, метрики репозитория) живёт в базе, и дублировать его в браузер незачем.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Decision
from app.models.finding import Finding, FindingProjectMatch
from app.models.project import Project, ProjectFeature

MAX_README_IN_PROMPT = 4000


def build_analysis_context(
    session: Session,
    *,
    finding: Finding,
    project: Project,
    match: FindingProjectMatch | None = None,
) -> str:
    repo = finding.repository
    features = session.execute(
        select(ProjectFeature).where(ProjectFeature.project_id == project.id)
    ).scalars().all()

    nearest_name = None
    if match is not None and match.nearest_feature_id:
        nearest = session.get(ProjectFeature, match.nearest_feature_id)
        nearest_name = nearest.name if nearest else None

    decision = session.execute(
        select(Decision)
        .where(
            Decision.finding_id == finding.id,
            Decision.project_id == project.id,
            Decision.is_current.is_(True),
        )
        .limit(1)
    ).scalar_one_or_none()

    lines: list[str] = [
        f"Проанализируй этот repository относительно проекта {project.name}.",
        "",
    ]

    # --- находка -----------------------------------------------------------
    if repo is not None:
        lines.append(f"REPOSITORY: {repo.full_name}")
        lines.append(f"URL: {repo.url}")
        meta = [
            f"★ {repo.stars}",
            f"forks {repo.forks}",
            repo.language or "язык не указан",
            f"лицензия {repo.license_spdx or 'не указана'}",
        ]
        if repo.pushed_at_gh:
            meta.append(f"последний push {_fmt_date(repo.pushed_at_gh)}")
        if repo.contributors_count is not None:
            meta.append(f"контрибьюторов {repo.contributors_count}")
        lines.append("МЕТРИКИ: " + " · ".join(meta))
        if repo.latest_release_tag:
            lines.append(
                f"ПОСЛЕДНИЙ РЕЛИЗ: {repo.latest_release_tag} "
                f"({_fmt_date(repo.latest_release_at)})"
            )
        if repo.topics:
            lines.append("TOPICS: " + ", ".join(repo.topics[:15]))
        if repo.description:
            lines.append(f"ОПИСАНИЕ: {repo.description}")
    else:
        lines.append(f"НАХОДКА: {finding.title}")
        if finding.url:
            lines.append(f"URL: {finding.url}")

    if finding.summary:
        lines += ["", f"ЧТО ЭТО: {finding.summary}"]

    # --- почему radar её выбрал -------------------------------------------
    lines += ["", "ПОЧЕМУ RADAR ЕГО ВЫБРАЛ:"]
    if match is not None:
        lines.append(
            f"  relevance {match.relevance_score:.2f} · "
            f"project_fit {match.project_fit_score:.2f} · "
            f"improvement {match.improvement_score:.2f} · "
            f"novelty {match.novelty_score:.2f} · "
            f"risk {match.risk_score:.2f} · "
            f"radar {match.radar_score:.2f}"
        )
        if match.why_relevant:
            lines.append(f"  {match.why_relevant}")
        if nearest_name and match.max_similarity_to_features is not None:
            lines.append(
                f"  ближайшая существующая фича: «{nearest_name}» "
                f"(похожесть {match.max_similarity_to_features:.2f})"
            )
        if match.categories:
            lines.append(f"  категории: {', '.join(match.categories)}")
    else:
        lines.append("  (оценка ещё не посчитана)")

    if finding.found_via:
        lines.append(f"  найдено через: {', '.join(finding.found_via)} (появлений: {finding.seen_count})")
    if decision is not None:
        lines.append(f"  текущий статус: {decision.status} — {decision.reason}")

    # --- контекст проекта --------------------------------------------------
    lines += ["", f"НАШ ПРОЕКТ: {project.name}"]
    if project.description:
        lines.append(project.description)
    if project.business_purpose:
        lines.append(f"Назначение: {project.business_purpose}")

    stack = project.current_stack or {}
    if stack:
        flat = " · ".join(f"{k}: {', '.join(v)}" for k, v in stack.items() if v)
        lines.append(f"НАШ ТЕКУЩИЙ СТЕК: {flat}")
    if project.existing_architecture:
        lines.append(f"АРХИТЕКТУРА: {project.existing_architecture}")

    if features:
        lines += ["", "ЧТО УЖЕ РЕАЛИЗОВАНО:"]
        for f in features[:40]:
            suffix = f" — {f.description}" if f.description else ""
            lines.append(f"  - {f.name}{suffix}")

    for label, values in (
        ("ИЗВЕСТНЫЕ ПРОБЛЕМЫ", project.current_problems),
        ("ЗАПЛАНИРОВАНО", project.planned_features),
        ("ПРИОРИТЕТЫ", project.priority_areas),
        ("НАМ ТОЧНО НЕ НУЖНО", project.things_not_needed),
    ):
        if values:
            lines.append(f"{label}: {'; '.join(values)}")

    # --- README ------------------------------------------------------------
    readme = (repo.readme_text if repo else None) or finding.content_text
    if readme:
        lines += ["", "README / ОПИСАНИЕ (фрагмент):", "---", readme[:MAX_README_IN_PROMPT].strip(), "---"]

    # --- вопрос ------------------------------------------------------------
    lines += [
        "",
        "ВОПРОС: даёт ли это реальное улучшение относительно того, что у нас уже есть,",
        "и стоит ли внедрять?",
        "",
        "Разбери по пунктам:",
        "  1. что технология делает;",
        "  2. что у нас есть сейчас для этой задачи;",
        "  3. что она заменяет, а что дополняет;",
        "  4. преимущество относительно текущего подхода — конкретно;",
        "  5. сложность внедрения: новые сервисы, новые зависимости, миграция;",
        "  6. maintenance burden, vendor lock-in, риски лицензии и безопасности;",
        "  7. вердикт: внедрять / отложить / отказаться — и почему.",
        "",
        "Будь скептичен: по умолчанию менять работающее не надо, ищи конкретные",
        "причины, почему всё-таки надо.",
    ]

    return "\n".join(lines)


def _fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value) if value else "n/a"


__all__ = ["build_analysis_context"]
