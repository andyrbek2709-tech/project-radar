"""Проверка триггеров переоценки.

REJECTED не навсегда. Ежедневно сверяем свежий снапшот с baseline: major release,
резкий рост звёзд, смена лицензии, возобновление активности — и находка
возвращается на стол с новым решением.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.analysis import ReassessmentTrigger
from app.models.finding import Finding, FindingStatus
from app.models.repository import Repository
from app.services.decision_engine import check_trigger_fired

log = get_logger("scheduler")


def current_state(repo: Repository | None) -> dict[str, Any]:
    if repo is None:
        return {}
    return {
        "stars": repo.stars,
        "forks": repo.forks,
        "latest_release_tag": repo.latest_release_tag,
        "license_spdx": repo.license_spdx,
        "pushed_at_gh": repo.pushed_at_gh,
        "contributors_count": repo.contributors_count,
    }


def check_all(session: Session) -> dict[str, Any]:
    triggers = session.execute(
        select(ReassessmentTrigger).where(ReassessmentTrigger.status == "armed")
    ).scalars().all()

    fired = 0
    checked = 0

    for trigger in triggers:
        finding = session.get(Finding, trigger.finding_id)
        if finding is None:
            trigger.status = "disarmed"
            continue

        repo = (
            session.get(Repository, finding.repository_id)
            if finding.repository_id
            else None
        )
        state = current_state(repo)
        if not state:
            continue

        checked += 1
        did_fire, evidence = check_trigger_fired(trigger, state)
        if not did_fire:
            continue

        trigger.status = "fired"
        trigger.fired_at = datetime.now(timezone.utc)
        trigger.fired_evidence = _jsonable(evidence)
        fired += 1

        # Возврат на стол: следующий прогон pipeline переоценит находку заново.
        finding.status = FindingStatus.ANALYZED
        finding.pipeline_stage = f"reassessment:{trigger.trigger_type}"

        log.info(
            "reassessment_fired",
            finding_id=str(finding.id),
            trigger=trigger.trigger_type,
            evidence=trigger.fired_evidence,
        )

    log.info("reassessment_checked", armed=len(triggers), checked=checked, fired=fired)
    return {"status": "ok", "armed": len(triggers), "checked": checked, "fired": fired}


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in data.items()
    }


__all__ = ["check_all", "current_state"]
