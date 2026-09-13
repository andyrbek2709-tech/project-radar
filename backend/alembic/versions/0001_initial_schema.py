"""Начальная схема Project Radar

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIM = settings.EMBEDDING_DIM
UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TEXT_ARRAY = postgresql.ARRAY(sa.Text())


def _hnsw(table: str, column: str = "embedding") -> None:
    """HNSW с m=16, ef_construction=64 — разумный дефолт для < 100k строк.

    Размерность обеих кандидатных моделей (1024 и 1536) меньше предела
    индексации в 2000, поэтому halfvec не нужен.
    """
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{table}_{column}_hnsw "
        f"ON {table} USING hnsw ({column} vector_cosine_ops) "
        f"WITH (m = 16, ef_construction = 64)"
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------ projects
    op.create_table(
        "projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("business_purpose", sa.Text()),
        sa.Column("existing_architecture", sa.Text()),
        sa.Column("current_stack", JSONB, nullable=False, server_default="{}"),
        sa.Column("integrations", TEXT_ARRAY, server_default="{}"),
        sa.Column("current_problems", TEXT_ARRAY, server_default="{}"),
        sa.Column("planned_features", TEXT_ARRAY, server_default="{}"),
        sa.Column("technology_interests", TEXT_ARRAY, server_default="{}"),
        sa.Column("search_keywords", TEXT_ARRAY, server_default="{}"),
        sa.Column("negative_keywords", TEXT_ARRAY, server_default="{}"),
        sa.Column("things_not_needed", TEXT_ARRAY, server_default="{}"),
        sa.Column("priority_areas", TEXT_ARRAY, server_default="{}"),
        sa.Column("github_repository", sa.String(255)),
        sa.Column("profile_source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("profile_locked_fields", TEXT_ARRAY, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("profile_text", sa.Text()),
        sa.Column("embedding", Vector(DIM)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _hnsw("projects")

    op.create_table(
        "project_features",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("category", sa.String(64)),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("evidence", JSONB, server_default="{}"),
        sa.Column("embedding", Vector(DIM)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_project_features_project_id", "project_features", ["project_id"])
    _hnsw("project_features")

    op.create_table(
        "project_repo_audits",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repo_full_name", sa.String(255), nullable=False),
        sa.Column("commit_sha", sa.String(64)),
        sa.Column("files_collected", JSONB, server_default="{}"),
        sa.Column("detected_stack", JSONB, server_default="{}"),
        sa.Column("detected_features", JSONB, server_default="[]"),
        sa.Column("llm_model", sa.String(120)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_project_repo_audits_project_id", "project_repo_audits", ["project_id"])

    # ------------------------------------------------------------- sources
    op.create_table(
        "sources",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(300)),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("paused_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("kind", "external_id", name="uq_sources_kind_external_id"),
    )
    op.create_index("ix_sources_active", "sources", ["kind", "is_active"])

    op.create_table(
        "source_cursors",
        sa.Column("source_id", UUID, sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_item_id", sa.String(255)),
        sa.Column("etag", sa.String(255)),
        sa.Column("last_modified", sa.String(128)),
        sa.Column("cursor_data", JSONB, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "raw_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("source_id", UUID, sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("urls", TEXT_ARRAY, server_default="{}"),
        sa.Column("github_urls", TEXT_ARRAY, server_default="{}"),
        sa.Column("author", sa.String(255)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processing_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("filter_reason", sa.String(120)),
        sa.Column("error", sa.Text()),
        # Главная защита от повторного анализа одного и того же.
        sa.UniqueConstraint("source_id", "external_id", name="uq_raw_items_source_external"),
    )
    op.create_index("ix_raw_items_status_collected", "raw_items", ["processing_status", "collected_at"])
    op.create_index("ix_raw_items_published_at", "raw_items", ["published_at"])
    op.create_index(
        "ix_raw_items_github_urls", "raw_items", ["github_urls"], postgresql_using="gin"
    )

    op.create_table(
        "collector_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_id", UUID, sa.ForeignKey("sources.id", ondelete="SET NULL")),
        sa.Column("collector", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("items_fetched", sa.Integer(), server_default="0"),
        sa.Column("items_new", sa.Integer(), server_default="0"),
        sa.Column("items_skipped", sa.Integer(), server_default="0"),
        sa.Column("api_requests", sa.Integer(), server_default="0"),
        sa.Column("rate_limit_remaining", sa.Integer()),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_collector_runs_started", "collector_runs", ["collector", "started_at"])

    # -------------------------------------------------------- repositories
    op.create_table(
        "repositories",
        sa.Column("id", UUID, primary_key=True),
        # github_id стабилен при переименовании — ключ дедупликации №1.
        sa.Column("github_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False, unique=True),
        sa.Column("owner", sa.String(120), nullable=False),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("homepage", sa.String(500)),
        sa.Column("language", sa.String(64)),
        sa.Column("topics", TEXT_ARRAY, server_default="{}"),
        sa.Column("license_spdx", sa.String(64)),
        sa.Column("default_branch", sa.String(120)),
        sa.Column("is_fork", sa.Boolean(), server_default="false"),
        sa.Column("parent_full_name", sa.String(255)),
        sa.Column("archived", sa.Boolean(), server_default="false"),
        sa.Column("disabled", sa.Boolean(), server_default="false"),
        sa.Column("created_at_gh", sa.DateTime(timezone=True)),
        sa.Column("updated_at_gh", sa.DateTime(timezone=True)),
        sa.Column("pushed_at_gh", sa.DateTime(timezone=True)),
        sa.Column("latest_release_tag", sa.String(120)),
        sa.Column("latest_release_at", sa.DateTime(timezone=True)),
        sa.Column("readme_text", sa.Text()),
        sa.Column("readme_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("readme_etag", sa.String(255)),
        sa.Column("contributors_count", sa.Integer()),
        sa.Column("commits_last_week", sa.Integer()),
        sa.Column("stars", sa.Integer(), server_default="0"),
        sa.Column("forks", sa.Integer(), server_default="0"),
        sa.Column("watchers", sa.Integer(), server_default="0"),
        sa.Column("open_issues", sa.Integer(), server_default="0"),
        sa.Column("etag", sa.String(255)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_repositories_topics", "repositories", ["topics"], postgresql_using="gin")
    op.create_index("ix_repositories_pushed_at", "repositories", ["pushed_at_gh"])
    op.create_index("ix_repositories_stars", "repositories", ["stars"])

    # История метрик — без неё рост не детектируется:
    # /stargazers закрыт с 30.06.2026, trending API не существует.
    op.create_table(
        "repository_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("repository_id", UUID, sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("captured_on", sa.Date(), nullable=False),
        sa.Column("stars", sa.Integer(), server_default="0"),
        sa.Column("forks", sa.Integer(), server_default="0"),
        sa.Column("watchers", sa.Integer(), server_default="0"),
        sa.Column("open_issues", sa.Integer(), server_default="0"),
        sa.Column("contributors_count", sa.Integer()),
        sa.Column("commits_last_week", sa.Integer()),
        sa.Column("latest_release_tag", sa.String(120)),
        sa.Column("license_spdx", sa.String(64)),
        sa.Column("pushed_at_gh", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("repository_id", "captured_on", name="uq_repo_snapshot_day"),
    )
    op.create_index("ix_repo_snapshots_repo_date", "repository_snapshots", ["repository_id", "captured_on"])

    # ------------------------------------------------------------ findings
    op.create_table(
        "findings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="repository"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("url", sa.String(1000)),
        sa.Column("normalized_url", sa.String(1000)),
        sa.Column("repository_id", UUID, sa.ForeignKey("repositories.id", ondelete="SET NULL")),
        sa.Column("summary", sa.Text()),
        sa.Column("content_text", sa.Text()),
        sa.Column("embedding", Vector(DIM)),
        sa.Column("found_via", TEXT_ARRAY, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("pipeline_stage", sa.String(40)),
        sa.Column("is_noise", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_findings_status_seen", "findings", ["status", "first_seen_at"])
    # Один репозиторий не может породить две находки.
    op.create_index(
        "uq_findings_repository_id",
        "findings",
        ["repository_id"],
        unique=True,
        postgresql_where=sa.text("repository_id IS NOT NULL"),
    )
    _hnsw("findings")

    op.create_table(
        "finding_keys",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("finding_id", UUID, sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_type", sa.String(32), nullable=False),
        sa.Column("key_value", sa.String(1000), nullable=False),
        # Дубль ловится на вставке, а не постфактум.
        sa.UniqueConstraint("key_type", "key_value", name="uq_finding_keys_type_value"),
    )
    op.create_index("ix_finding_keys_finding_id", "finding_keys", ["finding_id"])

    op.create_table(
        "finding_sources",
        sa.Column("finding_id", UUID, sa.ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("raw_item_id", UUID, sa.ForeignKey("raw_items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", UUID, sa.ForeignKey("sources.id", ondelete="SET NULL")),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dedup_method", sa.String(32)),
        sa.Column("dedup_score", sa.Float()),
    )
    op.create_index("ix_finding_sources_finding", "finding_sources", ["finding_id"])

    # Ядро полезности: оценка всегда относительно конкретного проекта.
    op.create_table(
        "finding_project_matches",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("finding_id", UUID, sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relevance_score", sa.Float(), server_default="0"),
        sa.Column("novelty_score", sa.Float(), server_default="0"),
        sa.Column("project_fit_score", sa.Float(), server_default="0"),
        sa.Column("improvement_score", sa.Float(), server_default="0"),
        sa.Column("maturity_score", sa.Float(), server_default="0"),
        sa.Column("activity_score", sa.Float(), server_default="0"),
        sa.Column("implementation_cost_score", sa.Float(), server_default="0"),
        sa.Column("risk_score", sa.Float(), server_default="0"),
        sa.Column("confidence_score", sa.Float(), server_default="0"),
        sa.Column("radar_score", sa.Float(), server_default="0"),
        sa.Column("score_breakdown", JSONB, server_default="{}"),
        sa.Column("max_similarity_to_features", sa.Float()),
        sa.Column("nearest_feature_id", UUID, sa.ForeignKey("project_features.id", ondelete="SET NULL")),
        sa.Column("max_similarity_to_past", sa.Float()),
        sa.Column("nearest_past_finding_id", UUID, sa.ForeignKey("findings.id", ondelete="SET NULL")),
        sa.Column("categories", TEXT_ARRAY, server_default="{}"),
        sa.Column("why_relevant", sa.Text()),
        sa.Column("what_we_have", sa.Text()),
        sa.Column("what_it_offers", sa.Text()),
        sa.Column("advantages", TEXT_ARRAY, server_default="{}"),
        sa.Column("disadvantages", TEXT_ARRAY, server_default="{}"),
        sa.Column("integration_complexity", sa.String(20)),
        sa.Column("recommend_deep_analysis", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("finding_id", "project_id", name="uq_match_finding_project"),
    )
    op.create_index("ix_match_project_score", "finding_project_matches", ["project_id", "radar_score"])
    op.create_index("ix_match_score", "finding_project_matches", ["radar_score"])

    # ------------------------------------------------------------ analyses
    op.create_table(
        "finding_analyses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("finding_id", UUID, sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("analysis_type", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False, server_default="v1"),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("result", JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_response", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0"),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # Один и тот же вход не оплачивается дважды. Два ЧАСТИЧНЫХ индекса,
    # а не один UNIQUE: в PostgreSQL NULL != NULL, и обычный UNIQUE с
    # nullable project_id не защитил бы классификацию и repo-аудит,
    # где project_id IS NULL.
    op.create_index(
        "uq_analysis_idempotency_project",
        "finding_analyses",
        ["finding_id", "project_id", "analysis_type", "prompt_version", "input_digest"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "uq_analysis_idempotency_global",
        "finding_analyses",
        ["finding_id", "analysis_type", "prompt_version", "input_digest"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index("ix_analyses_finding_type", "finding_analyses", ["finding_id", "analysis_type"])
    op.create_index("ix_analyses_created", "finding_analyses", ["created_at"])

    # ----------------------------------------------------------- decisions
    op.create_table(
        "decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("finding_id", UUID, sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.String(40)),
        sa.Column("decided_by", sa.String(16), nullable=False, server_default="engine"),
        sa.Column("radar_score_at_decision", sa.Float()),
        sa.Column("score_snapshot", JSONB, server_default="{}"),
        sa.Column("superseded_by", UUID, sa.ForeignKey("decisions.id", ondelete="SET NULL")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Решения без причины не бывает — это инвариант схемы, а не соглашение.
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_decisions_reason_not_empty"),
    )
    op.create_index("ix_decisions_current", "decisions", ["finding_id", "project_id", "is_current"])
    op.create_index("ix_decisions_status_created", "decisions", ["status", "created_at"])

    op.create_table(
        "reassessment_triggers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("finding_id", UUID, sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("condition", JSONB, nullable=False, server_default="{}"),
        sa.Column("baseline", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="armed"),
        sa.Column("fired_at", sa.DateTime(timezone=True)),
        sa.Column("fired_evidence", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_triggers_status_type", "reassessment_triggers", ["status", "trigger_type"])
    op.create_index("ix_triggers_finding", "reassessment_triggers", ["finding_id"])

    op.create_table(
        "review_queue",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("finding_id", UUID, sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("review_at", sa.Date(), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("notes", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_review_queue_due", "review_queue", ["review_at"])
    op.create_index("ix_review_queue_finding", "review_queue", ["finding_id", "project_id"])

    # ------------------------------------------------- reports / usage
    op.create_table(
        "daily_reports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("report_date", sa.Date(), nullable=False, unique=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telegram_messages_processed", sa.Integer(), server_default="0"),
        sa.Column("github_candidates", sa.Integer(), server_default="0"),
        sa.Column("after_cheap_filter", sa.Integer(), server_default="0"),
        sa.Column("after_ai_filter", sa.Integer(), server_default="0"),
        sa.Column("deep_analyses_count", sa.Integer(), server_default="0"),
        sa.Column("critical_count", sa.Integer(), server_default="0"),
        sa.Column("recommended_count", sa.Integer(), server_default="0"),
        sa.Column("review_later_count", sa.Integer(), server_default="0"),
        sa.Column("rejected_count", sa.Integer(), server_default="0"),
        sa.Column("duplicates_count", sa.Integer(), server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), server_default="0"),
        sa.Column("body_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", JSONB, server_default="{}"),
        sa.Column("is_empty", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("delivered_to", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_daily_reports_date", "daily_reports", ["report_date"])

    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120)),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("finding_id", UUID, sa.ForeignKey("findings.id", ondelete="SET NULL")),
        sa.Column("meta", JSONB, server_default="{}"),
    )
    op.create_index("ix_usage_occurred", "ai_usage_events", ["occurred_at"])
    op.create_index("ix_usage_provider_occurred", "ai_usage_events", ["provider", "occurred_at"])

    # Рантайм-конфиг. Секреты сюда не кладутся никогда — только env.
    op.create_table(
        "settings",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in (
        "settings",
        "ai_usage_events",
        "daily_reports",
        "review_queue",
        "reassessment_triggers",
        "decisions",
        "finding_analyses",
        "finding_project_matches",
        "finding_sources",
        "finding_keys",
        "findings",
        "repository_snapshots",
        "repositories",
        "collector_runs",
        "raw_items",
        "source_cursors",
        "sources",
        "project_repo_audits",
        "project_features",
        "projects",
    ):
        op.drop_table(table)
    # Расширение оставляем: им может пользоваться что-то ещё в этой БД.
