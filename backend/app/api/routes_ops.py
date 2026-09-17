"""Sources, Reports, Stats, Settings и ручные триггеры."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.models.analysis import (
    DailyReport,
    Decision,
    DecisionStatus,
    ReviewQueueItem,
    Setting,
)
from app.models.finding import Finding, FindingProjectMatch
from app.models.project import Project
from app.models.source import CollectorRun, ProcessingStatus, RawItem, Source, SourceCursor
from app.schemas import (
    DashboardOut,
    ReportOut,
    SettingIn,
    SettingOut,
    SourceCreate,
    SourceOut,
    SourceToggle,
)
from app.services import usage

router = APIRouter(tags=["ops"])


# ---------------------------------------------------------------- sources


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: DbSession, _: CurrentUser, kind: str | None = None):
    stmt = select(Source).order_by(Source.kind, Source.title)
    if kind:
        stmt = stmt.where(Source.kind == kind)
    sources = db.execute(stmt).scalars().all()

    counts = dict(
        db.execute(
            select(RawItem.source_id, func.count()).group_by(RawItem.source_id)
        ).all()
    )

    out: list[SourceOut] = []
    for source in sources:
        item = SourceOut.model_validate(source)
        item.last_item_id = source.cursor.last_item_id if source.cursor else None
        item.items_collected = counts.get(source.id, 0)
        out.append(item)
    return out


@router.post("/sources", response_model=SourceOut, status_code=201)
def create_source(payload: SourceCreate, db: DbSession, _: CurrentUser):
    exists = db.execute(
        select(Source).where(
            Source.kind == payload.kind, Source.external_id == payload.external_id
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Такой источник уже есть")

    source = Source(
        kind=payload.kind,
        external_id=payload.external_id,
        title=payload.title or payload.external_id,
        config=payload.config,
        is_active=True,
    )
    db.add(source)
    db.flush()
    return SourceOut.model_validate(source)


@router.patch("/sources/{source_id}", response_model=SourceOut)
def toggle_source(
    source_id: uuid.UUID, payload: SourceToggle, db: DbSession, _: CurrentUser
):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Источник не найден")
    source.is_active = payload.is_active
    if payload.is_active:
        # Ручное включение снимает паузу после FloodWait.
        source.paused_until = None
        source.last_error = None
    db.flush()
    return SourceOut.model_validate(source)


@router.post("/sources/{source_id}/reset-cursor", response_model=dict)
def reset_cursor(source_id: uuid.UUID, db: DbSession, _: CurrentUser):
    """Сбросить курсор — источник будет перечитан с начала доступной истории."""
    cursor = db.get(SourceCursor, source_id)
    if cursor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курсор не найден")
    previous = cursor.last_item_id
    cursor.last_item_id = None
    cursor.etag = None
    db.flush()
    return {"status": "ok", "previous_cursor": previous}


# ---------------------------------------------------------------- reports


@router.get("/reports", response_model=list[ReportOut])
def list_reports(db: DbSession, _: CurrentUser, limit: int = Query(default=30, ge=1, le=180)):
    return db.execute(
        select(DailyReport).order_by(DailyReport.report_date.desc()).limit(limit)
    ).scalars().all()


@router.get("/reports/latest", response_model=ReportOut)
def latest_report(db: DbSession, _: CurrentUser):
    report = db.execute(
        select(DailyReport).order_by(DailyReport.report_date.desc()).limit(1)
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Отчётов пока нет")
    return report


@router.get("/reports/{report_date}", response_model=ReportOut)
def get_report(report_date: date, db: DbSession, _: CurrentUser):
    report = db.execute(
        select(DailyReport).where(DailyReport.report_date == report_date)
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Отчёт за эту дату не найден")
    return report


# ------------------------------------------------------------------ stats


@router.get("/stats/dashboard", response_model=DashboardOut)
def dashboard(db: DbSession, _: CurrentUser):
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)

    findings_total = int(db.execute(select(func.count()).select_from(Finding)).scalar_one())
    findings_24h = int(
        db.execute(
            select(func.count()).select_from(Finding).where(Finding.first_seen_at >= day_ago)
        ).scalar_one()
    )

    by_status = dict(
        db.execute(
            select(Decision.status, func.count())
            .where(Decision.is_current.is_(True))
            .group_by(Decision.status)
        ).all()
    )

    by_project = [
        {
            "slug": slug,
            "name": name,
            "total": int(total),
            "avg_score": round(float(avg or 0), 3),
        }
        for slug, name, total, avg in db.execute(
            select(
                Project.slug,
                Project.name,
                func.count(FindingProjectMatch.id),
                func.avg(FindingProjectMatch.radar_score),
            )
            .join(FindingProjectMatch, FindingProjectMatch.project_id == Project.id, isouter=True)
            .group_by(Project.slug, Project.name)
            .order_by(func.count(FindingProjectMatch.id).desc())
        ).all()
    ]

    pending = int(
        db.execute(
            select(func.count())
            .select_from(RawItem)
            .where(RawItem.processing_status == ProcessingStatus.PENDING)
        ).scalar_one()
    )
    due = int(
        db.execute(
            select(func.count())
            .select_from(ReviewQueueItem)
            .where(
                ReviewQueueItem.resolved_at.is_(None),
                ReviewQueueItem.review_at <= date.today(),
            )
        ).scalar_one()
    )

    sources_by_kind = {
        kind: int(count)
        for kind, count in db.execute(
            select(Source.kind, func.count())
            .where(Source.is_active.is_(True))
            .group_by(Source.kind)
        ).all()
    }

    last = db.execute(
        select(DailyReport).order_by(DailyReport.report_date.desc()).limit(1)
    ).scalar_one_or_none()

    collectors = [
        {
            "collector": run.collector,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "items_fetched": run.items_fetched,
            "items_new": run.items_new,
            "api_requests": run.api_requests,
            "rate_limit_remaining": run.rate_limit_remaining,
            "error": run.error,
        }
        for run in db.execute(
            select(CollectorRun).order_by(CollectorRun.started_at.desc()).limit(10)
        ).scalars().all()
    ]

    return DashboardOut(
        findings_total=findings_total,
        findings_24h=findings_24h,
        by_status={s: int(c) for s, c in by_status.items()},
        by_project=by_project,
        pending_raw_items=pending,
        review_queue_due=due,
        sources=sources_by_kind,
        cost=usage.dashboard(db),
        last_report=(
            {
                "date": last.report_date.isoformat(),
                "is_empty": last.is_empty,
                "critical": last.critical_count,
                "recommended": last.recommended_count,
                "review_later": last.review_later_count,
            }
            if last
            else None
        ),
        collectors=collectors,
    )


@router.get("/stats/cost", response_model=dict)
def cost_stats(db: DbSession, _: CurrentUser):
    return {**usage.dashboard(db), "daily": usage.last_7_days(db)}


# --------------------------------------------------------------- settings


@router.get("/settings", response_model=list[SettingOut])
def list_settings(db: DbSession, _: CurrentUser):
    rows = db.execute(select(Setting).order_by(Setting.key)).scalars().all()
    return [SettingOut(key=r.key, value=r.value, updated_at=r.updated_at) for r in rows]


@router.get("/settings/runtime", response_model=dict)
def runtime_settings(_: CurrentUser):
    """Действующая конфигурация. Секретов здесь нет и быть не может."""
    return {
        "app_env": settings.APP_ENV,
        "timezone": settings.RADAR_TIMEZONE,
        "radar_enabled": settings.RADAR_ENABLED,
        "radar_daily_time": settings.RADAR_DAILY_TIME,
        "telegram_enabled": settings.TELEGRAM_ENABLED,
        "telegram_configured": bool(settings.TELEGRAM_SESSION_STRING),
        "telegram_scan_interval_minutes": settings.TELEGRAM_SCAN_INTERVAL_MINUTES,
        "github_search_enabled": settings.GITHUB_SEARCH_ENABLED,
        "github_configured": bool(settings.GITHUB_TOKEN),
        "github_scan_interval_minutes": settings.GITHUB_SCAN_INTERVAL_MINUTES,
        "groq_enabled": settings.GROQ_ENABLED,
        "groq_configured": bool(settings.GROQ_API_KEY),
        "groq_model": settings.GROQ_MODEL,
        "openai_deep_analysis_enabled": settings.OPENAI_DEEP_ANALYSIS_ENABLED,
        "openai_configured": bool(settings.OPENAI_API_KEY),
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "embedding_model": settings.EMBEDDING_MODEL,
        "embedding_dim": settings.EMBEDDING_DIM,
        "thresholds": {
            "min_relevance": settings.MIN_RELEVANCE_SCORE,
            "min_deep_analysis": settings.MIN_DEEP_ANALYSIS_SCORE,
            "critical": settings.CRITICAL_SCORE,
            "recommended": settings.RECOMMENDED_SCORE,
            "review_later": settings.REVIEW_LATER_SCORE,
            "dedup_cosine": settings.DEDUP_COSINE_THRESHOLD,
            "duplicate_feature": settings.DUPLICATE_FEATURE_THRESHOLD,
        },
        "max_deep_analyses_per_day": settings.MAX_DEEP_ANALYSES_PER_DAY,
    }


@router.put("/settings/{key}", response_model=SettingOut)
def upsert_setting(key: str, payload: SettingIn, db: DbSession, _: CurrentUser):
    """Пороги и веса меняются без редеплоя. Секреты сюда класть нельзя."""
    forbidden = ("key", "token", "secret", "password", "session", "hash")
    if any(marker in key.lower() for marker in forbidden):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Секреты хранятся только в environment variables",
        )

    setting = db.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=payload.value)
        db.add(setting)
    else:
        setting.value = payload.value
    db.flush()
    return SettingOut(key=setting.key, value=setting.value, updated_at=setting.updated_at)


# ---------------------------------------------------------------- triggers


@router.post("/trigger/{job}", response_model=dict)
def trigger_job(job: str, _: CurrentUser, force: bool = False):
    """Ручной запуск задач из UI — задача уходит в очередь Celery."""
    from app.tasks import jobs

    mapping = {
        "collect-github": jobs.collect_github,
        "snapshot-repositories": jobs.snapshot_repositories,
        "process-pipeline": jobs.process_pipeline,
        "retry-errors": jobs.retry_errors,
        "deep-analyses": jobs.run_deep_analyses,
        "check-reassessments": jobs.check_reassessments,
        "promote-review": jobs.promote_review_queue,
        "daily-radar": jobs.build_daily_radar,
        "cleanup": jobs.cleanup,
        "reembed-projects": jobs.reembed_projects,
    }
    task = mapping.get(job)
    if task is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Неизвестная задача. Доступны: {', '.join(sorted(mapping))}",
        )

    kwargs = {"force": force} if job == "daily-radar" else {}
    async_result = task.apply_async(kwargs=kwargs)
    return {"status": "queued", "job": job, "task_id": async_result.id}
