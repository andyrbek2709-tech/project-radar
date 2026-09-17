"""Celery-таски. Тонкие обёртки: вся логика — в services/ и collectors/."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.services import deep_analysis, radar_report, reassessment
from app.services.pipeline import reset_errors, run_pipeline
from app.tasks.celery_app import celery_app

log = get_logger("scheduler")


@celery_app.task(name="radar.collect_github", bind=True, max_retries=2)
def collect_github(self, **_: Any) -> dict[str, Any]:  # noqa: ANN001
    from app.collectors.github_radar import run_github_radar

    with session_scope() as session:
        try:
            return run_github_radar(session)
        except Exception as exc:  # noqa: BLE001
            log.warning("collect_github_failed", error=str(exc)[:300])
            raise self.retry(exc=exc, countdown=300) from exc


@celery_app.task(name="radar.snapshot_repositories")
def snapshot_repositories(**_: Any) -> dict[str, Any]:
    """Ежедневный добор метрик через GraphQL-батчи → repository_snapshots."""
    from app.collectors.github_radar import refresh_tracked_repositories

    with session_scope() as session:
        return refresh_tracked_repositories(session, limit=400)


@celery_app.task(name="radar.process_pipeline")
def process_pipeline(**_: Any) -> dict[str, Any]:
    with session_scope() as session:
        return run_pipeline(session)


@celery_app.task(name="radar.retry_errors")
def retry_errors(**_: Any) -> dict[str, Any]:
    """Вернуть сырьё, застрявшее в ERROR, обратно в очередь на разбор."""
    with session_scope() as session:
        restored = reset_errors(session)
        return {"status": "ok", "restored": restored}


@celery_app.task(name="radar.run_deep_analyses")
def run_deep_analyses(**_: Any) -> dict[str, Any]:
    with session_scope() as session:
        return deep_analysis.run_batch(session)


@celery_app.task(name="radar.check_reassessments")
def check_reassessments(**_: Any) -> dict[str, Any]:
    with session_scope() as session:
        return reassessment.check_all(session)


@celery_app.task(name="radar.promote_review_queue")
def promote_review_queue(**_: Any) -> dict[str, Any]:
    with session_scope() as session:
        return radar_report.promote_review_queue(session)


@celery_app.task(name="radar.build_daily_radar")
def build_daily_radar(force: bool = False, **_: Any) -> dict[str, Any]:
    if not settings.RADAR_ENABLED:
        return {"status": "skipped", "reason": "RADAR_ENABLED=false"}

    with session_scope() as session:
        report = radar_report.build_daily_report(session, force=force)
        body = report.body_markdown
        report_id = str(report.id)
        is_empty = report.is_empty
        delivered = dict(report.delivered_to or {})

    if settings.TELEGRAM_DELIVER_REPORT:
        ok = _deliver_telegram(body)
        delivered["telegram"] = ok
        with session_scope() as session:
            from app.models.analysis import DailyReport

            # report_id здесь строка — колонка UUID(as_uuid=True) требует UUID.
            fresh = session.get(DailyReport, uuid.UUID(report_id))
            if fresh is not None:
                fresh.delivered_to = delivered

    return {"status": "ok", "report_id": report_id, "is_empty": is_empty, "delivered": delivered}


def _deliver_telegram(body: str) -> bool:
    """Доставка в «Избранное» той же user-session — отдельный бот не нужен."""
    try:
        from app.collectors.telegram_collector import send_to_saved_messages

        return asyncio.run(send_to_saved_messages(body))
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram_report_delivery_failed", error=str(exc)[:200])
        return False


@celery_app.task(name="radar.cleanup")
def cleanup(days: int = 90, **_: Any) -> dict[str, Any]:
    """Сырьё, которое уже обработано или отфильтровано, дольше N дней не нужно:
    находки и решения хранятся отдельно и не удаляются."""
    from app.models.source import ProcessingStatus, RawItem

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as session:
        result = session.execute(
            delete(RawItem).where(
                RawItem.collected_at < cutoff,
                RawItem.processing_status.in_(
                    [ProcessingStatus.FILTERED_OUT, ProcessingStatus.PROCESSED]
                ),
            )
        )
        deleted = result.rowcount or 0

    log.info("cleanup_done", deleted=deleted, older_than_days=days)
    return {"status": "ok", "deleted": deleted}


@celery_app.task(name="radar.audit_project")
def audit_project(project_id: str, **_: Any) -> dict[str, Any]:
    """Техревизия репозитория проекта — запускается из API при создании проекта."""
    from app.models.project import Project
    from app.services.profiler import audit_repository

    with session_scope() as session:
        project = session.get(Project, uuid.UUID(str(project_id)))
        if project is None:
            return {"status": "error", "reason": "project not found"}
        audit = audit_repository(session, project)
        return {
            "status": audit.status,
            "error": audit.error,
            "features_detected": len(audit.detected_features or []),
            "keywords": len(project.search_keywords or []),
        }


@celery_app.task(name="radar.reembed_projects")
def reembed_projects(**_: Any) -> dict[str, Any]:
    """Пересчитать эмбеддинги профилей — после смены провайдера/модели."""
    from app.models.project import Project
    from app.services.profiler import refresh_project_embedding

    with session_scope() as session:
        projects = session.execute(select(Project)).scalars().all()
        for project in projects:
            refresh_project_embedding(session, project)
        return {"status": "ok", "projects": len(projects)}


__all__ = [
    "collect_github",
    "snapshot_repositories",
    "process_pipeline",
    "retry_errors",
    "run_deep_analyses",
    "check_reassessments",
    "promote_review_queue",
    "build_daily_radar",
    "cleanup",
    "audit_project",
    "reembed_projects",
]
