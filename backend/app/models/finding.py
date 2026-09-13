from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.models.base import Base, TimestampMixin, uuid_pk

DIM = settings.EMBEDDING_DIM


class FindingKind:
    REPOSITORY = "repository"
    ARTICLE = "article"
    RELEASE = "release"
    TOOL = "tool"
    DISCUSSION = "discussion"


class FindingStatus:
    NEW = "new"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    DECIDED = "decided"


class KeyType:
    GITHUB_ID = "github_id"
    GITHUB_FULL_NAME = "github_full_name"
    NORMALIZED_URL = "normalized_url"
    URL = "url"
    TITLE_HASH = "title_hash"


class Finding(Base, TimestampMixin):
    """Одна находка = одна строка, сколько бы раз и откуда она ни всплывала."""

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default=FindingKind.REPOSITORY)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1000))
    normalized_url: Mapped[str | None] = mapped_column(String(1000))

    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL")
    )

    summary: Mapped[str | None] = mapped_column(Text)
    content_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(DIM))

    found_via: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FindingStatus.NEW, server_default="new"
    )
    pipeline_stage: Mapped[str | None] = mapped_column(String(40))
    is_noise: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    keys: Mapped[list["FindingKey"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )
    # foreign_keys обязателен: FindingProjectMatch ссылается на findings ДВАЖДЫ
    # (finding_id и nearest_past_finding_id) — без явного указания SQLAlchemy
    # не сможет разрешить связь.
    matches: Mapped[list["FindingProjectMatch"]] = relationship(
        back_populates="finding",
        cascade="all, delete-orphan",
        foreign_keys="FindingProjectMatch.finding_id",
    )
    repository = relationship("Repository", lazy="joined")

    __table_args__ = (
        Index("ix_findings_status_seen", "status", "first_seen_at"),
        Index(
            "uq_findings_repository_id",
            "repository_id",
            unique=True,
            postgresql_where=text("repository_id IS NOT NULL"),
        ),
        Index(
            "ix_findings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Finding {self.title[:40]!r}>"


class FindingKey(Base):
    """Универсальные ключи дедупликации. Новый тип — без миграции схемы."""

    __tablename__ = "finding_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    key_type: Mapped[str] = mapped_column(String(32), nullable=False)
    key_value: Mapped[str] = mapped_column(String(1000), nullable=False)

    finding: Mapped[Finding] = relationship(back_populates="keys")

    __table_args__ = (
        UniqueConstraint("key_type", "key_value", name="uq_finding_keys_type_value"),
        Index("ix_finding_keys_finding_id", "finding_id"),
    )


class FindingSource(Base):
    """m2m находка ↔ сырьё. Повторное появление повышает confidence."""

    __tablename__ = "finding_sources"

    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True
    )
    raw_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("raw_items.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL")
    )
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    dedup_method: Mapped[str | None] = mapped_column(String(32))
    dedup_score: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (Index("ix_finding_sources_finding", "finding_id"),)


class FindingProjectMatch(Base, TimestampMixin):
    """Ядро полезности: оценка всегда относительно конкретного проекта."""

    __tablename__ = "finding_project_matches"

    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    novelty_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    project_fit_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    improvement_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    maturity_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    activity_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    implementation_cost_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    radar_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    score_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    max_similarity_to_features: Mapped[float | None] = mapped_column(Float)
    nearest_feature_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("project_features.id", ondelete="SET NULL")
    )
    max_similarity_to_past: Mapped[float | None] = mapped_column(Float)
    nearest_past_finding_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="SET NULL")
    )

    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    why_relevant: Mapped[str | None] = mapped_column(Text)
    what_we_have: Mapped[str | None] = mapped_column(Text)
    what_it_offers: Mapped[str | None] = mapped_column(Text)
    advantages: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    disadvantages: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    integration_complexity: Mapped[str | None] = mapped_column(String(20))
    recommend_deep_analysis: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    finding: Mapped[Finding] = relationship(
        back_populates="matches", foreign_keys=[finding_id]
    )
    project = relationship("Project", lazy="joined")

    __table_args__ = (
        UniqueConstraint("finding_id", "project_id", name="uq_match_finding_project"),
        Index("ix_match_project_score", "project_id", "radar_score"),
        Index("ix_match_score", "radar_score"),
    )


__all__ = [
    "Finding",
    "FindingKey",
    "FindingSource",
    "FindingProjectMatch",
    "FindingKind",
    "FindingStatus",
    "KeyType",
]
