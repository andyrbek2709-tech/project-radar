"""Projects: карточки проектов и автопрофилирование."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.analysis.embeddings import get_embedding_provider
from app.models.analysis import Decision, DecisionStatus
from app.models.finding import FindingProjectMatch
from app.models.project import Project, ProjectFeature, ProjectRepoAudit
from app.schemas import (
    FeatureCreate,
    FeatureOut,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectUpdate,
)
from app.services.profiler import audit_repository, refresh_project_embedding

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: DbSession, _: CurrentUser, include_inactive: bool = False):
    stmt = select(Project).order_by(Project.created_at.asc())
    if not include_inactive:
        stmt = stmt.where(Project.is_active.is_(True))
    return db.execute(stmt).scalars().all()


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: DbSession, _: CurrentUser):
    """Create Project автоматически формирует первичный search profile.

    Если указан github_repository — проводится техревизия (README, зависимости,
    docker-compose, структура) и профиль строится сам.
    """
    exists = db.execute(select(Project).where(Project.slug == payload.slug)).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Проект {payload.slug} уже существует")

    data = payload.model_dump(exclude={"run_audit"})
    project = Project(**data, profile_source="manual")
    db.add(project)
    db.flush()

    if payload.github_repository and payload.run_audit:
        audit_repository(db, project)
    else:
        refresh_project_embedding(db, project)

    db.flush()
    return _detail(db, project)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: uuid.UUID, db: DbSession, _: CurrentUser):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    return _detail(db, project)


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, db: DbSession, _: CurrentUser
):
    """Отредактированные вручную поля блокируются: повторная техревизия
    их больше не затирает."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")

    changes = payload.model_dump(exclude_unset=True)
    locked = set(project.profile_locked_fields or [])

    for field_name, value in changes.items():
        setattr(project, field_name, value)
        if field_name not in {"is_active", "name"}:
            locked.add(field_name)

    project.profile_locked_fields = sorted(locked)
    if project.profile_source == "auto_audit":
        project.profile_source = "hybrid"

    refresh_project_embedding(db, project)
    db.flush()
    return _detail(db, project)


@router.post("/{project_id}/audit", response_model=dict)
def run_audit(project_id: uuid.UUID, db: DbSession, _: CurrentUser):
    """Перезапустить техревизию репозитория."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    if not project.github_repository:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "У проекта не указан github_repository — ревизовать нечего",
        )

    audit = audit_repository(db, project)
    db.flush()
    return {
        "status": audit.status,
        "error": audit.error,
        "commit_sha": audit.commit_sha,
        "files_read": list((audit.files_collected or {}).keys()),
        "features_detected": len(audit.detected_features or []),
        "search_keywords": project.search_keywords,
    }


@router.get("/{project_id}/audits", response_model=list[dict])
def list_audits(project_id: uuid.UUID, db: DbSession, _: CurrentUser):
    rows = db.execute(
        select(ProjectRepoAudit)
        .where(ProjectRepoAudit.project_id == project_id)
        .order_by(ProjectRepoAudit.created_at.desc())
        .limit(20)
    ).scalars().all()
    return [
        {
            "id": str(a.id),
            "repo_full_name": a.repo_full_name,
            "commit_sha": a.commit_sha,
            "status": a.status,
            "error": a.error,
            "llm_model": a.llm_model,
            "files_read": list((a.files_collected or {}).keys()),
            "created_at": a.created_at,
        }
        for a in rows
    ]


@router.get("/{project_id}/features", response_model=list[FeatureOut])
def list_features(project_id: uuid.UUID, db: DbSession, _: CurrentUser):
    return db.execute(
        select(ProjectFeature)
        .where(ProjectFeature.project_id == project_id)
        .order_by(ProjectFeature.created_at.asc())
    ).scalars().all()


@router.post("/{project_id}/features", response_model=FeatureOut, status_code=201)
def add_feature(
    project_id: uuid.UUID, payload: FeatureCreate, db: DbSession, _: CurrentUser
):
    """Ручное добавление «что у нас уже есть» — прямо влияет на то,
    что радар посчитает дублирующим."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")

    feature = ProjectFeature(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        category=payload.category,
        source="manual",
    )
    try:
        feature.embedding = get_embedding_provider().embed(
            [f"{payload.name}. {payload.description or ''}"]
        )[0]
    except Exception:  # noqa: BLE001
        pass

    db.add(feature)
    db.flush()
    return feature


@router.delete("/{project_id}/features/{feature_id}", status_code=204)
def delete_feature(
    project_id: uuid.UUID, feature_id: uuid.UUID, db: DbSession, _: CurrentUser
):
    feature = db.get(ProjectFeature, feature_id)
    if feature is None or feature.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фича не найдена")
    db.delete(feature)


def _detail(db, project: Project) -> ProjectDetail:  # noqa: ANN001
    features = db.execute(
        select(ProjectFeature).where(ProjectFeature.project_id == project.id)
    ).scalars().all()

    findings_count = int(
        db.execute(
            select(func.count())
            .select_from(FindingProjectMatch)
            .where(FindingProjectMatch.project_id == project.id)
        ).scalar_one()
    )

    counts = dict(
        db.execute(
            select(Decision.status, func.count())
            .where(Decision.project_id == project.id, Decision.is_current.is_(True))
            .group_by(Decision.status)
        ).all()
    )

    detail = ProjectDetail.model_validate(project)
    detail.features = [FeatureOut.model_validate(f) for f in features]
    detail.findings_count = findings_count
    detail.critical_count = counts.get(DecisionStatus.CRITICAL, 0)
    detail.recommended_count = counts.get(DecisionStatus.RECOMMENDED, 0)
    return detail
