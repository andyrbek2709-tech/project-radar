"""Pydantic-схемы HTTP API."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------- projects


class ProjectCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    business_purpose: str | None = None
    existing_architecture: str | None = None
    github_repository: str | None = Field(
        default=None, description="owner/name — запускает автоматическую техревизию"
    )
    current_stack: dict[str, list[str]] = Field(default_factory=dict)
    integrations: list[str] = Field(default_factory=list)
    current_problems: list[str] = Field(default_factory=list)
    planned_features: list[str] = Field(default_factory=list)
    technology_interests: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    things_not_needed: list[str] = Field(default_factory=list)
    priority_areas: list[str] = Field(default_factory=list)
    run_audit: bool = True


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    business_purpose: str | None = None
    existing_architecture: str | None = None
    github_repository: str | None = None
    current_stack: dict[str, list[str]] | None = None
    integrations: list[str] | None = None
    current_problems: list[str] | None = None
    planned_features: list[str] | None = None
    technology_interests: list[str] | None = None
    search_keywords: list[str] | None = None
    negative_keywords: list[str] | None = None
    things_not_needed: list[str] | None = None
    priority_areas: list[str] | None = None
    is_active: bool | None = None


class FeatureOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    category: str | None = None
    source: str


class FeatureCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    category: str | None = None


class ProjectOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    business_purpose: str | None = None
    existing_architecture: str | None = None
    github_repository: str | None = None
    current_stack: dict[str, Any] = Field(default_factory=dict)
    integrations: list[str] = Field(default_factory=list)
    current_problems: list[str] = Field(default_factory=list)
    planned_features: list[str] = Field(default_factory=list)
    technology_interests: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    things_not_needed: list[str] = Field(default_factory=list)
    priority_areas: list[str] = Field(default_factory=list)
    profile_source: str
    profile_locked_fields: list[str] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    features: list[FeatureOut] = Field(default_factory=list)
    findings_count: int = 0
    critical_count: int = 0
    recommended_count: int = 0


# --------------------------------------------------------------- findings


class RepositoryOut(ORMModel):
    full_name: str
    url: str
    description: str | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    license_spdx: str | None = None
    stars: int
    forks: int
    open_issues: int
    contributors_count: int | None = None
    commits_last_week: int | None = None
    latest_release_tag: str | None = None
    latest_release_at: datetime | None = None
    pushed_at_gh: datetime | None = None
    created_at_gh: datetime | None = None
    archived: bool = False


class MatchOut(ORMModel):
    project_id: uuid.UUID
    project_slug: str | None = None
    project_name: str | None = None
    relevance_score: float
    novelty_score: float
    project_fit_score: float
    improvement_score: float
    maturity_score: float
    activity_score: float
    implementation_cost_score: float
    risk_score: float
    confidence_score: float
    radar_score: float
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    max_similarity_to_features: float | None = None
    nearest_feature_name: str | None = None
    categories: list[str] = Field(default_factory=list)
    why_relevant: str | None = None
    what_we_have: str | None = None
    what_it_offers: str | None = None
    advantages: list[str] = Field(default_factory=list)
    disadvantages: list[str] = Field(default_factory=list)
    integration_complexity: str | None = None
    recommend_deep_analysis: bool = False


class DecisionOut(ORMModel):
    status: str
    reason: str
    reason_code: str | None = None
    decided_by: str
    radar_score_at_decision: float | None = None
    created_at: datetime


class FindingOut(ORMModel):
    id: uuid.UUID
    kind: str
    title: str
    url: str | None = None
    summary: str | None = None
    found_via: list[str] = Field(default_factory=list)
    seen_count: int
    status: str
    is_noise: bool
    first_seen_at: datetime
    last_seen_at: datetime
    repository: RepositoryOut | None = None
    stars_delta: int | None = None


class FindingDetail(FindingOut):
    content_text: str | None = None
    matches: list[MatchOut] = Field(default_factory=list)
    decisions: list[DecisionOut] = Field(default_factory=list)
    deep_analysis: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)


class FindingListItem(FindingOut):
    match: MatchOut | None = None
    decision: DecisionOut | None = None


class FindingPage(BaseModel):
    items: list[FindingListItem]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------- actions


class ManualAnalysisIn(BaseModel):
    project_id: uuid.UUID
    analysis: dict[str, Any] = Field(
        description="JSON ответа ChatGPT/Codex по схеме DeepAnalysis"
    )


class DecisionOverride(BaseModel):
    project_id: uuid.UUID | None = None
    status: str = Field(pattern=r"^(CRITICAL|RECOMMENDED|REVIEW_LATER|ARCHIVED|REJECTED)$")
    reason: str = Field(min_length=3, max_length=2000)
    review_in_days: int | None = Field(default=None, ge=1, le=365)


class AnalysisContextOut(BaseModel):
    finding_id: uuid.UUID
    project_id: uuid.UUID
    project_slug: str
    prompt: str
    char_count: int


# ---------------------------------------------------------------- sources


class SourceOut(ORMModel):
    id: uuid.UUID
    kind: str
    external_id: str
    title: str | None = None
    is_active: bool
    last_run_at: datetime | None = None
    paused_until: datetime | None = None
    last_error: str | None = None
    last_item_id: str | None = None
    items_collected: int = 0


class SourceCreate(BaseModel):
    kind: str
    external_id: str
    title: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class SourceToggle(BaseModel):
    is_active: bool


# ---------------------------------------------------------------- reports


class ReportOut(ORMModel):
    id: uuid.UUID
    report_date: date
    window_start: datetime
    window_end: datetime
    telegram_messages_processed: int
    github_candidates: int
    after_cheap_filter: int
    after_ai_filter: int
    deep_analyses_count: int
    critical_count: int
    recommended_count: int
    review_later_count: int
    rejected_count: int
    duplicates_count: int
    estimated_cost_usd: float
    is_empty: bool
    body_markdown: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ReviewQueueOut(BaseModel):
    id: uuid.UUID
    finding_id: uuid.UUID
    finding_title: str
    project_slug: str | None = None
    review_at: date
    priority: str
    notes: str | None = None


# ------------------------------------------------------------------ stats


class DashboardOut(BaseModel):
    findings_total: int
    findings_24h: int
    by_status: dict[str, int]
    by_project: list[dict[str, Any]]
    pending_raw_items: int
    analyzed_pending: int = 0
    review_queue_due: int
    sources: dict[str, int]
    cost: dict[str, Any]
    last_report: dict[str, Any] | None = None
    collectors: list[dict[str, Any]] = Field(default_factory=list)


class SettingOut(BaseModel):
    key: str
    value: Any
    updated_at: datetime


class SettingIn(BaseModel):
    value: Any


__all__ = [name for name in dir() if name[0].isupper()]
