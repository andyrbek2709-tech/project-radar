from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, uuid_pk


class Repository(Base):
    """GitHub-репозиторий как сущность. Текущие метрики здесь, история — в снапшотах."""

    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = uuid_pk()
    # github_id стабилен при переименовании — ключ дедупликации №1.
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    owner: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    homepage: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str | None] = mapped_column(String(64))
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    license_spdx: Mapped[str | None] = mapped_column(String(64))
    default_branch: Mapped[str | None] = mapped_column(String(120))

    is_fork: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    parent_full_name: Mapped[str | None] = mapped_column(String(255))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    created_at_gh: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at_gh: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pushed_at_gh: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    latest_release_tag: Mapped[str | None] = mapped_column(String(120))
    latest_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    readme_text: Mapped[str | None] = mapped_column(Text)
    readme_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    readme_etag: Mapped[str | None] = mapped_column(String(255))

    contributors_count: Mapped[int | None] = mapped_column(Integer)
    commits_last_week: Mapped[int | None] = mapped_column(Integer)

    stars: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    forks: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    watchers: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    open_issues: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    etag: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    snapshots: Mapped[list["RepositorySnapshot"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_repositories_topics", "topics", postgresql_using="gin"),
        Index("ix_repositories_pushed_at", "pushed_at_gh"),
        Index("ix_repositories_stars", "stars"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Repository {self.full_name} ★{self.stars}>"


class RepositorySnapshot(Base):
    """История метрик по датам.

    Единственный способ детектировать рост: /stargazers с 30.06.2026 закрыт
    для не-коллабораторов, официального trending API не существует.
    """

    __tablename__ = "repository_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    captured_on: Mapped[date] = mapped_column(Date, nullable=False)

    stars: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    forks: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    watchers: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    open_issues: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    contributors_count: Mapped[int | None] = mapped_column(Integer)
    commits_last_week: Mapped[int | None] = mapped_column(Integer)
    latest_release_tag: Mapped[str | None] = mapped_column(String(120))
    license_spdx: Mapped[str | None] = mapped_column(String(64))
    pushed_at_gh: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Repository] = relationship(back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint("repository_id", "captured_on", name="uq_repo_snapshot_day"),
        Index("ix_repo_snapshots_repo_date", "repository_id", "captured_on"),
    )


__all__ = ["Repository", "RepositorySnapshot"]
