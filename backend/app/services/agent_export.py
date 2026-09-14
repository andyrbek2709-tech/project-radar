"""Выгрузка находок одним Markdown-файлом — на вход агенту.

Зачем отдельный формат, а не JSON выдачи API: файл читают двое — человек
глазами и агент, которому предстоит решать, внедрять или нет. JSON неудобен
первому, голый текст — второму. Markdown с жёсткой структурой разделов
устраивает обоих, а заодно переживает копирование в любой чат.

Собирается на бэкенде по той же причине, что и COPY ANALYSIS CONTEXT:
профиль проекта, разбор модели и метрики репозитория живут в базе.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.analysis import Decision, FindingAnalysis
from app.models.finding import Finding, FindingProjectMatch
from app.models.project import Project, ProjectFeature

# Порядок разделов внутри карточки. Ключ разбора → заголовок в файле.
# Список явный, а не обход словаря: агент должен видеть поля в одном и том же
# порядке от выгрузки к выгрузке, иначе diff между файлами нечитаем.
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("what_it_does", "Что делает"),
    ("what_we_have", "Что у нас уже есть"),
    ("expected_benefit", "Ожидаемая польза"),
    ("verdict_reason", "Почему такой вердикт"),
)

_LEVELS = {"low": "низкая", "medium": "средняя", "high": "высокая"}
_LOCK_IN = {"none": "нет", "partial": "частичный", "strong": "сильный"}


def build_agent_digest(
    session: Session,
    *,
    statuses: list[str],
    project_slug: str | None = None,
    limit: int = 200,
) -> str:
    """Markdown со всеми находками в указанных статусах."""
    stmt = (
        select(Finding, FindingProjectMatch, Decision, Project)
        .options(selectinload(Finding.repository))
        .join(FindingProjectMatch, FindingProjectMatch.finding_id == Finding.id)
        .join(
            Decision,
            (Decision.finding_id == Finding.id)
            & (Decision.project_id == FindingProjectMatch.project_id)
            & (Decision.is_current.is_(True)),
        )
        .join(Project, Project.id == FindingProjectMatch.project_id)
        .where(Decision.status.in_(statuses))
        .order_by(
            Decision.status,
            FindingProjectMatch.radar_score.desc().nullslast(),
        )
        .limit(limit)
    )
    rows = session.execute(stmt).all()

    now = datetime.now(settings.tz)
    head = [
        "# Project Radar — находки для агента",
        "",
        _header_line(now, statuses, project_slug, len(rows)),
        "",
        "Каждый раздел самодостаточен: ссылка, метрики, разбор модели и то, что "
        "у нас уже есть. Оценки приведены как есть — проверяй их по ссылке, "
        "а не принимай на веру.",
    ]

    if not rows:
        head += ["", "Находок в этих статусах нет."]
        return "\n".join(head) + "\n"

    features = _feature_names(session, [m for _, m, _, _ in rows])

    body: list[str] = []
    for number, (finding, match, decision, project) in enumerate(rows, start=1):
        body += ["", "---", ""]
        body += _card(session, number, finding, match, decision, project, features)

    return "\n".join(head + body) + "\n"


def _header_line(
    now: datetime, statuses: list[str], project_slug: str | None, count: int
) -> str:
    parts = [f"Выгружено {now:%d.%m.%Y %H:%M}", f"статусы: {', '.join(statuses)}"]
    if project_slug:
        parts.append(f"проект: {project_slug}")
    parts.append(f"находок: {count}")
    return " · ".join(parts)


def _feature_names(
    session: Session, matches: list[FindingProjectMatch]
) -> dict[Any, str]:
    """Имена ближайших фич одним запросом, а не по одному на карточку."""
    ids = {m.nearest_feature_id for m in matches if m.nearest_feature_id}
    if not ids:
        return {}
    rows = session.execute(
        select(ProjectFeature.id, ProjectFeature.name).where(ProjectFeature.id.in_(ids))
    ).all()
    return {fid: name for fid, name in rows}


def _card(
    session: Session,
    number: int,
    finding: Finding,
    match: FindingProjectMatch,
    decision: Decision,
    project: Project,
    features: dict[Any, str],
) -> list[str]:
    repo = finding.repository
    name = repo.full_name if repo is not None else finding.title
    score = match.radar_score if match.radar_score is not None else 0.0

    lines = [
        f"## {number}. {name}",
        "",
        f"**Статус:** {decision.status} · **проект:** {project.name} "
        f"· **radar:** {score:.2f}",
    ]

    url = (repo.url if repo is not None else None) or finding.url
    if url:
        lines.append(f"**Ссылка:** {url}")

    if repo is not None:
        meta = [
            f"★ {repo.stars}",
            f"forks {repo.forks}",
            repo.language or "язык не указан",
            f"лицензия {repo.license_spdx or 'не указана'}",
        ]
        if repo.pushed_at_gh:
            meta.append(f"push {repo.pushed_at_gh:%d.%m.%Y}")
        lines.append("**Метрики:** " + " · ".join(meta))
        if repo.description:
            lines.append(f"**Описание:** {repo.description}")

    why = [
        f"relevance {match.relevance_score:.2f}",
        f"project_fit {match.project_fit_score:.2f}",
    ]
    nearest = features.get(match.nearest_feature_id)
    if nearest:
        why.append(f"ближайшая фича «{nearest}»")
    lines.append("**Почему выбрано:** " + " · ".join(why))

    if decision.reason:
        lines.append(f"**Решение движка:** {decision.reason}")

    analysis = _latest_analysis(session, finding, project)
    if analysis is None:
        lines += ["", "_Глубокого разбора нет — решение принято по метрикам._"]
        return lines

    lines += _analysis_sections(analysis)
    return lines


def _latest_analysis(
    session: Session, finding: Finding, project: Project
) -> dict[str, Any] | None:
    row = session.execute(
        select(FindingAnalysis)
        .where(
            FindingAnalysis.finding_id == finding.id,
            FindingAnalysis.project_id == project.id,
            FindingAnalysis.status == "ok",
        )
        .order_by(FindingAnalysis.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None or not row.result:
        return None
    return row.result


def _analysis_sections(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    for key, title in _SECTIONS:
        value = (result.get(key) or "").strip()
        if value:
            lines += ["", f"### {title}", "", value]

    replaces = [str(x) for x in (result.get("replaces") or []) if x]
    complements = [str(x) for x in (result.get("complements") or []) if x]
    if replaces or complements:
        lines += ["", "### Отношение к текущему стеку", ""]
        if replaces:
            lines.append("Заменяет: " + ", ".join(replaces))
        if complements:
            lines.append("Дополняет: " + ", ".join(complements))

    cost = []
    if result.get("implementation_complexity"):
        cost.append(f"внедрение {_level(result['implementation_complexity'])}")
    if result.get("maintenance_burden"):
        cost.append(f"поддержка {_level(result['maintenance_burden'])}")
    if result.get("migration_complexity"):
        cost.append(f"миграция {_level(result['migration_complexity'])}")
    if result.get("vendor_lock_in"):
        cost.append(f"vendor lock-in {_LOCK_IN.get(result['vendor_lock_in'], result['vendor_lock_in'])}")
    services = [str(x) for x in (result.get("new_services_required") or []) if x]
    deps = [str(x) for x in (result.get("new_dependencies") or []) if x]
    if cost or services or deps:
        lines += ["", "### Цена внедрения", ""]
        if cost:
            lines.append(" · ".join(cost))
        if services:
            lines.append("Новые сервисы: " + ", ".join(services))
        if deps:
            lines.append("Новые зависимости: " + ", ".join(deps))

    security = [str(x) for x in (result.get("security_risks") or []) if x]
    license_risk = (result.get("license_risks") or "").strip()
    if security or license_risk:
        lines += ["", "### Риски", ""]
        for risk in security:
            lines.append(f"- {risk}")
        if license_risk:
            lines.append(f"- Лицензия: {license_risk}")

    return lines


def _level(value: str) -> str:
    return _LEVELS.get(value, value)


__all__ = ["build_agent_digest"]
