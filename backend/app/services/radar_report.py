"""Daily Radar — ежедневный отчёт.

По умолчанию 21:00, окно — предыдущие 24 часа, таймзона Asia/Aqtau.
Мусор в основной отчёт не помещается. Если за сутки ничего нет — отчёт
честно пишет «Сегодня значимых находок нет», а не выдумывает наполнение.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.analysis import (
    AnalysisType,
    DailyReport,
    Decision,
    DecisionStatus,
    FindingAnalysis,
    ReviewQueueItem,
)
from app.models.finding import Finding, FindingProjectMatch
from app.models.project import Project
from app.models.source import ProcessingStatus, RawItem, Source, SourceKind
from app.services import usage

log = get_logger("scheduler")

MAX_ITEMS_PER_SECTION = 12


def report_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Окно — предыдущие 24 часа относительно момента запуска в таймзоне радара."""
    now = now or datetime.now(settings.tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=settings.tz)
    end = now.astimezone(timezone.utc)
    return end - timedelta(hours=24), end


def collect_stats(session: Session, start: datetime, end: datetime) -> dict[str, int]:
    def count_raw(kind_filter: Any, **extra: Any) -> int:
        stmt = (
            select(func.count())
            .select_from(RawItem)
            .join(Source, Source.id == RawItem.source_id)
            .where(RawItem.collected_at >= start, RawItem.collected_at < end)
        )
        if kind_filter is not None:
            stmt = stmt.where(kind_filter)
        for key, value in extra.items():
            stmt = stmt.where(getattr(RawItem, key) == value)
        return int(session.execute(stmt).scalar_one())

    telegram = count_raw(Source.kind == SourceKind.TELEGRAM)
    github = count_raw(Source.kind.in_([SourceKind.GITHUB_SEARCH, SourceKind.GITHUB_WATCH]))
    filtered_out = count_raw(None, processing_status=ProcessingStatus.FILTERED_OUT)
    processed = count_raw(None, processing_status=ProcessingStatus.PROCESSED)

    classified = int(
        session.execute(
            select(func.count())
            .select_from(FindingAnalysis)
            .where(
                FindingAnalysis.analysis_type == AnalysisType.GROQ_CLASSIFICATION,
                FindingAnalysis.created_at >= start,
                FindingAnalysis.created_at < end,
                FindingAnalysis.status == "ok",
            )
        ).scalar_one()
    )
    deep = int(
        session.execute(
            select(func.count())
            .select_from(FindingAnalysis)
            .where(
                FindingAnalysis.analysis_type.in_(
                    [AnalysisType.DEEP_ANALYSIS, AnalysisType.MANUAL_CHATGPT]
                ),
                FindingAnalysis.created_at >= start,
                FindingAnalysis.created_at < end,
                FindingAnalysis.status == "ok",
            )
        ).scalar_one()
    )
    duplicates = int(
        session.execute(
            select(func.coalesce(func.sum(Finding.seen_count - 1), 0)).where(
                Finding.last_seen_at >= start, Finding.last_seen_at < end
            )
        ).scalar_one()
    )

    return {
        "telegram_messages_processed": telegram,
        "github_candidates": github,
        "after_cheap_filter": max(0, telegram + github - filtered_out),
        "after_ai_filter": classified,
        "deep_analyses_count": deep,
        "duplicates_count": duplicates,
        "processed": processed,
    }


def fetch_section(
    session: Session, status: str, start: datetime, end: datetime, limit: int = MAX_ITEMS_PER_SECTION
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Decision, Finding, FindingProjectMatch, Project)
        .join(Finding, Finding.id == Decision.finding_id)
        .outerjoin(
            FindingProjectMatch,
            (FindingProjectMatch.finding_id == Decision.finding_id)
            & (FindingProjectMatch.project_id == Decision.project_id),
        )
        .outerjoin(Project, Project.id == Decision.project_id)
        .where(
            Decision.status == status,
            Decision.is_current.is_(True),
            Decision.created_at >= start,
            Decision.created_at < end,
        )
        .order_by(Decision.radar_score_at_decision.desc().nullslast())
        .limit(limit)
    ).all()

    out: list[dict[str, Any]] = []
    for decision, finding, match, project in rows:
        repo = finding.repository
        out.append(
            {
                "finding_id": str(finding.id),
                "title": finding.title,
                "url": finding.url,
                "repository": repo.full_name if repo else None,
                "stars": repo.stars if repo else None,
                "stars_delta": None,
                "project": project.name if project else None,
                "project_slug": project.slug if project else None,
                "summary": finding.summary,
                "why_relevant": match.why_relevant if match else None,
                "what_we_have": match.what_we_have if match else None,
                "what_it_offers": match.what_it_offers if match else None,
                "advantages": list(match.advantages or []) if match else [],
                "disadvantages": list(match.disadvantages or []) if match else [],
                "integration_complexity": match.integration_complexity if match else None,
                "radar_score": round(decision.radar_score_at_decision or 0.0, 3),
                "reason": decision.reason,
                "found_via": list(finding.found_via or []),
                "seen_count": finding.seen_count,
            }
        )
    return out


def render_markdown(
    *,
    report_date: date,
    stats: dict[str, int],
    critical: list[dict[str, Any]],
    recommended: list[dict[str, Any]],
    review_later: list[dict[str, Any]],
    rejected_count: int,
    cost_usd: float,
) -> str:
    lines = [
        f"# Project Radar — {report_date.isoformat()}",
        "",
        f"Окно: предыдущие 24 часа · {settings.RADAR_TIMEZONE}",
        "",
        "## Обработано",
        "",
        f"- Telegram-сообщений: **{stats['telegram_messages_processed']}**",
        f"- GitHub-кандидатов: **{stats['github_candidates']}**",
        f"- После дешёвого фильтра: **{stats['after_cheap_filter']}**",
        f"- После AI-анализа: **{stats['after_ai_filter']}**",
        f"- Глубоких разборов: **{stats['deep_analyses_count']}**",
        "",
    ]

    total = len(critical) + len(recommended) + len(review_later)
    if total == 0:
        lines += [
            "## Результат",
            "",
            "**Сегодня значимых находок нет.**",
            "",
            "Это нормальный исход: фильтр отработал и не стал заполнять отчёт шумом.",
            "",
        ]
    else:
        for title, items in (
            ("CRITICAL", critical),
            ("RECOMMENDED", recommended),
            ("REVIEW LATER", review_later),
        ):
            if not items:
                continue
            lines += [f"## {title} ({len(items)})", ""]
            for item in items:
                lines.extend(_render_item(item, short=(title == "REVIEW LATER")))
            lines.append("")

    lines += [
        "---",
        "",
        f"Отклонено: **{rejected_count}** · дубликатов схлопнуто: "
        f"**{stats['duplicates_count']}** · стоимость AI за сутки: **${cost_usd:.4f}**",
        "",
    ]
    return "\n".join(lines)


def _render_item(item: dict[str, Any], *, short: bool = False) -> list[str]:
    head = f"### {item['title']}"
    lines = [head, ""]

    meta: list[str] = []
    if item.get("project"):
        meta.append(f"для **{item['project']}**")
    if item.get("repository"):
        star = f" ★{item['stars']}" if item.get("stars") is not None else ""
        meta.append(f"`{item['repository']}`{star}")
    meta.append(f"radar **{item['radar_score']:.2f}**")
    if item.get("found_via"):
        meta.append("через " + ", ".join(item["found_via"]))
    lines += [" · ".join(meta), ""]

    if item.get("url"):
        lines += [item["url"], ""]

    if short:
        lines += [f"_{item['reason']}_", ""]
        return lines

    if item.get("why_relevant"):
        lines += ["**Почему релевантно.** " + item["why_relevant"], ""]
    if item.get("what_we_have"):
        lines += ["**Что у нас есть.** " + item["what_we_have"], ""]
    if item.get("what_it_offers"):
        lines += ["**Что предлагает.** " + item["what_it_offers"], ""]
    if item.get("advantages"):
        lines += ["**Плюсы:** " + "; ".join(item["advantages"][:4]), ""]
    if item.get("disadvantages"):
        lines += ["**Минусы:** " + "; ".join(item["disadvantages"][:4]), ""]
    if item.get("integration_complexity"):
        lines += [f"**Сложность внедрения:** {item['integration_complexity']}", ""]
    lines += [f"_{item['reason']}_", ""]
    return lines


def build_daily_report(
    session: Session, *, now: datetime | None = None, force: bool = False
) -> DailyReport:
    start, end = report_window(now)
    report_date = (now or datetime.now(settings.tz)).date()

    existing = session.execute(
        select(DailyReport).where(DailyReport.report_date == report_date)
    ).scalar_one_or_none()
    if existing is not None and not force:
        return existing

    stats = collect_stats(session, start, end)
    critical = fetch_section(session, DecisionStatus.CRITICAL, start, end)
    recommended = fetch_section(session, DecisionStatus.RECOMMENDED, start, end)
    review_later = fetch_section(session, DecisionStatus.REVIEW_LATER, start, end)

    rejected_count = int(
        session.execute(
            select(func.count())
            .select_from(Decision)
            .where(
                Decision.status == DecisionStatus.REJECTED,
                Decision.is_current.is_(True),
                Decision.created_at >= start,
                Decision.created_at < end,
            )
        ).scalar_one()
    )
    cost = usage.cost_between(session, start, end)

    body = render_markdown(
        report_date=report_date,
        stats=stats,
        critical=critical,
        recommended=recommended,
        review_later=review_later,
        rejected_count=rejected_count,
        cost_usd=cost,
    )
    is_empty = not (critical or recommended or review_later)

    payload = {
        "critical": critical,
        "recommended": recommended,
        "review_later": review_later,
        "stats": stats,
    }

    values = {
        "report_date": report_date,
        "window_start": start,
        "window_end": end,
        "telegram_messages_processed": stats["telegram_messages_processed"],
        "github_candidates": stats["github_candidates"],
        "after_cheap_filter": stats["after_cheap_filter"],
        "after_ai_filter": stats["after_ai_filter"],
        "deep_analyses_count": stats["deep_analyses_count"],
        "critical_count": len(critical),
        "recommended_count": len(recommended),
        "review_later_count": len(review_later),
        "rejected_count": rejected_count,
        "duplicates_count": stats["duplicates_count"],
        "estimated_cost_usd": Decimal(str(round(cost, 6))),
        "body_markdown": body,
        "payload": payload,
        "is_empty": is_empty,
    }

    stmt = (
        pg_insert(DailyReport)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[DailyReport.report_date],
            set_={k: v for k, v in values.items() if k != "report_date"},
        )
        .returning(DailyReport.id)
    )
    report_id = session.execute(stmt).scalar_one()
    session.flush()
    # Core-upsert прошёл мимо ORM: при force=True объект уже в identity map,
    # и session.get() вернул бы вчерашний body_markdown — в Telegram ушёл бы
    # старый отчёт. Сбрасываем кэш.
    if existing is not None:
        session.expire(existing)

    log.info(
        "daily_report_built",
        date=report_date.isoformat(),
        critical=len(critical),
        recommended=len(recommended),
        review_later=len(review_later),
        rejected=rejected_count,
        empty=is_empty,
        cost_usd=round(cost, 6),
    )
    return session.get(DailyReport, report_id)


def promote_review_queue(session: Session) -> dict[str, Any]:
    """Вернуть на стол то, чей срок REVIEW LATER подошёл."""
    due = session.execute(
        select(ReviewQueueItem).where(
            ReviewQueueItem.review_at <= date.today(),
            ReviewQueueItem.resolved_at.is_(None),
        )
    ).scalars().all()

    from app.models.finding import FindingStatus

    promoted = 0
    for item in due:
        finding = session.get(Finding, item.finding_id)
        if finding is None:
            item.resolved_at = datetime.now(timezone.utc)
            continue
        finding.status = FindingStatus.ANALYZED
        finding.pipeline_stage = "review_due"
        item.resolved_at = datetime.now(timezone.utc)
        promoted += 1

    log.info("review_queue_promoted", count=promoted)
    return {"status": "ok", "promoted": promoted}


__all__ = [
    "build_daily_report",
    "report_window",
    "collect_stats",
    "render_markdown",
    "promote_review_queue",
]
