"""Учёт стоимости. Каждый внешний вызов оставляет след — иначе счёт не контролируется."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis import AiUsageEvent, AnalysisType, FindingAnalysis


def record(
    session: Session,
    *,
    provider: str,
    operation: str,
    model: str | None = None,
    requests: int = 1,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    finding_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    session.add(
        AiUsageEvent(
            provider=provider,
            operation=operation,
            model=model,
            requests=requests,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=Decimal(str(round(cost_usd, 6))),
            finding_id=finding_id,
            meta=meta or {},
        )
    )


def deep_analyses_today(session: Session) -> int:
    """Лимит MAX_DEEP_ANALYSES_PER_DAY считается по календарным суткам
    в таймзоне радара, а не по UTC."""
    now_local = datetime.now(settings.tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc)

    return int(
        session.execute(
            select(func.count())
            .select_from(FindingAnalysis)
            .where(
                FindingAnalysis.analysis_type == AnalysisType.DEEP_ANALYSIS,
                FindingAnalysis.created_at >= start_utc,
                FindingAnalysis.status == "ok",
            )
        ).scalar_one()
    )


def deep_budget_left(session: Session) -> int:
    return max(0, settings.MAX_DEEP_ANALYSES_PER_DAY - deep_analyses_today(session))


def summary(session: Session, *, since: datetime | None = None) -> dict[str, Any]:
    """Сводка по провайдерам за период."""
    stmt = select(
        AiUsageEvent.provider,
        func.sum(AiUsageEvent.requests),
        func.sum(AiUsageEvent.prompt_tokens),
        func.sum(AiUsageEvent.completion_tokens),
        func.sum(AiUsageEvent.cost_usd),
    ).group_by(AiUsageEvent.provider)
    if since is not None:
        stmt = stmt.where(AiUsageEvent.occurred_at >= since)

    providers: dict[str, dict[str, Any]] = {}
    total_cost = 0.0
    for provider, requests, p_tok, c_tok, cost in session.execute(stmt):
        cost_f = float(cost or 0)
        total_cost += cost_f
        providers[provider] = {
            "requests": int(requests or 0),
            "prompt_tokens": int(p_tok or 0),
            "completion_tokens": int(c_tok or 0),
            "cost_usd": round(cost_f, 6),
        }

    return {"providers": providers, "total_cost_usd": round(total_cost, 6)}


def cost_between(session: Session, start: datetime, end: datetime) -> float:
    value = session.execute(
        select(func.coalesce(func.sum(AiUsageEvent.cost_usd), 0)).where(
            AiUsageEvent.occurred_at >= start, AiUsageEvent.occurred_at < end
        )
    ).scalar_one()
    return float(value or 0)


def dashboard(session: Session) -> dict[str, Any]:
    """Что показывает раздел статистики: сегодня, месяц, за всё время."""
    now = datetime.now(timezone.utc)
    local_now = datetime.now(settings.tz)

    today_start = datetime.combine(
        local_now.date(), datetime.min.time(), tzinfo=settings.tz
    ).astimezone(timezone.utc)

    # Месяц считаем в ЛОКАЛЬНОЙ таймзоне и только потом переводим в UTC.
    # `today_start.replace(day=1)` дало бы 1-е число 19:00 UTC (для Asia/Aqtau)
    # и потеряло бы первый день месяца.
    month_start = datetime.combine(
        local_now.date().replace(day=1), datetime.min.time(), tzinfo=settings.tz
    ).astimezone(timezone.utc)

    daily = summary(session, since=today_start)
    monthly = summary(session, since=month_start)
    total = summary(session)

    # Прогноз месяца по среднедневному темпу — грубо, но достаточно для тревоги.
    day_of_month = max(1, local_now.day)
    projected = monthly["total_cost_usd"] / day_of_month * 30

    return {
        "today": daily,
        "month": monthly,
        "all_time": total,
        "projected_month_usd": round(projected, 4),
        "deep_analyses_today": deep_analyses_today(session),
        "deep_analyses_limit": settings.MAX_DEEP_ANALYSES_PER_DAY,
        "window": {
            "today_start": today_start.isoformat(),
            "month_start": month_start.isoformat(),
            "now": now.isoformat(),
        },
    }


def last_7_days(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            func.date_trunc("day", AiUsageEvent.occurred_at).label("day"),
            func.sum(AiUsageEvent.cost_usd),
            func.sum(AiUsageEvent.requests),
        )
        .where(AiUsageEvent.occurred_at >= datetime.now(timezone.utc) - timedelta(days=7))
        .group_by("day")
        .order_by("day")
    ).all()
    return [
        {
            "date": (d.date() if isinstance(d, datetime) else d).isoformat(),
            "cost_usd": round(float(c or 0), 6),
            "requests": int(r or 0),
        }
        for d, c, r in rows
    ]


__all__ = [
    "record",
    "summary",
    "dashboard",
    "deep_analyses_today",
    "deep_budget_left",
    "cost_between",
    "last_7_days",
]
