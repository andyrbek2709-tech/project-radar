"""Celery: приложение и расписание.

Решения, зафиксированные здесь:
  * beat — ОТДЕЛЬНЫЙ сервис, не `worker -B`: два воркера с -B дадут двойное
    расписание, а масштабировать воркеры хочется;
  * расписание хранится в Redis через redbeat, а не в файле: файловая система
    контейнера на Railway эфемерна, при редеплое файловое расписание теряется
    и задачи отрабатывают повторно;
  * Telegram здесь ОТСУТСТВУЕТ — он живёт отдельным asyncio-процессом.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging("worker")

celery_app = Celery(
    "project_radar",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.jobs"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.RADAR_TIMEZONE,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    task_time_limit=60 * 30,
    task_soft_time_limit=60 * 25,
    result_expires=60 * 60 * 24,
    broker_connection_retry_on_startup=True,
    # redbeat: расписание в Redis, переживает редеплой; встроенный lock не даёт
    # двум beat-процессам запустить одну задачу дважды.
    redbeat_redis_url=settings.REDIS_URL,
    redbeat_lock_timeout=120,
    beat_max_loop_interval=30,
)

_radar_hour, _radar_minute = settings.radar_hour_minute


def _every(minutes: int) -> crontab:
    """crontab для интервала в минутах.

    `crontab(minute="*/120")` невалиден — поле minute это 0..59. Интервалы
    длиннее часа приходится выражать через часы, иначе beat падает на старте.
    """
    minutes = max(1, minutes)
    if minutes < 60:
        return crontab(minute=f"*/{minutes}")
    hours = max(1, round(minutes / 60))
    if hours >= 24:
        return crontab(hour=0, minute=0)
    return crontab(hour=f"*/{hours}", minute=0)


celery_app.conf.beat_schedule = {
    # Сбор GitHub — с уважением к лимиту search 30/мин.
    "collect-github": {
        "task": "radar.collect_github",
        "schedule": _every(settings.GITHUB_SCAN_INTERVAL_MINUTES),
        "options": {"expires": 60 * 60},
    },
    # Основной конвейер: нормализация → dedup → фильтр → Groq → решения.
    "process-pipeline": {
        "task": "radar.process_pipeline",
        "schedule": _every(max(2, settings.PIPELINE_INTERVAL_MINUTES)),
        "options": {"expires": 60 * 20},
    },
    # Снапшоты метрик — основа детекции роста. Один раз в сутки.
    "snapshot-repositories": {
        "task": "radar.snapshot_repositories",
        "schedule": crontab(hour=3, minute=0),
    },
    # Проверка триггеров — после снапшотов, чтобы сравнивать со свежими данными.
    "check-reassessments": {
        "task": "radar.check_reassessments",
        "schedule": crontab(hour=4, minute=0),
    },
    # Глубокий разбор — раз в час, в рамках дневного лимита.
    "run-deep-analyses": {
        "task": "radar.run_deep_analyses",
        "schedule": crontab(minute=15),
    },
    # Возврат отложенного.
    "promote-review-queue": {
        "task": "radar.promote_review_queue",
        "schedule": crontab(hour=8, minute=0),
    },
    # Daily Radar.
    "build-daily-radar": {
        "task": "radar.build_daily_radar",
        "schedule": crontab(hour=_radar_hour, minute=_radar_minute),
    },
    # Гигиена: чистка старого сырья.
    "cleanup-old-raw-items": {
        "task": "radar.cleanup",
        "schedule": crontab(hour=5, minute=30),
    },
}

__all__ = ["celery_app"]
