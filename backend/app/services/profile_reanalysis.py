"""Возврат находок на переоценку после правки профиля проекта.

Профиль проекта — это вход промпта: `build_project_match_prompt` получает
`project_to_dict(project)`, а `input_digest` считается от готового текста
промпта. Значит кэш `FindingAnalysis` промахивается сам, как только профиль
изменился, и повторный разбор действительно уходит в модель, а не возвращает
старый ответ. Эта часть работала всегда.

Не хватало другого: находка до переоценки просто не доходила.
`reanalyse_pending` выбирает `status == ANALYZED`, а разобранная находка лежит
в `DECIDED`, и вернуть её оттуда умел только reassessment-триггер — а он следит
за репозиторием самой находки (релиз, всплеск звёзд, смена лицензии), не за
профилем проекта. Поэтому после починки профиля enghub 14.09.2026 выгрузка от
16.09 по-прежнему описывала Express-проект как «Python, FastAPI, PostgreSQL» и
советовала pydantic, который уже стоит в трёх его сервисах: решения были
приняты по прежнему профилю и пересматривать их было некому.

Решения, принятые человеком (`decisions.decided_by = 'user'`), не трогаются:
переоценке подлежит то, что посчитал движок.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.analysis import Decision
from app.models.finding import Finding, FindingProjectMatch, FindingStatus

log = get_logger("pipeline")

# Поля профиля, которые реально уходят в промпт оценки. Список обязан совпадать
# с ключами `pipeline.project_to_dict` (кроме неизменяемого `id`) — за этим
# следит тест: добавленное в промпт поле должно возвращать находки на
# переоценку, иначе правка профиля снова окажется незамеченной.
#
# `search_keywords`, `negative_keywords` и `integrations` сюда НЕ входят
# сознательно: они влияют на сбор и дешёвый фильтр, но не на текст промпта.
# Их правка не меняет `input_digest`, повторный разбор вернулся бы из кэша, и
# переоценка вышла бы холостой.
PROFILE_PROMPT_FIELDS = frozenset({
    "slug",
    "name",
    "description",
    "business_purpose",
    "existing_architecture",
    "current_stack",
    "current_problems",
    "planned_features",
    "priority_areas",
    "things_not_needed",
    "technology_interests",
})

# Предохранитель. Переоценка платная: каждая находка — вызов модели. Пачками по
# `reanalyse_pending(limit=30)` это растянется на несколько прогонов, но объём
# всё равно стоит видеть в логе, а не узнавать из счёта.
MAX_REQUEUED_PER_PROJECT = 500

# `findings.pipeline_stage` — String(40); длинный slug обрезаем, чтобы правка
# профиля не роняла сохранение на переполнении колонки.
_STAGE_LIMIT = 40


def profile_changes_need_reanalysis(changed_fields: Iterable[str]) -> bool:
    """Есть ли среди изменённых полей хоть одно, влияющее на промпт оценки."""
    return bool(PROFILE_PROMPT_FIELDS.intersection(changed_fields))


def requeue_project_findings(session: Session, project_id, *, trigger: str) -> int:
    """Вернуть решённые находки проекта на переоценку. Возвращает их число.

    Статус переводится в `ANALYZED` — оттуда их заберёт `reanalyse_pending`
    на ближайшем прогоне pipeline. Сами решения не удаляются: движок запишет
    новое поверх, и история остаётся в `decisions`.

    Дату прошлого разбора здесь не смотрим — в отличие от разовой миграции
    0004, которая лечит уже накопленное и потому обязана отличать устаревший
    разбор от свежего. Сюда мы попадаем в момент самой правки: к этой секунде
    любой разбор сделан по прежнему профилю. Лишних вызовов модели это не даёт
    — находка, чей `input_digest` не изменился, вернётся из кэша `finding_analyses`.
    """
    manual = select(Decision.finding_id).where(
        Decision.is_current.is_(True),
        Decision.decided_by == "user",
    )
    matched = select(FindingProjectMatch.finding_id).where(
        FindingProjectMatch.project_id == project_id
    )

    finding_ids = session.execute(
        select(Finding.id)
        .where(
            Finding.status == FindingStatus.DECIDED,
            Finding.id.in_(matched),
            Finding.id.not_in(manual),
        )
        .order_by(Finding.updated_at.desc())
        .limit(MAX_REQUEUED_PER_PROJECT)
    ).scalars().all()

    if not finding_ids:
        log.info("profile_reanalysis_noop", project_id=str(project_id), trigger=trigger)
        return 0

    stage = f"profile_changed:{trigger}"[:_STAGE_LIMIT]
    session.execute(
        update(Finding)
        .where(Finding.id.in_(finding_ids))
        .values(status=FindingStatus.ANALYZED, pipeline_stage=stage)
    )

    log.info(
        "profile_reanalysis_requeued",
        project_id=str(project_id),
        trigger=trigger,
        requeued=len(finding_ids),
        capped=len(finding_ids) >= MAX_REQUEUED_PER_PROJECT,
    )
    return len(finding_ids)


__all__ = [
    "PROFILE_PROMPT_FIELDS",
    "MAX_REQUEUED_PER_PROJECT",
    "profile_changes_need_reanalysis",
    "requeue_project_findings",
]
