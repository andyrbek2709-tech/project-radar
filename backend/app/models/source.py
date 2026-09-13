from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class SourceKind:
    TELEGRAM = "telegram"
    GITHUB_SEARCH = "github_search"
    GITHUB_WATCH = "github_watch"
    RSS = "rss"
    HACKERNEWS = "hackernews"
    REDDIT = "reddit"


class Source(Base, TimestampMixin):
    """Реестр источников. Новый тип добавляется записью, а не миграцией."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    cursor: Mapped["SourceCursor | None"] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("kind", "external_id", name="uq_sources_kind_external_id"),
        Index("ix_sources_active", "kind", "is_active"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Source {self.kind}:{self.external_id}>"


class SourceCursor(Base):
    """Где остановились. Горячая запись — вынесена из sources."""

    __tablename__ = "source_cursors"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_item_id: Mapped[str | None] = mapped_column(String(255))
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    cursor_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    source: Mapped[Source] = relationship(back_populates="cursor")


class ProcessingStatus:
    PENDING = "pending"
    FILTERED_OUT = "filtered_out"
    PROCESSED = "processed"
    ERROR = "error"


class RawItem(Base):
    """Сырьё из источника. Неизменяемое."""

    __tablename__ = "raw_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_text: Mapped[str | None] = mapped_column(Text)
    urls: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    github_urls: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    author: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProcessingStatus.PENDING, server_default="pending"
    )
    filter_reason: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Главная защита от повторного анализа одного и того же.
        UniqueConstraint("source_id", "external_id", name="uq_raw_items_source_external"),
        Index("ix_raw_items_status_collected", "processing_status", "collected_at"),
        Index("ix_raw_items_published_at", "published_at"),
        Index("ix_raw_items_github_urls", "github_urls", postgresql_using="gin"),
    )


class CollectorRun(Base):
    """Лог прогонов коллекторов — видно, что сбор реально работает."""

    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL")
    )
    collector: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    items_fetched: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items_new: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    api_requests: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_collector_runs_started", "collector", "started_at"),)


__all__ = [
    "Source",
    "SourceKind",
    "SourceCursor",
    "RawItem",
    "ProcessingStatus",
    "CollectorRun",
]
