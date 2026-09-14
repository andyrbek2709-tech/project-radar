"""Radar: находки, карточки, COPY ANALYSIS CONTEXT, ручные решения."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.collectors.github_radar import compute_growth, compute_growth_bulk
from app.models.analysis import (
    AnalysisType,
    Decision,
    DecisionStatus,
    FindingAnalysis,
    ReviewQueueItem,
)
from app.models.finding import Finding, FindingProjectMatch, FindingSource
from app.models.project import Project, ProjectFeature
from app.models.source import RawItem, Source
from app.schemas import (
    AnalysisContextOut,
    DecisionOut,
    DecisionOverride,
    FindingDetail,
    FindingListItem,
    FindingPage,
    ManualAnalysisIn,
    MatchOut,
    RepositoryOut,
    ReviewQueueOut,
)
from app.services.context_prompt import build_analysis_context
from app.services.deep_analysis import store_manual_analysis
from app.services.decision_engine import Verdict, apply_verdict
from app.services.scoring import ScoreSet

router = APIRouter(prefix="/findings", tags=["radar"])


@router.get("", response_model=FindingPage)
def list_findings(
    db: DbSession,
    _: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status"),
    project: str | None = Query(default=None, description="slug проекта"),
    search: str | None = None,
    min_score: float | None = Query(default=None, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Список находок. status: CRITICAL / RECOMMENDED / REVIEW_LATER / ARCHIVED / REJECTED.

    Единица выдачи — пара (находка × проект), а не находка. Это осознанно:
    одна и та же технология для EngHub и для TrackParts — два разных решения
    с разными причинами, и показывать их надо по отдельности. `total`,
    `limit` и `offset` считаются в тех же парах.
    """
    stmt = (
        select(Finding, FindingProjectMatch, Decision, Project)
        .options(selectinload(Finding.repository))
        .outerjoin(FindingProjectMatch, FindingProjectMatch.finding_id == Finding.id)
        .outerjoin(
            Decision,
            (Decision.finding_id == Finding.id)
            & (Decision.project_id == FindingProjectMatch.project_id)
            & (Decision.is_current.is_(True)),
        )
        .outerjoin(Project, Project.id == FindingProjectMatch.project_id)
    )

    if status_filter:
        statuses = [s.strip().upper() for s in status_filter.split(",") if s.strip()]
        invalid = [s for s in statuses if s not in DecisionStatus.ALL]
        if invalid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Неизвестный статус: {invalid}")
        stmt = stmt.where(Decision.status.in_(statuses))
    if project:
        stmt = stmt.where(Project.slug == project)
    if min_score is not None:
        stmt = stmt.where(FindingProjectMatch.radar_score >= min_score)
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(func.lower(Finding.title).like(pattern))

    total = int(
        db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    )

    rows = db.execute(
        stmt.order_by(
            FindingProjectMatch.radar_score.desc().nullslast(),
            Finding.first_seen_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    # Рост звёзд и названия ближайших фич — по одному запросу на страницу,
    # а не по два-три на строку. На полусотне находок это была разница между
    # четырьмя запросами и двумя сотнями.
    growth = compute_growth_bulk(db, [f.repository for f, *_ in rows if f.repository])
    features = _feature_names(db, [m for _, m, _, _ in rows if m is not None])

    items: list[FindingListItem] = []
    for finding, match, decision, proj in rows:
        item = FindingListItem.model_validate(finding)
        if finding.repository is not None:
            item.repository = RepositoryOut.model_validate(finding.repository)
            item.stars_delta = growth.get(finding.repository.id, {}).get("stars_delta")
        if match is not None:
            item.match = _match_out(match, proj, features)
        if decision is not None:
            item.decision = DecisionOut.model_validate(decision)
        items.append(item)

    return FindingPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{finding_id}", response_model=FindingDetail)
def get_finding(finding_id: uuid.UUID, db: DbSession, _: CurrentUser):
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Находка не найдена")

    detail = FindingDetail.model_validate(finding)

    if finding.repository is not None:
        detail.repository = RepositoryOut.model_validate(finding.repository)
        detail.stars_delta = compute_growth(db, finding.repository).get("stars_delta")

    matches = db.execute(
        select(FindingProjectMatch, Project)
        .join(Project, Project.id == FindingProjectMatch.project_id)
        .where(FindingProjectMatch.finding_id == finding_id)
        .order_by(FindingProjectMatch.radar_score.desc())
    ).all()
    features = _feature_names(db, [m for m, _ in matches])
    detail.matches = [_match_out(m, p, features) for m, p in matches]

    detail.decisions = [
        DecisionOut.model_validate(d)
        for d in db.execute(
            select(Decision)
            .where(Decision.finding_id == finding_id)
            .order_by(Decision.created_at.desc())
            .limit(20)
        ).scalars().all()
    ]

    deep = db.execute(
        select(FindingAnalysis)
        .where(
            FindingAnalysis.finding_id == finding_id,
            FindingAnalysis.analysis_type.in_(
                [AnalysisType.DEEP_ANALYSIS, AnalysisType.MANUAL_CHATGPT]
            ),
            FindingAnalysis.status == "ok",
        )
        .order_by(FindingAnalysis.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if deep is not None:
        detail.deep_analysis = {
            "provider": deep.provider,
            "model": deep.model,
            "created_at": deep.created_at.isoformat(),
            **(deep.result or {}),
        }

    sources = db.execute(
        select(FindingSource, Source, RawItem)
        .join(Source, Source.id == FindingSource.source_id, isouter=True)
        .join(RawItem, RawItem.id == FindingSource.raw_item_id, isouter=True)
        .where(FindingSource.finding_id == finding_id)
        .order_by(FindingSource.seen_at.desc())
        .limit(20)
    ).all()
    detail.sources = [
        {
            "kind": src.kind if src else None,
            "title": src.title if src else None,
            "seen_at": fs.seen_at.isoformat(),
            "dedup_method": fs.dedup_method,
            "dedup_score": fs.dedup_score,
            "excerpt": (raw.content_text or "")[:300] if raw else None,
        }
        for fs, src, raw in sources
    ]
    return detail


@router.get("/{finding_id}/analysis-context", response_model=AnalysisContextOut)
def analysis_context(
    finding_id: uuid.UUID,
    db: DbSession,
    _: CurrentUser,
    project_id: uuid.UUID | None = None,
):
    """COPY ANALYSIS CONTEXT — готовый промпт для ChatGPT/Codex одним кликом."""
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Находка не найдена")

    match: FindingProjectMatch | None
    if project_id is not None:
        match = db.execute(
            select(FindingProjectMatch).where(
                FindingProjectMatch.finding_id == finding_id,
                FindingProjectMatch.project_id == project_id,
            )
        ).scalar_one_or_none()
    else:
        match = db.execute(
            select(FindingProjectMatch)
            .where(FindingProjectMatch.finding_id == finding_id)
            .order_by(FindingProjectMatch.radar_score.desc())
            .limit(1)
        ).scalar_one_or_none()

    if match is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Для находки нет оценки относительно проекта — контекст собирать не из чего",
        )

    project = db.get(Project, match.project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")

    prompt = build_analysis_context(db, finding=finding, project=project, match=match)
    return AnalysisContextOut(
        finding_id=finding.id,
        project_id=project.id,
        project_slug=project.slug,
        prompt=prompt,
        char_count=len(prompt),
    )


@router.post("/{finding_id}/manual-analysis", response_model=dict)
def manual_analysis(
    finding_id: uuid.UUID, payload: ManualAnalysisIn, db: DbSession, _: CurrentUser
):
    """Вставить ответ ChatGPT/Codex обратно в систему.

    Разбор остаётся в базе и участвует в решениях, а не теряется в чужом чате.
    """
    try:
        parsed = store_manual_analysis(
            db,
            finding_id=finding_id,
            project_id=payload.project_id,
            payload=payload.analysis,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    db.flush()
    return {"status": "ok", "verdict": parsed.verdict, "reason": parsed.verdict_reason}


@router.post("/{finding_id}/decision", response_model=DecisionOut)
def override_decision(
    finding_id: uuid.UUID, payload: DecisionOverride, db: DbSession, user: CurrentUser
):
    """Ручное решение. Причина обязательна — как и у автоматического."""
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Находка не найдена")

    if payload.project_id is not None:
        match = db.execute(
            select(FindingProjectMatch).where(
                FindingProjectMatch.finding_id == finding_id,
                FindingProjectMatch.project_id == payload.project_id,
            )
        ).scalar_one_or_none()
    else:
        match = db.execute(
            select(FindingProjectMatch)
            .where(FindingProjectMatch.finding_id == finding_id)
            .order_by(FindingProjectMatch.radar_score.desc())
            .limit(1)
        ).scalar_one_or_none()

    if match is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Нет оценки относительно проекта"
        )

    scores = ScoreSet(
        relevance_score=match.relevance_score,
        novelty_score=match.novelty_score,
        project_fit_score=match.project_fit_score,
        improvement_score=match.improvement_score,
        maturity_score=match.maturity_score,
        activity_score=match.activity_score,
        implementation_cost_score=match.implementation_cost_score,
        risk_score=match.risk_score,
        confidence_score=match.confidence_score,
        radar_score=match.radar_score,
    )
    verdict = Verdict(
        status=payload.status,
        reason=payload.reason,
        reason_code="manual_override",
        review_in_days=payload.review_in_days
        or (30 if payload.status == DecisionStatus.REVIEW_LATER else None),
    )
    decision = apply_verdict(
        db, finding=finding, match=match, verdict=verdict, scores=scores, decided_by=f"user:{user}"
    )
    db.flush()
    return DecisionOut.model_validate(decision)


@router.get("/queue/review-later", response_model=list[ReviewQueueOut])
def review_queue(
    db: DbSession,
    _: CurrentUser,
    due_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = (
        select(ReviewQueueItem, Finding.title, Project.slug)
        .join(Finding, Finding.id == ReviewQueueItem.finding_id)
        .outerjoin(Project, Project.id == ReviewQueueItem.project_id)
        .where(ReviewQueueItem.resolved_at.is_(None))
        .order_by(ReviewQueueItem.review_at.asc())
        .limit(limit)
    )
    if due_only:
        stmt = stmt.where(ReviewQueueItem.review_at <= date.today())

    return [
        ReviewQueueOut(
            id=item.id,
            finding_id=item.finding_id,
            finding_title=title,
            project_slug=slug,
            review_at=item.review_at,
            priority=item.priority,
            notes=item.notes,
        )
        for item, title, slug in db.execute(stmt).all()
    ]


def _feature_names(db, matches: list[FindingProjectMatch]) -> dict[uuid.UUID, str]:  # noqa: ANN001
    """Названия ближайших фич одним запросом на всю выдачу."""
    ids = {m.nearest_feature_id for m in matches if m.nearest_feature_id}
    if not ids:
        return {}
    rows = db.execute(
        select(ProjectFeature.id, ProjectFeature.name).where(ProjectFeature.id.in_(ids))
    ).all()
    return {row[0]: row[1] for row in rows}


def _match_out(
    match: FindingProjectMatch,
    project: Project | None,
    features: dict[uuid.UUID, str],
) -> MatchOut:
    out = MatchOut.model_validate(match)
    if project is not None:
        out.project_slug = project.slug
        out.project_name = project.name
    if match.nearest_feature_id:
        out.nearest_feature_name = features.get(match.nearest_feature_id)
    return out
