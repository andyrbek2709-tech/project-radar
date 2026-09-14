from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk


class AnalysisType:
    GROQ_CLASSIFICATION = "groq_classification"
    PROJECT_MATCH = "project_match"
    DEEP_ANALYSIS = "deep_analysis"
    MANUAL_CHATGPT = "manual_chatgpt"
    REPO_AUDIT = "repo_audit"


class FindingAnalysis(Base):
    """Результаты LLM. Версионируются по prompt_version + input_digest —
    один и тот же вход не оплачивается дважды."""

    __tablename__ = "finding_analyses"

    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    analysis_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_response: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Идемпотентность — ДВА частичных индекса, а не один UNIQUE.
    # В PostgreSQL NULL != NULL, поэтому обычный UNIQUE с nullable project_id
    # не защитил бы записи классификации и repo-аудита (там project_id IS NULL)
    # и один и тот же вход оплачивался бы повторно.
    #
    # Оба индекса покрывают ТОЛЬКО status='ok'. Идемпотентность здесь про то,
    # чтобы не платить дважды за удавшийся ответ; отказ по rate limit ответом
    # не является. Пока status в условие не входил, провал занимал ключ навсегда:
    # кэш искал строго status='ok' и промахивался, а вставка повторной попытки
    # падала на uq_analysis_idempotency_global. Находка выбывала из обработки
    # насовсем — даже после того, как лимит провайдера отпускал.
    __table_args__ = (
        Index(
            "uq_analysis_idempotency_project",
            "finding_id", "project_id", "analysis_type", "prompt_version", "input_digest",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL AND status = 'ok'"),
        ),
        Index(
            "uq_analysis_idempotency_global",
            "finding_id", "analysis_type", "prompt_version", "input_digest",
            unique=True,
            postgresql_where=text("project_id IS NULL AND status = 'ok'"),
        ),
        Index("ix_analyses_finding_type", "finding_id", "analysis_type"),
        Index("ix_analyses_created", "created_at"),
    )


class DecisionStatus:
    CRITICAL = "CRITICAL"
    RECOMMENDED = "RECOMMENDED"
    REVIEW_LATER = "REVIEW_LATER"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"

    ALL = (CRITICAL, RECOMMENDED, REVIEW_LATER, ARCHIVED, REJECTED)


class ReasonCode:
    DUPLICATES_EXISTING = "duplicates_existing"
    NO_REAL_ADVANTAGE = "no_real_advantage"
    ADDS_INFRASTRUCTURE = "adds_infrastructure"
    PROJECT_INACTIVE = "project_inactive"
    STALE_COMMITS = "stale_commits"
    LICENSE_RISK = "license_risk"
    HIGH_RISK = "high_risk"
    NOT_NEEDED = "not_needed"
    LOW_RELEVANCE = "low_relevance"
    HIGH_VALUE = "high_value"
    WORTH_REVIEW = "worth_review"
    LOW_SCORE = "low_score"


class Decision(Base):
    """Решение. reason обязателен на уровне схемы — решений без причины не бывает."""

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(40))
    decided_by: Mapped[str] = mapped_column(String(16), nullable=False, default="engine")
    radar_score_at_decision: Mapped[float | None] = mapped_column(Float)
    score_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("decisions.id", ondelete="SET NULL")
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_empty"),
        Index("ix_decisions_current", "finding_id", "project_id", "is_current"),
        Index("ix_decisions_status_created", "status", "created_at"),
    )


class TriggerType:
    MAJOR_RELEASE = "major_release"
    STARS_SURGE = "stars_surge"
    ARCHITECTURE_CHANGE = "architecture_change"
    NEW_FEATURE = "new_feature"
    LICENSE_CHANGE = "license_change"
    ACTIVITY_RESUMED = "activity_resumed"
    SCHEDULED = "scheduled"


class ReassessmentTrigger(Base):
    """REJECTED не навсегда: при срабатывании находка возвращается на стол."""

    __tablename__ = "reassessment_triggers"

    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="armed", server_default="armed")
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fired_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_triggers_status_type", "status", "trigger_type"),
        Index("ix_triggers_finding", "finding_id"),
    )


class ReviewQueueItem(Base):
    """REVIEW LATER — вернуться через 7 дней / месяц / позже."""

    __tablename__ = "review_queue"

    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    review_at: Mapped[date] = mapped_column(Date, nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    notes: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_review_queue_due", "review_at"),
        Index("ix_review_queue_finding", "finding_id", "project_id"),
    )


class DailyReport(Base):
    """Ежедневный радар. Окно — предыдущие 24 часа, Asia/Aqtau."""

    __tablename__ = "daily_reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    report_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    telegram_messages_processed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    github_candidates: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    after_cheap_filter: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    after_ai_filter: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deep_analyses_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    critical_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recommended_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    review_later_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duplicates_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, server_default="0")
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    is_empty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    delivered_to: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_daily_reports_date", "report_date"),)


class AiUsageEvent(Base):
    """Каждый внешний вызов оставляет след — иначе стоимость не контролируется."""

    __tablename__ = "ai_usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120))
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0, server_default="0")
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="SET NULL")
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    __table_args__ = (
        Index("ix_usage_occurred", "occurred_at"),
        Index("ix_usage_provider_occurred", "provider", "occurred_at"),
    )


class Setting(Base):
    """Рантайм-конфиг. Секреты здесь не хранятся никогда — только env."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


__all__ = [
    "AnalysisType",
    "FindingAnalysis",
    "Decision",
    "DecisionStatus",
    "ReasonCode",
    "ReassessmentTrigger",
    "TriggerType",
    "ReviewQueueItem",
    "DailyReport",
    "AiUsageEvent",
    "Setting",
]
