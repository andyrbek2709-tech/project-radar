"""Все модели импортируются здесь — Alembic autogenerate видит только то,
что зарегистрировано в Base.metadata на момент импорта."""
from app.models.analysis import (
    AiUsageEvent,
    AnalysisType,
    DailyReport,
    Decision,
    DecisionStatus,
    FindingAnalysis,
    ReasonCode,
    ReassessmentTrigger,
    ReviewQueueItem,
    Setting,
    TriggerType,
)
from app.models.base import Base
from app.models.finding import (
    Finding,
    FindingKey,
    FindingKind,
    FindingProjectMatch,
    FindingSource,
    FindingStatus,
    KeyType,
)
from app.models.project import Project, ProjectFeature, ProjectRepoAudit
from app.models.repository import Repository, RepositorySnapshot
from app.models.source import (
    CollectorRun,
    ProcessingStatus,
    RawItem,
    Source,
    SourceCursor,
    SourceKind,
)

__all__ = [
    "Base",
    "Project",
    "ProjectFeature",
    "ProjectRepoAudit",
    "Source",
    "SourceKind",
    "SourceCursor",
    "RawItem",
    "ProcessingStatus",
    "CollectorRun",
    "Repository",
    "RepositorySnapshot",
    "Finding",
    "FindingKey",
    "FindingSource",
    "FindingProjectMatch",
    "FindingKind",
    "FindingStatus",
    "KeyType",
    "FindingAnalysis",
    "AnalysisType",
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
