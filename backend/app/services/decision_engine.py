"""Decision Engine.

Решение принимает КОД, а не модель. LLM даёт материал, движок — вердикт,
который всегда можно объяснить одной строкой.

REJECTED не навсегда: на каждое отклонение ставятся триггеры переоценки.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.analysis import (
    Decision,
    DecisionStatus,
    ReassessmentTrigger,
    ReasonCode,
    ReviewQueueItem,
    TriggerType,
)
from app.models.finding import Finding, FindingProjectMatch
from app.services.scoring import RISKY_LICENSES, ScoreSet

log = get_logger("decisions")


@dataclass(slots=True)
class Verdict:
    status: str
    reason: str
    reason_code: str
    review_in_days: int | None = None
    priority: str = "medium"
    triggers: list[dict[str, Any]] = field(default_factory=list)


def evaluate(
    *,
    scores: ScoreSet,
    repo: dict[str, Any] | None,
    match_context: dict[str, Any],
    project_name: str,
) -> Verdict:
    """Детерминированные правила. Порядок важен: сначала отсечения, потом ранжирование."""
    license_spdx = (repo or {}).get("license_spdx", "") or ""
    similarity = match_context.get("max_similarity_to_features")
    nearest = match_context.get("nearest_feature_name")

    # --- жёсткие отсечения -------------------------------------------------
    if license_spdx.upper() in RISKY_LICENSES:
        return Verdict(
            DecisionStatus.REJECTED,
            f"лицензия {license_spdx} несовместима с закрытым продуктом — "
            f"использование в {project_name} создаёт юридический риск",
            ReasonCode.LICENSE_RISK,
            triggers=[{"type": TriggerType.LICENSE_CHANGE, "condition": {"field": "license_spdx", "op": "changed"}}],
        )

    if scores.risk_score > 0.80:
        return Verdict(
            DecisionStatus.REJECTED,
            f"совокупный риск слишком высок ({scores.risk_score:.2f}): "
            "заброшенность, отсутствие лицензии или зависимость от одного автора",
            ReasonCode.HIGH_RISK,
            triggers=[_activity_trigger(repo)],
        )

    if similarity is not None and similarity >= settings.DUPLICATE_FEATURE_THRESHOLD:
        return Verdict(
            DecisionStatus.REJECTED,
            f"дублирует существующий механизм{f' «{nearest}»' if nearest else ''} "
            f"(похожесть {similarity:.2f}) — в {project_name} эта задача уже решена",
            ReasonCode.DUPLICATES_EXISTING,
            triggers=[_major_release_trigger(repo), _stars_surge_trigger(repo)],
        )

    if scores.relevance_score < settings.MIN_RELEVANCE_SCORE:
        return Verdict(
            DecisionStatus.REJECTED,
            f"релевантность {scores.relevance_score:.2f} ниже порога "
            f"{settings.MIN_RELEVANCE_SCORE:.2f} — к задачам {project_name} отношения не имеет",
            ReasonCode.LOW_RELEVANCE,
        )

    if scores.improvement_score < 0.25:
        return Verdict(
            DecisionStatus.REJECTED,
            f"не даёт заметного преимущества относительно текущего подхода в {project_name}"
            + (f" ({nearest})" if nearest else ""),
            ReasonCode.NO_REAL_ADVANTAGE,
            triggers=[_major_release_trigger(repo), _stars_surge_trigger(repo)],
        )

    if scores.activity_score < 0.15:
        pushed = (repo or {}).get("pushed_at_gh")
        return Verdict(
            DecisionStatus.REJECTED,
            "проект малоактивен: последний commit "
            + (f"{pushed}" if pushed else "слишком давно")
            + " — внедрять заброшенное нельзя",
            ReasonCode.PROJECT_INACTIVE,
            triggers=[_activity_trigger(repo), _major_release_trigger(repo)],
        )

    if scores.implementation_cost_score > 0.75 and scores.improvement_score < 0.6:
        return Verdict(
            DecisionStatus.REJECTED,
            "добавляет инфраструктуру: цена внедрения "
            f"({scores.implementation_cost_score:.2f}) не окупается выигрышем "
            f"({scores.improvement_score:.2f})",
            ReasonCode.ADDS_INFRASTRUCTURE,
            triggers=[_major_release_trigger(repo)],
        )

    # --- ранжирование ------------------------------------------------------
    if scores.radar_score >= settings.CRITICAL_SCORE and scores.confidence_score >= 0.70:
        return Verdict(
            DecisionStatus.CRITICAL,
            f"закрывает актуальную задачу {project_name}: улучшение "
            f"{scores.improvement_score:.2f} при цене внедрения "
            f"{scores.implementation_cost_score:.2f} и риске {scores.risk_score:.2f}",
            ReasonCode.HIGH_VALUE,
        )

    if scores.radar_score >= settings.RECOMMENDED_SCORE:
        return Verdict(
            DecisionStatus.RECOMMENDED,
            f"заметное улучшение для {project_name} (radar {scores.radar_score:.2f}), "
            "цена внедрения приемлемая",
            ReasonCode.HIGH_VALUE,
        )

    if scores.radar_score >= settings.REVIEW_LATER_SCORE:
        # Молодое и растущее стоит посмотреть раньше, чем зрелое и посредственное.
        young = scores.maturity_score < 0.5
        return Verdict(
            DecisionStatus.REVIEW_LATER,
            f"потенциал есть (radar {scores.radar_score:.2f}), но "
            + ("проект ещё незрелый — вернуться, когда появятся релизы"
               if young else "сейчас не приоритет для " + project_name),
            ReasonCode.WORTH_REVIEW,
            review_in_days=7 if young else 30,
            priority="high" if scores.radar_score >= 0.55 else "medium",
            triggers=[_major_release_trigger(repo)],
        )

    return Verdict(
        DecisionStatus.ARCHIVED,
        f"итоговая оценка {scores.radar_score:.2f} ниже порога внимания "
        f"{settings.REVIEW_LATER_SCORE:.2f}",
        ReasonCode.LOW_SCORE,
    )


# ------------------------------------------------------------------ triggers


def _major_release_trigger(repo: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "type": TriggerType.MAJOR_RELEASE,
        "condition": {"field": "latest_release_tag", "op": "major_bump"},
        "baseline": {"latest_release_tag": (repo or {}).get("latest_release_tag")},
    }


def _stars_surge_trigger(repo: dict[str, Any] | None) -> dict[str, Any]:
    stars = int((repo or {}).get("stars") or 0)
    return {
        "type": TriggerType.STARS_SURGE,
        "condition": {"field": "stars", "op": ">=", "value": max(100, stars * 2)},
        "baseline": {"stars": stars},
    }


def _activity_trigger(repo: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "type": TriggerType.ACTIVITY_RESUMED,
        "condition": {"field": "pushed_at_gh", "op": "newer_than_baseline"},
        "baseline": {"pushed_at_gh": _iso((repo or {}).get("pushed_at_gh"))},
    }


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) else None


# ------------------------------------------------------------------- persist


def apply_verdict(
    session: Session,
    *,
    finding: Finding,
    match: FindingProjectMatch,
    verdict: Verdict,
    scores: ScoreSet,
    decided_by: str = "engine",
) -> Decision:
    """Записать решение, погасив предыдущее, и разложить побочные эффекты."""
    session.execute(
        update(Decision)
        .where(
            Decision.finding_id == finding.id,
            Decision.project_id == match.project_id,
            Decision.is_current.is_(True),
        )
        .values(is_current=False)
    )

    decision = Decision(
        finding_id=finding.id,
        project_id=match.project_id,
        status=verdict.status,
        reason=verdict.reason,
        reason_code=verdict.reason_code,
        decided_by=decided_by,
        radar_score_at_decision=scores.radar_score,
        score_snapshot=scores.as_dict(),
        is_current=True,
    )
    session.add(decision)
    session.flush()

    if verdict.status == DecisionStatus.REVIEW_LATER and verdict.review_in_days:
        _enqueue_review(session, finding, match, verdict)

    for trigger in verdict.triggers:
        if trigger:
            _arm_trigger(session, finding, match.project_id, trigger)

    log.info(
        "decision",
        finding_id=str(finding.id),
        project_id=str(match.project_id),
        status=verdict.status,
        reason_code=verdict.reason_code,
        radar_score=round(scores.radar_score, 4),
    )
    return decision


def _enqueue_review(
    session: Session,
    finding: Finding,
    match: FindingProjectMatch,
    verdict: Verdict,
) -> None:
    existing = session.execute(
        select(ReviewQueueItem).where(
            ReviewQueueItem.finding_id == finding.id,
            ReviewQueueItem.project_id == match.project_id,
            ReviewQueueItem.resolved_at.is_(None),
        )
    ).scalar_one_or_none()

    due = date.today() + timedelta(days=verdict.review_in_days or 30)
    if existing is not None:
        existing.review_at = due
        existing.priority = verdict.priority
        existing.notes = verdict.reason
        return

    session.add(
        ReviewQueueItem(
            finding_id=finding.id,
            project_id=match.project_id,
            review_at=due,
            priority=verdict.priority,
            notes=verdict.reason,
        )
    )


def _arm_trigger(
    session: Session,
    finding: Finding,
    project_id: uuid.UUID | None,
    spec: dict[str, Any],
) -> None:
    existing = session.execute(
        select(ReassessmentTrigger).where(
            ReassessmentTrigger.finding_id == finding.id,
            ReassessmentTrigger.project_id == project_id,
            ReassessmentTrigger.trigger_type == spec["type"],
            ReassessmentTrigger.status == "armed",
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.baseline = spec.get("baseline") or {}
        existing.condition = spec.get("condition") or {}
        return

    session.add(
        ReassessmentTrigger(
            finding_id=finding.id,
            project_id=project_id,
            trigger_type=spec["type"],
            condition=spec.get("condition") or {},
            baseline=spec.get("baseline") or {},
            status="armed",
        )
    )


def check_trigger_fired(
    trigger: ReassessmentTrigger,
    current: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Сравнить свежий снапшот с baseline. Возвращает (сработал, чем именно)."""
    cond = trigger.condition or {}
    baseline = trigger.baseline or {}
    field_name = cond.get("field")
    op = cond.get("op")
    now_value = current.get(field_name)

    if now_value is None:
        return False, {}

    if op == ">=":
        threshold = cond.get("value")
        if threshold is not None and float(now_value) >= float(threshold):
            return True, {"field": field_name, "was": baseline.get(field_name), "now": now_value}

    elif op == "changed":
        if baseline.get(field_name) != now_value:
            return True, {"field": field_name, "was": baseline.get(field_name), "now": now_value}

    elif op == "major_bump":
        if _is_major_bump(baseline.get(field_name), now_value):
            return True, {"field": field_name, "was": baseline.get(field_name), "now": now_value}

    elif op == "newer_than_baseline":
        was = baseline.get(field_name)
        if was is None:
            return False, {}
        try:
            was_dt = datetime.fromisoformat(str(was).replace("Z", "+00:00"))
            now_dt = (
                now_value
                if isinstance(now_value, datetime)
                else datetime.fromisoformat(str(now_value).replace("Z", "+00:00"))
            )
            if was_dt.tzinfo is None:
                was_dt = was_dt.replace(tzinfo=timezone.utc)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            # Возобновление активности — это заметный скачок, а не любой коммит.
            if now_dt - was_dt > timedelta(days=30):
                return True, {"field": field_name, "was": was, "now": now_dt.isoformat()}
        except (ValueError, TypeError):
            return False, {}

    return False, {}


def _is_major_bump(old_tag: Any, new_tag: Any) -> bool:
    """v1.4.2 → v2.0.0 считается, v1.4.2 → v1.5.0 нет."""
    if not new_tag:
        return False
    if not old_tag:
        return True

    def major(tag: str) -> int | None:
        cleaned = str(tag).lstrip("vV").split("-")[0]
        head = cleaned.split(".")[0]
        return int(head) if head.isdigit() else None

    a, b = major(str(old_tag)), major(str(new_tag))
    return a is not None and b is not None and b > a


__all__ = [
    "Verdict",
    "evaluate",
    "apply_verdict",
    "check_trigger_fired",
]
