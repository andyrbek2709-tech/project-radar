from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.models.base import Base, TimestampMixin, uuid_pk

DIM = settings.EMBEDDING_DIM


class Project(Base, TimestampMixin):
    """Project Profile — эталон, относительно которого оценивается всё."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    description: Mapped[str | None] = mapped_column(Text)
    business_purpose: Mapped[str | None] = mapped_column(Text)
    existing_architecture: Mapped[str | None] = mapped_column(Text)

    current_stack: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    integrations: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    current_problems: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    planned_features: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    technology_interests: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    search_keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    negative_keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    things_not_needed: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    priority_areas: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")

    github_repository: Mapped[str | None] = mapped_column(String(255))
    profile_source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    profile_locked_fields: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    profile_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(DIM))

    features: Mapped[list["ProjectFeature"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index(
            "ix_projects_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.slug}>"


class ProjectFeature(Base):
    """«Что у нас уже есть» — построчно, чтобы сравнивать векторно."""

    __tablename__ = "project_features"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(DIM))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="features")

    __table_args__ = (
        Index("ix_project_features_project_id", "project_id"),
        Index(
            "ix_project_features_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ProjectRepoAudit(Base):
    """Лог автопрофилирования: сырьё, чтобы перестроить профиль без похода в GitHub."""

    __tablename__ = "project_repo_audits"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    files_collected: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    detected_stack: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    detected_features: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    llm_model: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_project_repo_audits_project_id", "project_id"),)


__all__ = ["Project", "ProjectFeature", "ProjectRepoAudit"]
